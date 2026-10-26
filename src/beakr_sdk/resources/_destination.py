"""Where a download is saved: inside a directory, or at the path given.

Names that come from the server (a file's name, a skill's slug, a skill file's
path) are never trusted as paths: each is checked to stay inside the directory
the caller chose before anything is written (security review of beakr-python#60).
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from beakr_sdk.errors import UnsafePathError

__all__ = ["contained", "file_name", "names_directory", "safe_component"]


def names_directory(destination: str | Path) -> bool:
    """Whether a download destination names a directory to save into.

    An existing directory does, and so does a path written with a trailing
    separator (``"downloads/"``) that does not exist yet. ``Path`` drops that
    separator, so it is read from the value the caller passed: without this, a
    first download to ``"downloads/"`` wrote a file named ``downloads``.
    """
    if Path(destination).is_dir():
        return True
    if not isinstance(destination, str):
        return False
    separators = tuple(sep for sep in (os.sep, os.altsep) if sep)
    return destination.endswith(separators)


def safe_component(name: object, *, what: str) -> str:
    """``name`` as one path component, or :class:`UnsafePathError`.

    Refused: empty, ``.``, ``..``, a NUL byte, any ``/`` or ``\\``, and a
    Windows drive (``C:``), each of which could name a place other than a
    file directly inside the destination.
    """
    text = str(name)
    if (
        text in {"", ".", ".."}
        or "\0" in text
        or "/" in text
        or "\\" in text
        or PureWindowsPath(text).drive
    ):
        raise UnsafePathError(f"The server sent {what} {text!r}, which is not a safe file name.")
    return text


def file_name(name: object, *, what: str) -> str:
    """The last component of a server-supplied file name, checked by :func:`safe_component`.

    A title such as ``reports/q3.pdf`` saves as ``q3.pdf``; a name whose last
    component is unsafe (``..``, empty) is refused rather than rewritten.
    """
    text = str(name).replace("\\", "/")
    last = PurePosixPath(text).name if text else ""
    return safe_component(last, what=what)


def contained(directory: Path, relative: str | Path, *, what: str) -> Path:
    """``directory / relative``, refused unless it resolves inside ``directory``.

    Resolution follows symlinks already on disk, so a link inside the
    directory that points elsewhere cannot carry a write out of it.
    """
    candidate = directory / relative
    root = directory.resolve()
    resolved = candidate.resolve()
    if resolved == root or root not in resolved.parents:
        raise UnsafePathError(
            f"The server sent {what} {str(relative)!r}, which would be written outside {directory}."
        )
    return candidate
