"""Resumable multipart uploads, one call per route: ``client.files.uploads``.

``files.upload`` drives these for any file over the multipart threshold, and
``files.resume_upload`` picks an interrupted one back up; use this resource to
run the steps yourself (another transport, a browser hand-off, custom retries).
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource


class Uploads(Resource[M]):
    def create(
        self,
        *,
        project_id: str,
        path: str,
        content_length: int,
        content_type: str = "application/octet-stream",
        folder_id: str | None = None,
        content_hash: str | None = None,
        part_size: int | None = None,
        ingest: bool = True,
    ) -> Any:
        """Start an upload; the answer gives ``part_size`` and ``part_count``."""
        body = {
            "target_project_id": project_id,
            "parent_folder_id": folder_id,
            "path": path,
            "content_type": content_type,
            "content_length": content_length,
            "content_hash": content_hash,
            "part_size": part_size,
            "ingest_to_wiki": ingest,
        }
        return self._run(ops.build_uploads_create_upload(body=clean_body(body)))

    @returns(ops.build_uploads_get_upload)
    def get(self, upload_id: str) -> Any:
        """The upload's state; ``uploaded_parts`` lists the parts S3 holds while it is open."""
        return self._run(ops.build_uploads_get_upload(upload_id))

    @returns(ops.build_uploads_presign_upload_parts)
    def presign_parts(self, upload_id: str, part_numbers: list[int]) -> Any:
        """Upload URLs for these parts (at most 100 per call)."""
        return self._run(
            ops.build_uploads_presign_upload_parts(
                upload_id, body={"part_numbers": list(part_numbers)}
            )
        )

    def complete(
        self,
        upload_id: str,
        *,
        parts: list[dict[str, Any]] | None = None,
        title: str | None = None,
        artifact_type: str | None = None,
        description: str | None = None,
    ) -> Any:
        """Assemble the parts and create the file. Omit ``parts`` to use every part S3 holds."""
        body = clean_body(
            {
                "parts": parts,
                "title": title,
                "artifact_type": artifact_type,
                "description": description,
            }
        )
        return self._run(ops.build_uploads_complete_upload(upload_id, body=body))

    @returns(ops.build_uploads_abort_upload)
    def abort(self, upload_id: str) -> Any:
        """Discard an unfinished upload's parts. Aborting twice is a no-op."""
        return self._run(ops.build_uploads_abort_upload(upload_id))
