"""Shared adapters for qibocal MCP servers."""

import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from qibocal.auto.runcard import Runcard
from qibocal.auto.task import Action


def make_output_path(parent_folder: str | Path, platform: str) -> Path:
    """Create and return a timestamped platform output directory."""
    now = datetime.now().astimezone()
    path = (
        Path(parent_folder)
        / platform
        / now.strftime("%Y%m%d")
        / now.strftime("%H:%M:%S")
    )
    path.mkdir(parents=True)
    return path


def make_runcard(
    experiments: list[dict[str, Any]],
    targets: list[Any] | None = None,
    platform: str = "mock",
    backend: str = "qibolab",
    update: bool = True,
) -> Runcard:
    """Build a qibocal runcard from JSON-compatible experiment definitions."""
    actions = []
    for index, experiment in enumerate(experiments, start=1):
        action = dict(experiment)
        action.setdefault("id", f"experiment-{index}")
        action.setdefault("update", update)
        actions.append(Action.cast(action))
    return Runcard(
        actions=actions,
        targets=targets,
        platform=platform,
        backend=backend,
        update=update,
    )


def write_runcard(runcard: Runcard, output_dir: Path) -> Path:
    """Dump a runcard to a standalone YAML file consumable by `qq run`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    runcard_path = output_dir / "runcard.yml"
    with open(runcard_path, "w") as file:
        yaml.safe_dump(asdict(runcard), file)
    return runcard_path


def _qq_executable() -> str:
    """Resolve the `qq` entry point installed alongside the running interpreter.

    Using the `qq` from ``sys.executable``'s directory guarantees the subprocess
    runs with the same Python environment as the MCP server, avoiding a bare
    ``qq`` on ``PATH`` that may point at a different interpreter lacking
    ``qibocal``.
    """
    qq = Path(sys.executable).parent / "qq"
    if not qq.exists():
        raise RuntimeError(
            f"Could not find `qq` next to the running interpreter ({sys.executable})."
        )
    return str(qq)


def run_qq(
    runcard_path: Path,
    output_path: Path,
    update: bool,
    partition: str | None,
    platform_path: Path | None = None,
) -> str:
    """Run ``qq run``, optionally submitting it via Slurm ``srun`` and checking its exit status.

    Arguments:
        - runcard_path: Path to the runcard YAML file.
        - output_path: Directory where qibocal writes its output.
        - update: Whether to apply platform updates after the run.
        - partition: Optional Slurm partition name; when set, the command is
          submitted via ``srun``.
        - platform_path: Optional path to the qibolab platform file; when
          provided, ``QIBOLAB_PLATFORMS`` is set in the subprocess environment.
    """

    qq_command = [
        _qq_executable(),
        "run",
        str(runcard_path),
        "-o",
        str(output_path),
        "-f",
        "--update" if update else "--no-update",
    ]
    if platform_path is not None:
        # command for changing the QIBOLAB_PLATFORMS environment variable in the subprocess
        platform_command = [f"QIBOLAB_PLATFORMS={platform_path.resolve()}", "&&"]
        qq_command = platform_command + qq_command
        # adding the command in the qq_command list to specify the new platform
        qq_command += ["--platform", "new_platform"]

    if partition is None:
        command = qq_command
    else:
        command = [
            "srun",
            "--time=01:00:00",
            f"--partition={partition}",
        ] + qq_command

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"'{' '.join(command)}' failed with exit code {process.returncode}:\n"
            f"{process.stderr}"
        )
    return process.stdout


def update_qq(
    data_path: Path,
    platform_path: Path | None = None,
    skip_qubits: list[Any] | None = None,
) -> str:
    """Run ``qq update`` as a subprocess and check its exit status.

    Arguments:
        - data_path: Qibocal output folder.
        - platform_path: Optional path to the qibolab platform file; when
          provided, ``QIBOLAB_PLATFORMS`` is set in the subprocess environment.
    """
    command = [_qq_executable(), "update", str(data_path.resolve())]

    if skip_qubits:
        command += [arg for q in skip_qubits for arg in ("--skip-qubits", str(q))]

    if platform_path is not None:
        # command for changing the QIBOLAB_PLATFORMS environment variable in the subprocess
        platform_command = [f"QIBOLAB_PLATFORMS={platform_path.resolve()}", "&&"]
        command = platform_command + command

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"'{' '.join(command)}' failed with exit code {process.returncode}:\n"
            f"{process.stderr}"
        )
    return process.stdout


def return_content(output_folder: str | Path) -> dict[str, str]:
    """Return a stable, JSON-compatible MCP response."""
    return {"output_folder": str(Path(output_folder).resolve())}


def read_platform_json(platform_path: Path) -> dict[str, Any]:
    """Read and parse a JSON file from a platform's folder."""
    calibration_path = platform_path / "calibration.json"
    parameters_path = platform_path / "parameters.json"
    if not calibration_path.is_file() or not parameters_path.is_file:
        raise FileNotFoundError("Platform files not found.")
    return {
        "platform_path": str(platform_path.resolve()),
        "calibration": json.loads(calibration_path.read_text(encoding="utf-8")),
        "parameters": json.loads(parameters_path.read_text(encoding="utf-8")),
    }
