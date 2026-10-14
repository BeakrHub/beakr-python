"""``multipart/form-data`` bodies, built once and sent by either client.

A :class:`~beakr_sdk._calls.Call` carries raw ``content`` bytes, so the sync and
async transports need no multipart support of their own; the body and its
``Content-Type`` (with the boundary) are produced here.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def multipart_body(
    fields: Mapping[str, str],
    files: Mapping[str, tuple[str, bytes, str]],
) -> tuple[bytes, str]:
    """``(body, content_type)`` for ``fields`` and ``files`` (name -> (filename, data, type))."""
    boundary = f"beakr-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{_quote(name)}"'
        parts.append(f"{header}\r\n\r\n".encode() + value.encode() + b"\r\n")
    for name, (filename, data, content_type) in files.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{_quote(name)}"; '
                f'filename="{_quote(filename)}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
