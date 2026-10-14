"""The resumable upload flow for large files (``/v1/uploads``).

1. ``POST /v1/uploads`` with the file's size and hash; the server plans the parts;
2. ``GET /v1/uploads/{id}`` to learn which parts S3 already holds (a resumed
   upload skips them);
3. ``POST /v1/uploads/{id}/parts`` for presigned URLs, a window at a time, and a
   PUT of each part's bytes with no Beakr credential, keeping its ``ETag``;
4. ``POST /v1/uploads/{id}/complete``, which creates the file and answers with
   its indexing job.

Only one part is held in memory at a time. If the process dies, the upload id
(``BeakrUploadInterrupted.upload_id``) resumes it with ``files.resume_upload``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from beakr_sdk._calls import Call, Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk.errors import (
    APIConnectionError,
    BeakrUploadInterrupted,
    ConflictError,
    RateLimitError,
    ServerError,
)

#: Formerly the size above which uploads went multipart. Every upload is
#: resumable now (a small file is one part); kept so existing imports work.
MULTIPART_THRESHOLD = 64 * 1024 * 1024
#: Part URLs requested per ``/parts`` call. A URL is valid for an hour, so a
#: small window keeps every URL fresh however slow the link is.
PRESIGN_WINDOW = 8


def _resumable(exc: BaseException) -> bool:
    if isinstance(exc, (APIConnectionError, ServerError, RateLimitError, OSError)):
        return True
    return isinstance(exc, ConflictError) and exc.code == "upload_incomplete"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_part(path: Path, part_number: int, part_size: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek((part_number - 1) * part_size)
        return handle.read(part_size)


def multipart_upload_flow(
    path: Path,
    *,
    project_id: str | None,
    folder_id: str | None = None,
    title: str | None = None,
    artifact_type: str | None = None,
    description: str | None = None,
    mime_type: str,
    ingest: bool = True,
    part_size: int | None = None,
    upload_id: str | None = None,
    relative_path: str | None = None,
) -> Flow:
    """Upload (or, with ``upload_id``, resume) one large file.

    ``project_id`` is needed to start an upload; a resumed one already has its.
    ``relative_path`` (the file's path on the caller's machine, relative to the
    folder it was uploaded from) is sent on ``complete``, which records it.

    Returns ``{"artifact", "upload", "job_id", "status_url"}`` from completion.
    """
    size = path.stat().st_size
    if upload_id is None:
        if not project_id:
            raise ValueError("A resumable upload needs a project_id")
        upload = yield ops.build_uploads_create_upload(
            body=clean_body(
                {
                    "target_project_id": project_id,
                    "parent_folder_id": folder_id,
                    "path": path.name,
                    "content_type": mime_type,
                    "content_length": size,
                    "content_hash": file_sha256(path),
                    "part_size": part_size,
                    "ingest_to_wiki": ingest,
                }
            )
        )
        upload_id = str(upload["id"])
    try:
        state = yield ops.build_uploads_get_upload(upload_id)
        if state["content_length"] != size:
            raise ValueError(
                f"Upload {upload_id} is for a {state['content_length']}-byte file; "
                f"{path} is {size} bytes"
            )
        etags = {
            int(part["part_number"]): part["etag"] for part in state.get("uploaded_parts") or []
        }
        missing = [n for n in range(1, int(state["part_count"]) + 1) if n not in etags]
        for start in range(0, len(missing), PRESIGN_WINDOW):
            window = missing[start : start + PRESIGN_WINDOW]
            signed = yield ops.build_uploads_presign_upload_parts(
                upload_id, body={"part_numbers": window}
            )
            for part in signed["parts"]:
                number = int(part["part_number"])
                headers = yield Call(
                    "PUT",
                    str(part["url"]),
                    content=_read_part(path, number, int(state["part_size"])),
                    external=True,
                    authenticated=False,
                    response="headers",
                )
                etags[number] = headers["etag"]
        completed: dict[str, Any] = yield ops.build_uploads_complete_upload(
            upload_id,
            body=clean_body(
                {
                    "parts": [
                        {"part_number": number, "etag": etag}
                        for number, etag in sorted(etags.items())
                    ],
                    "title": (title or path.name)[:200],
                    "artifact_type": artifact_type,
                    "description": description,
                    "relative_path": relative_path,
                }
            ),
        )
    except Exception as exc:
        if _resumable(exc):
            raise BeakrUploadInterrupted(upload_id, path, exc) from exc
        raise
    return completed
