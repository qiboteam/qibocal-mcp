"""Platform versions, kept in the platform's Git repository for now.

The tools speak of versions, branches and differences; only this module knows
they are Git commits and refs, so that a database can replace it later.

A calibration procedure saves to a branch of its own, extended linearly across
sessions, so that it can be followed and reviewed on Git hosting as a pull
request. A save extends a branch only from the version the session started
from, or saved last: if the branch moved meanwhile, saving would silently
revert those changes, and is refused. Saving to the checked-out branch also
updates its files; saving to any other branch leaves the checkout untouched.
Nothing is ever force-pushed.
"""

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .locking import locked
from .sessions import PlatformState, git

FILES = ("parameters.json", "calibration.json")
# Values larger than this are summarized in differences.
VALUE_LIMIT = 2_000
CHANGES_LIMIT = 200
# Tool descriptions, shared by the runtime, the frontend and the gateway.
DESCRIPTIONS = {
    "list_versions": """List saved platform versions, branches and this session's base version.

Versions reachable from ref (default: the checked-out one), newest
first, with date, author, message and the branches or tags at each.
fetch=true first updates the remote's branches. Load a version with
start_session(source="repository", ref=...).
""",
    "diff_platform": """Compare this session's accepted state with a saved version.

against is "source", the platform files on disk that a new server
would load, or any version or branch. Returns the changed values by
JSON pointer: what saving would record.
""",
    "save_platform": """Save this session's accepted state as a new version on branch.

Call first without confirm: nothing is written, and the differences
from the branch, with a confirm token, are returned. Show them to the
user, and call again with the token only after their explicit approval.
Keep one branch per calibration procedure, extended across sessions,
so that it can be reviewed as a pull request; an existing branch is
only extended from this session's base version. push=true, only on
request, publishes the branch without forcing.
""",
    "checkout_platform": """Check out a branch or version as the platform files on disk.

Only on the user's request: it changes what new servers and the
"source" of diff_platform load, not this session's state; load it
here with start_session(source="repository"). Refused over
uncommitted changes.
""",
}
GITHUB = re.compile(r"github\.com[:/](?P<repository>[^/]+/[^/]+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class Repository:
    root: Path
    prefix: str

    @classmethod
    def of(cls, platform_folder: Path) -> "Repository":
        platform_folder = platform_folder.resolve()
        root = Path(git(platform_folder, "rev-parse", "--show-toplevel")).resolve()
        relative = platform_folder.relative_to(root).as_posix()
        return cls(root, "" if relative == "." else relative + "/")

    def git(self, *arguments: str, **kwargs) -> str:
        return git(self.root, *arguments, **kwargs)

    @property
    def paths(self) -> list[str]:
        return [self.prefix + name for name in FILES]

    def commit(self, ref: str) -> str | None:
        """The commit a ref names, or None if it names none."""
        if ref.startswith("-"):
            raise ValueError("Version references must not start with a dash")
        try:
            return self.git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        except ValueError:
            return None

    def state(self, commit: str) -> PlatformState:
        return PlatformState(*(self.git("show", f"{commit}:{path}") for path in self.paths))

    def checkout(self) -> dict:
        """The checked-out branch and version, and whether the platform files match it."""
        return {
            "branch": self.git("branch", "--show-current") or None,
            "commit": self.commit("HEAD"),
            "clean": not self.git("status", "--porcelain", "--", *self.paths),
        }

    def fetch(self, remote: str) -> None:
        if remote not in self.git("remote").splitlines():
            raise ValueError(f"Unknown configured repository remote: {remote}")
        git_directory = Path(self.git("rev-parse", "--absolute-git-dir"))
        with locked(git_directory / "qibocal-fetch.lock"):
            self.git("fetch", "--quiet", "--prune", "--no-tags", "--", remote)


def history(repository: Repository, ref: str | None, limit: int) -> dict:
    """The versions of the platform reachable from ref, and the known branches."""
    commit = repository.commit(ref or "HEAD")
    if commit is None:
        raise ValueError(f"Unknown version: {ref}")
    fields = "%x1f".join(("%H", "%aI", "%an", "%s", "%D"))
    log = repository.git(
        "log", f"--max-count={max(1, min(limit, 100))}", f"--format={fields}",
        commit, "--", *repository.paths,
    )
    versions = [
        dict(zip(("version", "date", "author", "message", "refs"), line.split("\x1f"), strict=True))
        for line in log.splitlines()
    ]
    branches = []
    for line in repository.git(
        "for-each-ref", "--sort=-committerdate", "--count=50",
        "--format=%(refname)%1f%(objectname)%1f%(committerdate:iso-strict)",
        "refs/heads", "refs/remotes",
    ).splitlines():
        name, version, date = line.split("\x1f")
        if not name.endswith("/HEAD"):
            short = name.removeprefix("refs/heads/").removeprefix("refs/remotes/")
            branches.append({"branch": short, "version": version, "date": date})
    return {"checkout": repository.checkout(), "versions": versions, "branches": branches}


ABSENT = object()


def summarized(value):
    size = len(json.dumps(value))
    return value if size <= VALUE_LIMIT else f"<{size} bytes>"


def differences(old: PlatformState, new: PlatformState) -> dict:
    """The values that differ, by JSON pointer, from old to new."""
    changes = []

    def compare(before, after, path):
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after), key=str):
                token = str(key).replace("~", "~0").replace("/", "~1")
                compare(before.get(key, ABSENT), after.get(key, ABSENT), f"{path}/{token}")
        elif (
            isinstance(before, list) and isinstance(after, list)
            and len(before) == len(after)
        ):
            for index, (first, second) in enumerate(zip(before, after, strict=True)):
                compare(first, second, f"{path}/{index}")
        elif before != after:
            changes.append({
                "path": path,
                "old": None if before is ABSENT else summarized(before),
                "new": None if after is ABSENT else summarized(after),
            })

    compare(old.document(), new.document(), "")
    return {
        "count": len(changes),
        "changes": changes[:CHANGES_LIMIT],
        "truncated": len(changes) > CHANGES_LIMIT,
    }



def parent(repository: Repository, branch: str, base: dict | None) -> tuple[str, bool]:
    """The version a save to branch extends, and whether the branch exists.

    An existing branch is only extended from the session's base version.
    """
    if branch.startswith("-"):
        raise ValueError("Branch names must not start with a dash")
    repository.git("check-ref-format", "--branch", branch)
    tip = repository.commit(f"refs/heads/{branch}")
    since = None if base is None else base.get("commit")
    if tip is not None:
        if since != tip:
            raise ValueError(
                f"Branch {branch} is at {tip[:12]}, but this session started from "
                f"{since[:12] if since else 'unsaved files'}: saving would revert "
                "its other changes. Start a session from the branch "
                "(start_session with source=repository and ref set to it) and "
                "apply the changes there, or save to a new branch."
            )
        return tip, True
    start = since or repository.commit("HEAD")
    if start is None:
        raise ValueError("The platform repository has no commits to start from")
    return start, False


def token(branch: str, start: str, state: PlatformState) -> str:
    """Identifies one proposed save: it changes with the branch or the state."""
    data = json.dumps([branch, start, state.parameters, state.calibration])
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def save(
    repository: Repository, branch: str, start: str, files: dict[str, str], message: str,
) -> str:
    """Commit the platform files to branch, extending start; return the new version."""
    checkout = repository.checkout()
    if checkout["branch"] == branch:
        if not checkout["clean"]:
            raise ValueError(
                f"The checked-out platform files of {branch} have uncommitted changes"
            )
        for path, name in zip(repository.paths, FILES, strict=True):
            (repository.root / path).write_text(files[name], encoding="utf-8")
        repository.git("commit", "--quiet", "--no-verify", "-m", message, "--", *repository.paths)
        return repository.commit("HEAD")
    with tempfile.TemporaryDirectory() as directory:
        # A private index: the checkout and its index stay untouched.
        env = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
        repository.git("read-tree", start, env=env)
        for path, name in zip(repository.paths, FILES, strict=True):
            blob = repository.git("hash-object", "-w", "--stdin", input=files[name])
            repository.git(
                "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env,
            )
        tree = repository.git("write-tree", env=env)
    commit = repository.git("commit-tree", tree, "-p", start, input=message)
    # Atomic: fails if the branch was created or moved meanwhile.
    old = repository.commit(f"refs/heads/{branch}") or "0" * 40
    if old not in (start, "0" * 40):
        raise ValueError(f"Branch {branch} moved while saving")
    repository.git("update-ref", f"refs/heads/{branch}", commit, old)
    return commit


def push(repository: Repository, branch: str, remote: str | None) -> dict:
    """Publish branch, never forcing; link to a pull request on GitHub."""
    if remote is None:
        try:
            remote = repository.git("config", f"branch.{branch}.remote")
        except ValueError:
            remote = "origin"
    if remote not in repository.git("remote").splitlines():
        raise ValueError(f"Unknown configured repository remote: {remote}")
    repository.git(
        "push", "--quiet", "--set-upstream", "--", remote,
        f"refs/heads/{branch}:refs/heads/{branch}",
    )
    published = {"remote": remote, "branch": branch}
    match = GITHUB.search(repository.git("remote", "get-url", remote))
    if match is not None:
        published["pull_request"] = (
            f"https://github.com/{match['repository']}/compare/{branch}?expand=1"
        )
    return published


def switch(repository: Repository, ref: str) -> dict:
    """Check out a branch, or a version detached; never over uncommitted changes."""
    if repository.git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError(
            "The platform repository has uncommitted changes: commit or discard "
            "them before checking out another version"
        )
    if ref.startswith("-"):
        raise ValueError("Version references must not start with a dash")
    local = repository.commit(f"refs/heads/{ref}") is not None
    remote = any(
        line.removeprefix("refs/remotes/").split("/", 1)[-1] == ref
        for line in repository.git(
            "for-each-ref", "--format=%(refname)", "refs/remotes",
        ).splitlines()
    )
    if local or remote:
        # A remote branch gets a local branch tracking it.
        repository.git("switch", "--quiet", ref)
    elif repository.commit(ref) is not None:
        repository.git("switch", "--quiet", "--detach", ref)
    else:
        raise ValueError(f"Unknown version: {ref}")
    return repository.checkout()
