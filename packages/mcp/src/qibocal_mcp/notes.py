"""Append Qibocal notes without losing concurrent report-server comments."""

from pathlib import Path
from tempfile import TemporaryDirectory

from qibocal import Note
from qibocal.auto.notes import NOTES, dump_notes, load_notes

from .locking import locked


def read_notes(directory: Path) -> list[Note]:
    if (directory / NOTES).is_symlink():
        raise ValueError("Notes files must not be symbolic links")
    return load_notes(directory)


def append_note(directory: Path, note: Note) -> list[Note]:
    lock = directory / ".notes.lock"
    if lock.is_symlink():
        raise ValueError("Notes locks must not be symbolic links")
    # Use the same lock as qibocal-report, and read again while holding it.
    with locked(lock):
        notes = [*read_notes(directory), note]
        with TemporaryDirectory(prefix=".notes-", dir=directory) as temporary:
            dump_notes(notes, Path(temporary))
            (Path(temporary) / NOTES).replace(directory / NOTES)
    return notes
