"""Serve the calibration reports written by the agent."""

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from .config import DEFAULT, REPORT_ENVIRONMENT, REPORT_FIELDS, ConfigError, check_setting
from .deployment import ServerLaunch
from .environment import REPORT_READY_MARKER, DeploymentError


@dataclass(frozen=True)
class ReportSettings:
    """Resolved report storage, environment and network settings."""

    output: str = DEFAULT
    ssh: str | None = None
    workdir: str | None = None
    venv: str = REPORT_ENVIRONMENT
    port: int = 8000
    remote_port: int | None = None
    directories: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.remote_port is None:
            object.__setattr__(self, "remote_port", self.port)
        for item in fields(self):
            if item.name == "directories":
                continue
            value = getattr(self, item.name)
            if value is not None:
                check_setting(f"report.{item.name}", REPORT_FIELDS[item.name], value)
        if self.ssh is None and self.remote_port != self.port:
            raise ConfigError("A distinct --remote-port requires --ssh")

    @classmethod
    def from_values(cls, values: Mapping[str, object]) -> "ReportSettings":
        return cls(**{
            item.name: values[item.name]
            for item in fields(cls)
            if item.name in values
        })


def launch_for(settings: ReportSettings) -> ServerLaunch:
    """Build a local launch or an SSH launch with a loopback port forward."""
    bootstrap = Path(__file__).with_name("environment.py").read_text(encoding="utf-8")
    request = {
        "application": "report",
        "options": {"venv": settings.venv},
    }
    if settings.directories:
        request["options"]["directories"] = settings.directories
    if settings.ssh:
        request["options"]["notify_ready"] = settings.remote_port
    python = "python3" if settings.ssh else sys.executable
    command = [
        python, "-c", bootstrap, json.dumps(request),
        settings.output, "--host", "127.0.0.1",
        "--port", str(settings.remote_port),
    ]
    if settings.ssh:
        remote = "exec " + shlex.join(command)
        if settings.workdir is not None and "workdir" not in settings.directories:
            remote = f"cd {shlex.quote(settings.workdir)} && {remote}"
        return ServerLaunch(
            command="ssh",
            args=[
                "-tt", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-L",
                f"127.0.0.1:{settings.port}:127.0.0.1:{settings.remote_port}",
                "--", settings.ssh, remote,
            ],
            env={
                name: os.environ[name]
                for name in ("SSH_AUTH_SOCK",)
                if name in os.environ
            },
        )
    return ServerLaunch(
        command=command[0], args=command[1:],
        env=dict(os.environ),
        cwd=settings.workdir if "workdir" not in settings.directories else None,
    )


def relay_output(stream: TextIO, ready: threading.Event) -> None:
    """Forward remote logs, consuming only the bootstrap's listening signal."""
    for line in stream:
        if line.strip() == REPORT_READY_MARKER:
            ready.set()
        else:
            sys.stderr.write(line)
            sys.stderr.flush()


def run(settings: ReportSettings) -> None:
    """Wait for HTTP readiness, print the registration URL, and own the process."""
    launch = launch_for(settings)
    url = f"http://127.0.0.1:{settings.port}"
    process = None
    relay = None
    ready = threading.Event()
    if settings.ssh is None:
        ready.set()
    try:
        with socket.socket() as listener:
            # Match the HTTP server's reuse policy for connections in TIME_WAIT.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", settings.port))
        process = subprocess.Popen(
            [launch.command, *launch.args], env=launch.env, cwd=launch.cwd,
            stdout=subprocess.PIPE if settings.ssh else sys.stderr,
            start_new_session=True,
            # Keep SSH's remote PTY without putting our terminal into raw mode.
            **({"stdin": subprocess.DEVNULL, "text": True} if settings.ssh else {}),
        )
        if settings.ssh:
            relay = threading.Thread(
                target=relay_output, args=(process.stdout, ready), daemon=True,
            )
            relay.start()
        opener = build_opener(ProxyHandler({}))
        while process.poll() is None:
            if not ready.wait(timeout=0.2):
                continue
            try:
                with opener.open(url + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except HTTPError as error:
                raise DeploymentError(
                    f"Report server health endpoint returned HTTP {error.code}"
                ) from error
            except (URLError, TimeoutError, ConnectionError):
                # Local startup and HTTP readiness can precede a health response.
                time.sleep(0.2)
        if process.poll() is None:
            print(url, flush=True)
        status = process.wait()
        if status != 0:
            raise DeploymentError(f"Report server exited with status {status}")
    except KeyboardInterrupt:
        pass
    except OSError as error:
        raise DeploymentError(f"Could not start report server: {error}") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                # The child may exit between poll() and signaling its group.
                process.wait()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if relay is not None:
            relay.join()
            process.stdout.close()
