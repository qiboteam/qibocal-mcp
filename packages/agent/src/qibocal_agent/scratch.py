"""The client side of scratch protocols: the repository agents write in.

Before a scratch protocol runs, its commit must be known: the repository must
have no uncommitted changes, and its HEAD is what runs. For remote execution,
that commit is pushed to a bare repository next to the server, on a single ref
updated at each push, so that nothing accumulates there besides Git objects.
These are the only Git operations performed; everything else is up to the user
and the agent, directly on the repository.
"""

import os
import shlex
import subprocess
from pathlib import Path

REF = "refs/qibocal/scratch"
# Bytecode caches are neither code nor committed.
IGNORED = ":(exclude,glob)**/__pycache__/**"


class ScratchError(Exception):
    """A scratch commit could not be prepared."""


def git(repository: Path, *arguments: str) -> str:
    environment = {
        **os.environ, "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes"),
    }
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(repository), *arguments],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, env=environment,
    )
    if result.returncode != 0:
        raise ScratchError(result.stderr.strip() or f"git {arguments[0]} failed")
    return result.stdout.strip()


def create(path: Path) -> None:
    """Start a managed scratch repository, as a package with nothing in it."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--quiet")
    (path / "__init__.py").touch()
    (path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")


def head(path: Path) -> str:
    """The commit to run: HEAD, refusing uncommitted changes."""
    path = path.expanduser()
    if not (path / ".git").exists():
        create(path)
    if git(path, "status", "--porcelain", "--untracked-files=all", "--", ".", IGNORED):
        raise ScratchError(
            f"The scratch repository at {path} has uncommitted changes: commit them "
            "first, so that the run is reproducible."
        )
    try:
        return git(path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    except ScratchError as error:
        raise ScratchError(
            f"The scratch repository at {path} has no commits yet: write the protocol "
            "there and commit it first."
        ) from error


def remote_path(remote: str) -> str:
    """A path on the SSH host; ~/ means its home, as SSH commands start there."""
    return remote.removeprefix("~/")


def push(path: Path, ssh: str, remote: str, commit: str) -> None:
    """Make commit available in the bare repository next to the server."""
    target = remote_path(remote)
    # Idempotent: creates the bare repository on first use.
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "--", ssh,
         f"git init --quiet --bare {shlex.quote(target)}"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise ScratchError(f"Could not prepare {remote} on {ssh}: {result.stderr.strip()}")
    git(path.expanduser(), "push", "--quiet", "--force", f"{ssh}:{target}", f"{commit}:{REF}")
