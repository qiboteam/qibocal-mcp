"""Platform checkpoints and read-only repository revision selection."""

import json
import re
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

    def document(self) -> dict:
        """Expose the serialized models with JSON-string mapping keys."""
        return {
            "parameters": json.loads(self.parameters),
            "calibration": json.loads(self.calibration),
        }

    def patched(self, changes: dict) -> "PlatformState":
        """Replace existing values only; validation occurs before application."""
        if not changes:
            raise ValueError("Supply at least one platform change")
        document = self.document()
        paths = [pointer_tokens(path) for path in changes]
        for index, tokens in enumerate(paths):
            if len(tokens) < 2 or tokens[0] not in {"parameters", "calibration"}:
                raise ValueError("Changes must select a value within parameters or calibration")
            for other in paths[:index]:
                if tokens[:len(other)] == other or other[:len(tokens)] == tokens:
                    raise ValueError("Platform change paths must not overlap")
        for (path, value), tokens in zip(changes.items(), paths):
            parent = pointer_value(document, tokens[:-1])
            key = pointer_key(parent, tokens[-1])
            previous = parent[key]
            # Replacing containers could silently add fields ignored by a model.
            if isinstance(previous, (dict, list)) or isinstance(value, (dict, list)):
                raise ValueError(f"Select individual scalar values, not containers: {path}")
            parent[key] = value
        return PlatformState(
            json.dumps(document["parameters"], allow_nan=False),
            json.dumps(document["calibration"], allow_nan=False),
        )


def pointer_tokens(path: str) -> list[str]:
    """Decode an RFC 6901 JSON pointer, rejecting malformed escapes."""
    if path == "":
        return []
    if not path.startswith("/") or re.search(r"~(?![01])", path):
        raise ValueError(f"Invalid JSON pointer: {path}")
    return [token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")]


def pointer_key(container, token: str) -> str | int:
    if isinstance(container, dict) and token in container:
        return token
    if isinstance(container, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
        index = int(token)
        if index < len(container):
            return index
    raise ValueError(f"Unknown platform path component: {token}")


def pointer_value(document, tokens: list[str]):
    for token in tokens:
        document = document[pointer_key(document, token)]
    return document


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
