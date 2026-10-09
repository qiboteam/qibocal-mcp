"""Serve the calibration reports written by the agent."""

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from .config import ConfigError, parse_settings, validate_value
from .deployment import ServerLaunch
from .environment import DeploymentError


@dataclass(frozen=True)
class ReportSettings:
    """Resolved report storage, environment and network settings."""

    output: str = "runs"
    ssh: str | None = None
    workdir: str | None = None
    venv: str = "qibocal-report"
    port: int = 8000
    remote_port: int | None = None

    def __post_init__(self) -> None:
        if self.remote_port is None:
            object.__setattr__(self, "remote_port", self.port)
        for item in fields(self):
            value = getattr(self, item.name)
            if value is not None:
                validate_value(f"report.{item.name}", value)
        if self.ssh is None and self.remote_port != self.port:
            raise ConfigError("A distinct --remote-port requires --ssh")

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "ReportSettings":
        return cls(**{
            item.name: getattr(args, item.name)
            for item in fields(cls)
        })


def launch_for(settings: ReportSettings) -> ServerLaunch:
    """Build a local launch or an SSH launch with a loopback port forward."""
    bootstrap = Path(__file__).with_name("environment.py").read_text(encoding="utf-8")
    request = {
        "application": "report",
        "options": {"venv": settings.venv},
    }
    python = "python3" if settings.ssh else sys.executable
    command = [
        python, "-c", bootstrap, json.dumps(request),
        settings.output, "--host", "127.0.0.1",
        "--port", str(settings.remote_port),
    ]
    if settings.ssh:
        remote = "exec " + shlex.join(command)
        if settings.workdir is not None:
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
        env=dict(os.environ), cwd=settings.workdir,
    )


def run(settings: ReportSettings) -> None:
    """Wait for HTTP readiness, print the registration URL, and own the process."""
    launch = launch_for(settings)
    url = f"http://127.0.0.1:{settings.port}"
    process = None
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", settings.port))
        process = subprocess.Popen(
            [launch.command, *launch.args], env=launch.env, cwd=launch.cwd,
            stdout=sys.stderr, start_new_session=True,
        )
        opener = build_opener(ProxyHandler({}))
        while process.poll() is None:
            try:
                with opener.open(url + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except HTTPError as error:
                raise DeploymentError(
                    f"Report server health endpoint returned HTTP {error.code}"
                ) from error
            except (URLError, TimeoutError, ConnectionError):
                # Installation and SSH forwarding can precede HTTP startup.
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


def main(argv: list[str] | None = None) -> None:
    """Start a managed qibocal-report server for the agent's output directory."""
    parser = argparse.ArgumentParser(
        description=__doc__, argument_default=argparse.SUPPRESS
    )
    parser.add_argument("--output", help="Calibration report directory")
    parser.add_argument("--ssh", help="SSH host/alias containing the reports")
    parser.add_argument("--workdir", help="Working directory on the report host")
    parser.add_argument("--venv", help="Managed environment on the report host")
    parser.add_argument("--port", type=int, help="Local registration URL port")
    parser.add_argument(
        "--remote-port", type=int,
        help="Report-host port when using SSH (defaults to --port)",
    )
    args = parse_settings(parser, "report", argv)
    try:
        run(ReportSettings.from_namespace(args))
    except DeploymentError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
