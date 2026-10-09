"""Platform checkpoints and read-only repository revision selection."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from qibocal.calibration import CalibrationPlatform

SessionSource = Literal["current", "initial", "run", "repository", "snapshot"]


@dataclass(frozen=True)
class PlatformState:
    """Keep both models together without sharing mutable calibration objects."""

    parameters: str
    calibration: str

    @classmethod
    def capture(cls, platform: "CalibrationPlatform") -> "PlatformState":
        return cls(
            platform.parameters.model_dump_json(),
            platform.calibration.model_dump_json(),
        )

    @classmethod
    def load(cls, path: Path) -> "PlatformState":
        return cls(
            (path / "parameters.json").read_text(encoding="utf-8"),
            (path / "calibration.json").read_text(encoding="utf-8"),
        )

    def apply(self, platform: "CalibrationPlatform") -> None:
        """Validate both models before changing either live model."""
        parameters = type(platform.parameters).model_validate_json(self.parameters)
        calibration = type(platform.calibration).model_validate_json(self.calibration)
        platform.parameters = parameters
        platform.calibration = calibration


def git(repository: Path, *arguments: str) -> str:
    """Run only read-only Git operations, surfacing their diagnostics."""
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(repository), *arguments],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"Could not read platform repository: {result.stderr.strip()}")
    return result.stdout.strip()


def repository_state(
    platform_folder: Path, ref: str | None,
) -> tuple[PlatformState, dict]:
    """Read paired files from a commit without checking out or executing code."""
    platform_folder = platform_folder.resolve()
    root = Path(git(platform_folder, "rev-parse", "--show-toplevel")).resolve()
    relative = platform_folder.relative_to(root).as_posix()
    if ref is not None and not ref.strip():
        raise ValueError("Repository ref must not be empty")
    selected_ref = "HEAD" if ref is None else ref
    commit = git(
        root, "rev-parse", "--verify", "--end-of-options", f"{selected_ref}^{{commit}}"
    )
    prefix = "" if relative == "." else relative + "/"
    state = PlatformState(
        git(root, "show", f"{commit}:{prefix}parameters.json"),
        git(root, "show", f"{commit}:{prefix}calibration.json"),
    )
    return state, {
        "repository": str(root), "platform_path": relative,
        "ref": selected_ref, "commit": commit,
    }
