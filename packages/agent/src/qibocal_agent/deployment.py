"""Backend-neutral agent deployment settings and stdio launch construction."""

import json
import os
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from .config import AGENT_FIELDS, DEFAULT, ConfigError, check_setting
from .environment import source_payload


@dataclass(frozen=True)
class DeploymentSettings:
    """Resolved execution-host settings, independent of model selection."""

    platform: str
    output: str = "runs"
    platforms_dir: str | None = None
    partition: str | None = None
    workdir: str | None = None
    slurm_arg: list[str] = field(default_factory=list)
    venv: str | None = None
    platform_repo: list[str] = field(default_factory=list)
    dependency: list[str] = field(default_factory=list)
    ssh: str | None = None
    server_python: str | None = None
    timeout: float = 3600
    scratch: bool = False
    scratch_path: str | None = None
    scratch_remote: str | None = None
    directories: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for item in fields(self):
            if item.name == "directories":
                continue
            value = getattr(self, item.name)
            if value is not None:
                check_setting(item.name, AGENT_FIELDS[item.name], value)
        if self.platform is None:
            raise ConfigError("platform is required")
        if self.slurm_arg and self.partition is None:
            raise ConfigError("--slurm-arg requires --partition")
        if self.venv is not None and self.server_python is not None:
            raise ConfigError("--venv cannot be combined with --server-python")
        if self.scratch and (self.scratch_path is None or (self.ssh and self.scratch_remote is None)):
            raise ConfigError("Scratch protocols need scratch_path, and scratch_remote with ssh")

    @classmethod
    def from_values(cls, values: Mapping[str, object]) -> "DeploymentSettings":
        """Keep the deployment fields of resolved settings, dropping the others."""
        return cls(**{
            item.name: values[item.name]
            for item in fields(cls)
            if item.name in values
        })


@dataclass(frozen=True)
class ServerLaunch:
    """An executable argument vector, launch directory and runtime environment."""

    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str | None = None


def scratch_checkout(remote: str) -> str:
    """The working directory next to a remote bare scratch repository."""
    return remote.removesuffix(".git") if remote.endswith(".git") else remote + ".checkout"


def launch_for(settings: DeploymentSettings) -> ServerLaunch:
    """Use stdio locally or carry it over SSH to an access-node gateway."""
    python = settings.server_python or ("python3" if settings.ssh else sys.executable)
    command = [
        python,
        "-m", "qibocal_mcp.server",
        "--platform", settings.platform,
        "--output", settings.output,
    ]
    if settings.partition is not None:
        command += ["--partition", settings.partition]
    command += [f"--slurm-arg={arg}" for arg in settings.slurm_arg]
    if settings.scratch:
        if settings.ssh:
            # The client pushes to a bare repository there, checked out alongside.
            command += [
                "--scratch", scratch_checkout(settings.scratch_remote),
                "--scratch-git-dir", settings.scratch_remote,
            ]
        else:
            command += ["--scratch", settings.scratch_path]
    venv = settings.venv
    if settings.ssh and settings.server_python is None and venv is None:
        venv = DEFAULT
    if venv is not None or settings.platform_repo or settings.dependency or settings.directories:
        options = {
            "venv": venv,
            "python": python,
            "platform": settings.platform,
            "platforms_dir": settings.platforms_dir,
            "platform_repo": settings.platform_repo,
            "dependency": settings.dependency,
        }
        request = {"options": options}
        if settings.directories:
            options["directories"] = settings.directories
        if venv is not None:
            request["source"] = source_payload()
        bootstrap = Path(__file__).with_name("environment.py").read_text(encoding="utf-8")
        command = [python, "-c", bootstrap, json.dumps(request), *command[3:]]
    elif settings.platforms_dir is not None:
        command += ["--platforms-dir", settings.platforms_dir]
    if settings.ssh:
        remote = "exec " + shlex.join(command)
        if settings.workdir is not None and "workdir" not in settings.directories:
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
        env=dict(os.environ),
        cwd=settings.workdir if "workdir" not in settings.directories else None,
    )
