"""Backend-neutral agent deployment settings and stdio launch construction."""

import argparse
import json
import os
import shlex
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

from .config import ConfigError, add_deployment_arguments, validate_value
from .environment import source_payload


@dataclass(frozen=True)
class DeploymentSettings:
    """Resolved execution-host settings, independent of model selection."""

    platform: str
    output: str = "runs"
    platforms: str | None = None
    partition: str | None = None
    workdir: str | None = None
    slurm_arg: list[str] = field(default_factory=list)
    venv: str | None = None
    platform_repo: list[str] = field(default_factory=list)
    dependency: list[str] = field(default_factory=list)
    ssh: str | None = None
    server_python: str | None = None
    timeout: float = 3600
    report_rendering: str = "server"

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is not None:
                validate_value(f"agent.{item.name}", value)
        if self.platform is None:
            raise ConfigError("platform is required")
        if self.slurm_arg and self.partition is None:
            raise ConfigError("--slurm-arg requires --partition")
        if self.venv is not None and self.server_python is not None:
            raise ConfigError("--venv cannot be combined with --server-python")

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "DeploymentSettings":
        """Discard backend-specific arguments, keeping shared defaults."""
        return cls(**{
            item.name: getattr(args, item.name)
            for item in fields(cls)
            if hasattr(args, item.name)
        })


@dataclass(frozen=True)
class ServerLaunch:
    """An executable argument vector, launch directory and runtime environment."""

    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str | None = None


def launch_for(settings: DeploymentSettings) -> ServerLaunch:
    """Use stdio locally or carry it over SSH to an access-node gateway."""
    python = settings.server_python or ("python3" if settings.ssh else sys.executable)
    command = [
        python,
        "-m", "qibocal_mcp.server",
        "--no-config",
        "--platform", settings.platform,
        "--output", settings.output,
    ]
    if settings.report_rendering != "server":
        command += ["--report-rendering", settings.report_rendering]
    if settings.partition is not None:
        command += ["--partition", settings.partition]
    command += [f"--slurm-arg={arg}" for arg in settings.slurm_arg]
    venv = settings.venv
    if settings.ssh and settings.server_python is None and venv is None:
        venv = "qibocal-mcp"
    if venv is not None or settings.platform_repo or settings.dependency:
        options = {
            "venv": venv,
            "python": python,
            "platform": settings.platform,
            "platforms": settings.platforms,
            "platform_repo": settings.platform_repo,
            "dependency": settings.dependency,
        }
        request = {"options": options}
        if venv is not None:
            request["source"] = source_payload()
        bootstrap = Path(__file__).with_name("environment.py").read_text(encoding="utf-8")
        command = [python, "-c", bootstrap, json.dumps(request), *command[3:]]
    elif settings.platforms is not None:
        command += ["--platforms", settings.platforms]
    if settings.ssh:
        remote = "exec " + shlex.join(command)
        if settings.workdir is not None:
            remote = f"cd {shlex.quote(settings.workdir)} && {remote}"
        return ServerLaunch(
            command="ssh",
            args=["-T", "-o", "BatchMode=yes", "--", settings.ssh, remote],
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


def add_agent_deployment_arguments(parser: argparse.ArgumentParser) -> None:
    """Keep hardware/deployment arguments identical across agent entry points."""
    parser.add_argument("--platform")
    parser.add_argument("--output")
    parser.add_argument("--ssh", help="SSH host/alias for the access node")
    parser.add_argument("--partition", help="New SLURM job for each protocol/update")
    parser.add_argument(
        "--server-python", help="Server Python path, on the SSH host if remote"
    )
    parser.add_argument("--workdir", help="Working directory on the server/compute nodes")
    parser.add_argument("--platforms", help="QIBOLAB_PLATFORMS on the server/compute nodes")
    parser.add_argument("--slurm-arg", action="append", help="Extra srun option")
    add_deployment_arguments(parser)
    parser.add_argument(
        "--timeout", type=float, help="Seconds per operation, including queue wait"
    )
