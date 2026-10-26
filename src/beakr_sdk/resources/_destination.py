"""Where a download is saved: inside a directory, or at the path given."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["names_directory"]


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
