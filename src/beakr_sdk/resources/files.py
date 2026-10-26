"""Uploaded files (artifacts): upload, list, read, download, delete.

Example::

    result = beakr.files.upload("notes/kickoff.pdf", project_id=project_id)
    print(result["artifact"]["id"])
    beakr.files.upload_folder("docs/", project_id=project_id)
    for item in beakr.files.iter(project_id=project_id):
        print(item["title"])
    window = beakr.files.read("deals/acme/memo.pdf", unit="p. 7")
    print(window["text"])
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from beakr_sdk._calls import Call, Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource
from beakr_sdk.resources._destination import names_directory
from beakr_sdk.resources._multipart_upload import multipart_upload_flow
from beakr_sdk.resources._upload import (
    batch_entries,
    batch_relative_paths,
    detect_artifact_type,
    guess_mime_type,
    upload_flow,
    upload_folder_flow,
    upload_many_flow,
    upload_relative_path,
)
from beakr_sdk.resources.jobs import Jobs
from beakr_sdk.resources.knowledge_readiness import (
    wait_for_batch_knowledge_flow,
    wait_for_files_knowledge_flow,
)
from beakr_sdk.resources.uploads import Uploads


def already_uploaded_outcome(batch_status: Mapping[str, Any]) -> dict[str, Any]:
    """The batch's duplicate files, and whether they are its only failures.

    Indexing does not store bytes already in the same place twice: it rejects
    the new file as ``duplicate`` and keeps the existing one
    (``duplicate_of``). That is "already uploaded", not a failure, so an upload
    whose only failed files are duplicates has ``status`` ``"succeeded"``. An
    engine that counts them in ``summary.duplicates`` leaves them out of
    ``summary.failed``; an earlier one counted them there.
    """
    files = batch_status.get("artifacts") or []
    duplicates = [
        {
            "path": item.get("path"),
            "artifact_id": item.get("id"),
            "duplicate_of": dict(item["duplicate_of"]),
        }
        for item in files
        if item.get("error_code") == "duplicate"
        and isinstance(item.get("duplicate_of"), Mapping)
        and item["duplicate_of"].get("artifact_id")
    ]
    summary = batch_status.get("summary") or {}
    failed = int(summary.get("failed") or 0)
    others_failed = failed if "duplicates" in summary else failed - len(duplicates)
    outcome: dict[str, Any] = {"duplicates": duplicates}
    if duplicates and others_failed <= 0:
        outcome["status"] = "succeeded"
    return outcome


#: Page size for listings. Passing ``limit`` is also what makes the list route
#: answer with the ``{items, next_cursor, has_more}`` wrapper instead of a bare list.
_DEFAULT_PAGE_SIZE = 50


class Files(Resource[M]):
    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.uploads: Uploads[M] = Uploads(client)

    def upload(
        self,
        path: str | Path,
        *,
        project_id: str | None = None,
        folder_id: str | None = None,
        title: str | None = None,
        artifact_type: str | None = None,
        description: str | None = None,
        ingest: bool = True,
        relative_path: str | None = None,
    ) -> Any:
        """Upload one file; indexing starts on its own.

        Every file is sent through the resumable upload (``/v1/uploads``; a small
        file is one part) and the answer is ``{"artifact", "upload", "folders",
        "job_id", "status_url", ...}``. If it is interrupted,
        ``BeakrUploadInterrupted.upload_id`` resumes it with ``resume_upload``.
        Without ``project_id`` the file goes to your personal project.
        ``ingest=False`` keeps the file out of Knowledge.

        ``relative_path`` is the path recorded on the file, which Knowledge reads
        name it by and ``read`` accepts; by default a relative ``path`` with
        directories (``deals/acme/memo.pdf``) is recorded as given.
        """
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Not a file: {file_path}")
        return self._drive(
            upload_flow(
                file_path,
                project_id=project_id,
                folder_id=folder_id,
                title=title,
                artifact_type=artifact_type,
                description=description,
                ingest=ingest,
                relative_path=relative_path or upload_relative_path(file_path),
            )
        )

    def resume_upload(
        self,
        upload_id: str,
        path: str | Path,
        *,
        title: str | None = None,
        artifact_type: str | None = None,
        description: str | None = None,
        relative_path: str | None = None,
    ) -> Any:
        """Finish an interrupted resumable upload, sending only the parts S3 lacks.

        ``relative_path`` is recorded as in :meth:`upload`.
        """
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Not a file: {file_path}")
        mime_type = guess_mime_type(file_path)
        return self._drive(
            multipart_upload_flow(
                file_path,
                project_id=None,
                title=title,
                artifact_type=artifact_type or detect_artifact_type(mime_type, file_path.name),
                description=description,
                mime_type=mime_type,
                upload_id=upload_id,
                relative_path=relative_path or upload_relative_path(file_path),
            )
        )

    def upload_many(
        self,
        paths: str | Path | Sequence[str | Path],
        *,
        project_id: str | None = None,
        folder_id: str | None = None,
        ingest: bool = True,
        include_hidden: bool = False,
        wait: bool | Literal["index", "knowledge"] = False,
        timeout: float = 600.0,
        poll: float = 5.0,
    ) -> Any:
        """Upload several files, or whole directories, as one batch (``/v1/bulk``).

        ``paths`` is a file, a directory, or a list of either. A file lands under
        its own name; a directory's subdirectories become folders (under
        ``folder_id`` when given). Up to 1000 files per call. Returns the batch's
        completion: ``{batch_id, artifacts, folders, failed, job_id, status_url}``.
        Without ``project_id`` the files go to your personal project.

        Indexing runs as one job. ``wait=True`` (or ``"index"``) waits for it and
        adds its final state as ``job``, and the upload's outcome as ``status``.
        A file whose bytes are already in the same place is not stored twice: it
        is listed in ``duplicates`` (``path``, ``artifact_id``, and
        ``duplicate_of``, the file that was kept), and when every failed file is
        such a duplicate ``status`` is ``"succeeded"`` (the job succeeds too on
        an engine that counts them in ``summary.duplicates``; an earlier one
        reports it ``failed``). ``wait="knowledge"`` then also waits
        until none of the batch's files is queued or running for Knowledge
        (polling every ``poll`` seconds, growing) and adds the final
        :meth:`batch_status` as ``batch``: each file's ``knowledge_status`` and
        the batch's ``knowledge`` counts, including ``failed``; it also waits for
        each kept copy's Knowledge and adds it to its duplicate as
        ``kept_knowledge_status``. Both waits share
        ``timeout`` seconds (``JobTimeout``, then ``KnowledgeTimeout``). Without
        a wait, pass the result to ``jobs.wait`` later, or read per-file progress
        with :meth:`batch_status`.
        """
        if wait not in (False, True, "index", "knowledge"):
            raise ValueError("wait must be False, True, 'index' or 'knowledge'")
        if wait == "knowledge" and not ingest:
            raise ValueError("wait='knowledge' needs ingest=True; these stay out of Knowledge")
        entries = batch_entries(paths, include_hidden=include_hidden)
        relative_paths = batch_relative_paths(paths, include_hidden=include_hidden)

        def flow() -> Flow:
            completed = yield from upload_many_flow(
                entries,
                project_id=project_id,
                folder_id=folder_id,
                ingest=ingest,
                relative_paths=relative_paths,
            )
            if not wait:
                return completed
            deadline = time.monotonic() + timeout
            job = yield from Jobs(self._client).wait_flow(completed, timeout=timeout)
            result: dict[str, Any] = {**completed, "job": job, "status": job.get("status")}
            # A job that succeeded may still have duplicates (``summary.duplicates``):
            # read the batch to list them and to wait for the files that were kept.
            job_result = job.get("result")
            job_summary = job_result.get("summary") if isinstance(job_result, Mapping) else None
            duplicated = (
                isinstance(job_summary, Mapping) and int(job_summary.get("duplicates") or 0) > 0
            )
            if job.get("status") == "failed" or duplicated:
                batch_now = yield ops.build_uploads_get_bulk_upload_status(
                    str(completed["batch_id"])
                )
                result.update(already_uploaded_outcome(batch_now))
            if wait != "knowledge":
                return result
            batch = yield from wait_for_batch_knowledge_flow(
                str(completed["batch_id"]),
                timeout=max(0.0, deadline - time.monotonic()),
                poll=poll,
                max_interval=max(poll, 30.0),
            )
            result["batch"] = batch
            kept = sorted({d["duplicate_of"]["artifact_id"] for d in result.get("duplicates", [])})
            if kept:
                knowledge = yield from wait_for_files_knowledge_flow(
                    kept,
                    timeout=max(0.0, deadline - time.monotonic()),
                    poll=poll,
                    max_interval=max(poll, 30.0),
                )
                for duplicate in result["duplicates"]:
                    duplicate["kept_knowledge_status"] = knowledge.get(
                        duplicate["duplicate_of"]["artifact_id"]
                    )
            return result

        return self._drive(flow())

    @returns(ops.build_uploads_get_bulk_upload_status)
    def batch_status(self, batch_id: str) -> Any:
        """Per-file status of an :meth:`upload_many` batch, with counts.

        Each file has its indexing ``status`` and its ``knowledge_status``; the
        batch has indexing ``summary`` counts and ``knowledge`` counts
        (``queued``, ``running``, ``created``, ``failed``, ``ready``...).
        """
        return self._run(ops.build_uploads_get_bulk_upload_status(batch_id))

    def upload_folder(
        self,
        path: str | Path,
        *,
        project_id: str,
        folder_id: str | None = None,
        mirror_folders: bool = True,
        include_hidden: bool = False,
        ingest: bool = True,
        root_folder: bool = True,
    ) -> Any:
        """Upload a directory tree; subdirectories become folders unless ``mirror_folders``
        is False.

        Either way each file records its path from the directory's parent
        (``docs/guides/a.md`` for ``guides/a.md`` under ``docs/``), which Knowledge
        reads name it by and ``read`` accepts. With ``root_folder`` (the default) the
        folders start with one named after the directory (``docs/``), matching those
        paths; False puts the directory's contents straight into ``folder_id``.
        Folders that already exist under the same name are reused, so uploading two
        trees with shared subfolder names, or the same tree again, does not fail.
        """
        root = Path(path)
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")
        return self._drive(
            upload_folder_flow(
                root,
                project_id=project_id,
                folder_id=folder_id,
                mirror_folders=mirror_folders,
                include_hidden=include_hidden,
                ingest=ingest,
                root_folder=root_folder,
            )
        )

    def _list_call(
        self,
        *,
        project_id: str | None,
        folder_id: str | None,
        search: str | None,
        knowledge_status: str | None,
        limit: int,
        cursor: str | None,
    ) -> Call:
        return ops.build_files_list_artifacts(
            project_id=project_id,
            folder_id=folder_id,
            search=search,
            knowledge_status=knowledge_status,
            limit=limit,
            cursor=cursor,
        )

    def list(
        self,
        *,
        project_id: str | None = None,
        folder_id: str | None = None,
        search: str | None = None,
        knowledge_status: str | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Any:
        """One page: ``{items, next_cursor, has_more}``.

        ``knowledge_status="waiting_for_person"`` lists only the files held out of
        Knowledge until a person approves them (:meth:`approve_knowledge`).
        """
        return self._run(
            self._list_call(
                project_id=project_id,
                folder_id=folder_id,
                search=search,
                knowledge_status=knowledge_status,
                limit=limit,
                cursor=cursor,
            )
        )

    def iter(
        self,
        *,
        project_id: str | None = None,
        folder_id: str | None = None,
        search: str | None = None,
        knowledge_status: str | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> Any:
        """Every matching file, page by page (``knowledge_status`` as in :meth:`list`)."""
        call = self._list_call(
            project_id=project_id,
            folder_id=folder_id,
            search=search,
            knowledge_status=knowledge_status,
            limit=page_size,
            cursor=None,
        )
        return self._iter(call)

    @returns(ops.build_files_get_artifact)
    def get(self, artifact_id: str) -> Any:
        return self._run(ops.build_files_get_artifact(artifact_id))

    @returns(ops.build_files_read_file)
    def read(
        self,
        reference: str,
        *,
        unit: str | None = None,
        offset: int = 0,
        max_chars: int = 20_000,
        project: str | None = None,
    ) -> Any:
        """Read a file's extracted text by any reference a Knowledge read hands out.

        ``reference`` is a file id, a version id, a source record's ``source_ref``
        (``beakr-source:v1:...``, from any Knowledge read or research answer), a
        citation token (``{{artifact:<uuid>-u3}}``) or the file's path as a Sources
        line prints it (``deals/acme/Closing/memo.docx``, or a trailing part that
        names one file). Pass ``unit`` (``p. 7``, ``slide 3``, ``sheet 2, B4:D9``,
        ``lines 10-30``), or write it after the reference as ``memo.pdf › p. 7`` (or
        ``memo.pdf#p. 7``), to read the window around that place instead of the
        start; a token's or source_ref's own passage is its unit.

        Returns ``{file, unit, text, start, end, total_chars, has_more, next_offset,
        next_reference}``: without a unit pass ``next_offset`` back as ``offset`` to
        continue, with a paged unit read ``next_reference``. A reference that reaches
        no readable file raises ``NotFoundError``; a path naming several files raises
        ``ConflictError`` (``ambiguous_reference``, its body lists them). Only uploads
        and canvases are files: a Knowledge page's ``source_ref`` raises
        ``ValidationError`` (``knowledge_page``; read the page with the knowledge_base
        ``cat`` command), and a connected item's (Slack, email, calendar, Jira)
        ``ValidationError`` (``not_a_file``). ``project`` (an id or name) opens only
        a file in that project; one that names no project you can read raises
        ``NotFoundError`` naming the closest ones.
        """
        if unit:
            reference = f"{reference}#{unit}"
        body: dict[str, Any] = {"reference": reference, "offset": offset, "max_chars": max_chars}
        if project:
            body["project"] = project
        return self._run(ops.build_files_read_file(body=body))

    @returns(ops.build_files_get_artifact_content)
    def content(self, artifact_id: str) -> Any:
        """Extracted text: ``{content, original_format, version_id, title}``."""
        return self._run(ops.build_files_get_artifact_content(artifact_id))

    @returns(ops.build_files_download_url)
    def download_link(self, reference: str, *, inline: bool = False) -> Any:
        """A short-lived signed URL for a file's original bytes, by any reference.

        ``reference`` is anything :meth:`read` takes: a source record's
        ``source_ref`` from a Knowledge read, research answer or documents
        search, a file or version id, a citation token or a path. A reference
        that names a version downloads that version; any other, the current one.
        Returns ``{url, expires_in, filename, mime_type, file}``. A Knowledge
        page's or connected item's reference raises ``ValidationError``
        (``knowledge_page``, ``not_a_file``); a Beakr document with no stored
        original ``ConflictError`` (``no_original_file``); a file you cannot read
        ``NotFoundError``.
        """
        return self._run(
            ops.build_files_download_url(body={"reference": reference, "inline": inline})
        )

    def download_reference(self, reference: str, destination: str | Path) -> Any:
        """Save the original file a reference names (a ``source_ref``, id or path).

        ``destination`` may be a directory, which keeps the file's name: an
        existing one, or a path ending in ``/`` that is created. Returns the path.
        """
        target = Path(destination)
        into_directory = names_directory(destination)

        def flow() -> Flow:
            signed = yield ops.build_files_download_url(body={"reference": reference})
            data = yield Call(
                "GET", signed["url"], external=True, authenticated=False, response="bytes"
            )
            path = target
            if into_directory:
                path = target / Path(str(signed.get("filename") or reference)).name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return path

        return self._drive(flow())

    def download_url(self, artifact_id: str) -> Any:
        """A short-lived presigned URL for the current version's original bytes.

        :meth:`download_link` does this in one request, from any reference.
        """
        return self._drive(self._download_url_flow(artifact_id))

    def _download_url_flow(self, artifact_id: str) -> Flow:
        artifact = yield ops.build_files_get_artifact(artifact_id)
        version_id = artifact.get("current_version_id")
        if not version_id:
            raise FileNotFoundError(f"File {artifact_id} has no uploaded version to download.")
        signed = yield ops.build_uploads_get_download_url(version_id=version_id)
        return {"url": signed["url"], "artifact": artifact}

    def download(self, artifact_id: str, destination: str | Path) -> Any:
        """Save the original file. Returns the path.

        ``destination`` may be a directory, which keeps the file's name: an
        existing one, or a path ending in ``/`` that is created.
        """
        target = Path(destination)
        into_directory = names_directory(destination)

        def flow() -> Flow:
            located = yield from self._download_url_flow(artifact_id)
            data = yield Call(
                "GET", located["url"], external=True, authenticated=False, response="bytes"
            )
            path = target
            if into_directory:
                artifact = located["artifact"]
                name = artifact.get("original_filename") or artifact.get("title") or artifact_id
                path = target / Path(str(name)).name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return path

        return self._drive(flow())

    @returns(ops.build_files_delete_artifact)
    def delete(self, artifact_id: str) -> Any:
        return self._run(ops.build_files_delete_artifact(artifact_id))

    def update(
        self,
        artifact_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        artifact_type: str | None = None,
    ) -> Any:
        body = clean_body(
            {"title": title, "description": description, "artifact_type": artifact_type}
        )
        return self._run(ops.build_files_update_artifact(artifact_id, body=body))

    @returns(ops.build_files_create_knowledge_from_artifact)
    def send_to_knowledge(self, artifact_id: str) -> Any:
        """Compile the file into Knowledge (if it was uploaded without).

        Where a person must review agent uploads (``knowledge.agent_review``),
        a key or token's request answers ``queue_result: waiting_for_person``
        and the file waits for :meth:`approve_knowledge`.
        """
        return self._run(ops.build_files_create_knowledge_from_artifact(artifact_id))

    @returns(ops.build_files_approve_artifact_knowledge)
    def approve_knowledge(self, artifact_id: str) -> Any:
        """Compile a file that is ``waiting_for_person`` into Knowledge.

        The only way a held file is compiled. Only a signed-in person or a key
        holding ``proposals:accept`` may call it (``PersonReviewRequiredError``
        otherwise); a file that is not waiting answers ``409 not_waiting``.
        """
        return self._run(ops.build_files_approve_artifact_knowledge(artifact_id))

    def move(self, artifact_id: str, project_id: str, *, space_id: str | None = None) -> Any:
        body = clean_body({"new_home_project_id": project_id, "new_home_space_id": space_id})
        return self._run(ops.build_files_move_artifact(artifact_id, body=body))
