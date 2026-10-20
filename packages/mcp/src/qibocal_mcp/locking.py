"""Advisory file locking shared with qibocal-report's note writers."""

import contextlib
import os
from pathlib import Path


@contextlib.contextmanager
def locked(path: Path):
    if os.name != "posix":
        raise RuntimeError("File locking requires a POSIX execution host")
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
