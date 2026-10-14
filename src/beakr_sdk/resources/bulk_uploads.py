"""Many files in one batch (``/v1/bulk``): ``client.bulk_uploads``.

``prepare`` returns a presigned URL per file; upload the bytes to each, then
``complete`` records them and starts one indexing job. ``files.upload_many``
does all of it for paths on disk; this is the batch protocol underneath for
programs that manage the transfer themselves.
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource


class BulkUploads(Resource[M]):
    def prepare(
        self,
        project_id: str,
        files: list[dict[str, Any]],
        *,
        parent_folder_id: str | None = None,
    ) -> Any:
        """``files``: ``[{"path", "content_type", "content_length"}, ...]``; ``path`` is
        relative and becomes folders under ``parent_folder_id``."""
        body = clean_body(
            {"target_project_id": project_id, "files": files, "parent_folder_id": parent_folder_id}
        )
        return self._run(ops.build_uploads_prepare_bulk_upload(body=body))

    def complete(
        self,
        batch_id: str,
        files: list[dict[str, Any]],
        *,
        ingest_to_wiki: bool | None = None,
        source: str | None = None,
    ) -> Any:
        """``files``: ``[{"path", "key", "title"}, ...]`` for the files uploaded."""
        body = clean_body(
            {
                "batch_id": batch_id,
                "files": files,
                "ingest_to_wiki": ingest_to_wiki,
                "source": source,
            }
        )
        return self._run(ops.build_uploads_complete_bulk_upload(body=body))

    @returns(ops.build_uploads_get_bulk_upload_status)
    def status(self, batch_id: str) -> Any:
        return self._run(ops.build_uploads_get_bulk_upload_status(batch_id))
