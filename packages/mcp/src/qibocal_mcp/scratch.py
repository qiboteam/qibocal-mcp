"""Scratch protocols: written by agents in a Git repository, run at a commit.

The repository is a plain package: a top-level ``__init__.py``, and modules
defining Qibocal protocols, addressed as ``<module>.<name>``. Every run records
the commit it executed, so that it can be checked out and executed again.

Locally, protocols are imported from the repository itself, which must have no
uncommitted changes: its files are then exactly its HEAD. Remotely, the client
pushes the commit to a bare repository next to the server, and the server
updates a single working directory to it: one directory per repository,
whatever the number of commits run.
"""

import importlib
import importlib.util
import sys
from pathlib import Path

from qibocal.auto.operation import Data, Parameters, Protocol

from .locking import locked
from .sessions import git

PACKAGE = "qibocal_scratch"
# Operation names of scratch runs in reports; Qibocal's names have no dots.
PREFIX = "scratch."
# Bytecode caches are neither code nor committed.
IGNORED = ":(exclude,glob)**/__pycache__/**"


class Scratch:
    """One scratch repository: local, or a bare repository with its checkout."""

    def __init__(self, path: Path, git_dir: Path | None = None):
        self.path = path.expanduser().resolve()
        self.git_dir = None if git_dir is None else git_dir.expanduser().resolve()
        self.loaded: str | None = None

    def git(self, *arguments: str) -> str:
        if self.git_dir is None:
            return git(self.path, *arguments)
        return git(
            self.path, f"--git-dir={self.git_dir}", f"--work-tree={self.path}", *arguments,
        )

    def describe(self) -> str:
        if self.git_dir is None:
            return (
                "Scratch protocols are enabled, from a local repository at "
                f"{self.path}: execute_protocol and get_protocol with "
                "source=\"scratch\" and protocol=\"<module>.<name>\" run its HEAD, "
                "which must have no uncommitted changes."
            )
        return (
            "Scratch protocols are enabled: execute_protocol and get_protocol with "
            "source=\"scratch\" and protocol=\"<module>.<name>\" run the commit "
            "pushed by the client (scratch_commit), from the client's scratch "
            "repository."
        )

    def checkout(self, requested: str | None) -> str:
        """The commit to run, with the working directory at it."""
        if self.git_dir is None:
            if self.git("status", "--porcelain", "--untracked-files=all", "--", ".", IGNORED):
                raise ValueError(
                    f"The scratch repository at {self.path} has uncommitted changes: "
                    "commit them first, so that the run is reproducible."
                )
            head = self.git("rev-parse", "--verify", "HEAD")
            if requested not in (None, head):
                raise ValueError(
                    f"Scratch commit {requested} is not the repository's HEAD {head}: "
                    "check it out first."
                )
            return head
        if requested is None:
            raise ValueError(
                "Scratch protocols run a commit pushed by the client: none was given "
                "(qibocal-agent pushes it as scratch_commit)."
            )
        if requested.startswith("-"):
            raise ValueError("Scratch commits must not start with a dash")
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            commit = self.git("rev-parse", "--verify", "--quiet", f"{requested}^{{commit}}")
        except ValueError as error:
            raise ValueError(
                f"Unknown scratch commit {requested}: it was not pushed to the server"
            ) from error
        # Exactly this commit's files, removing those of the previous one.
        self.git("read-tree", "-u", "--reset", commit)
        return commit

    def load(self, name: str, requested: str | None = None) -> tuple[Protocol, dict]:
        """A scratch protocol at a commit, and the source to record for its runs."""
        module, _, attribute = name.rpartition(".")
        if not module or not attribute:
            raise ValueError("Scratch protocols are named <module>.<name>, e.g. my_rabi.rabi")
        git_dir = self.git_dir or Path(self.git("rev-parse", "--absolute-git-dir"))
        with locked(git_dir / "qibocal-scratch.lock"):
            commit = self.checkout(requested)
            if self.loaded != commit:
                self.unload()
            dont_write_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True
            try:
                if PACKAGE not in sys.modules:
                    spec = importlib.util.spec_from_file_location(
                        PACKAGE, self.path / "__init__.py",
                        submodule_search_locations=[str(self.path)],
                    )
                    if spec is None or not (self.path / "__init__.py").is_file():
                        raise ValueError("The scratch repository needs a top-level __init__.py")
                    package = importlib.util.module_from_spec(spec)
                    sys.modules[PACKAGE] = package
                    spec.loader.exec_module(package)
                self.loaded = commit
                loaded = importlib.import_module(f"{PACKAGE}.{module}")
            except ModuleNotFoundError as error:
                raise ValueError(f"Unknown scratch module: {module}") from error
            finally:
                sys.dont_write_bytecode = dont_write_bytecode
        protocol = getattr(loaded, attribute, None)
        if not isinstance(protocol, Protocol):
            raise ValueError(f"{module} defines no protocol named {attribute}")
        return protocol, {
            "source": "scratch", "protocol": name, "commit": commit,
            "path": Path(loaded.__file__).resolve().relative_to(self.path).as_posix(),
        }

    def unload(self) -> None:
        """Forget the imported commit; objects already loaded stay usable."""
        for module in [name for name in sys.modules if name.split(".")[0] == PACKAGE]:
            del sys.modules[module]
        importlib.invalidate_caches()
        self.loaded = None


class Opaque(Parameters):
    """Any parameters, kept as given."""

    def __init__(self, **values):
        self.__dict__.update(values)


def unavailable(operation: str) -> Protocol:
    """Stands in for a scratch protocol in report histories, until it is loaded."""

    def acquisition(parameters: Opaque) -> Data:
        raise ValueError(f"Scratch protocol {operation} is not loaded")

    return Protocol(acquisition)


class Registry(dict):
    """Built-in protocols; scratch runs in reports get a stand-in."""

    def __missing__(self, operation: str) -> Protocol:
        if operation.startswith(PREFIX):
            return unavailable(operation)
        raise KeyError(operation)
