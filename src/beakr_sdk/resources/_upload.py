"""The upload flow: every file goes through the resumable ``/v1/uploads`` route.

``POST /v1/uploads`` plans the parts (one for a file under the minimum part
size), each part is PUT to its presigned URL, and ``complete`` creates the file
in its project (and folder) and starts indexing. See ``_multipart_upload``.
A file with no project goes to the caller's personal project.
"""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from beakr_sdk._calls import Call, Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import collect_pages
from beakr_sdk.errors import ValidationError
from beakr_sdk.resources._multipart_upload import multipart_upload_flow

#: Extensions the web classifies as scientific data (browsers report them as
#: octet-stream). Mirrors ``SCIENTIFIC_EXTENSIONS`` in beakr-web's
#: ``lib/utils/artifact-file-type.ts`` so a CLI upload lands as the same type.
_SCIENTIFIC_EXTENSIONS = frozenset(
    ".h5ad .loom .fasta .fa .fastq .bam .sam .vcf .bed .gff .gtf .bigwig .bw .gct .gctx "
    ".h5 .hdf5 .hdf .sdf .mol .mol2 .smi .pdb .cif .nii .nifty .dcm .edf .nc .nc4 "
    ".parquet .feather .mtx .pzfx .pzm .pzt .prism .dna".split()
)


def guess_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() in {".md", ".markdown"}:
        return "text/markdown"
    return "application/octet-stream"


def detect_artifact_type(mime_type: str, filename: str) -> str:
    """The artifact type the web app would assign (``detectArtifactType``)."""
    suffix = Path(filename).suffix.lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type == "application/pdf":
        return "doc"
    if "spreadsheet" in mime_type or mime_type == "text/csv":
        return "dataset"
    if mime_type == "application/json":
        return "json"
    if mime_type in {"application/x-ipynb+json", "text/x-rmarkdown"} or suffix in {
        ".ipynb",
        ".rmd",
    }:
        return "notebook"
    if suffix in _SCIENTIFIC_EXTENSIONS:
        return "scientific_data"
    return "doc"


def upload_flow(
    path: Path,
    *,
    project_id: str | None,
    folder_id: str | None = None,
    title: str | None = None,
    artifact_type: str | None = None,
    description: str | None = None,
    ingest: bool = True,
    relative_path: str | None = None,
) -> Flow:
    """Upload one file; returns ``{"artifact", "upload", "folders", "job_id", "status_url", ...}``.

    Every size goes through the resumable upload (a small file is one part), so
    an interrupted upload is resumed with ``files.resume_upload``. Without a
    ``project_id`` the file lands in the caller's personal project.
    ``relative_path`` is recorded on the file when it differs from its name (see
    :func:`upload_relative_path`).
    """
    if path.stat().st_size == 0:
        raise ValueError(f"{path} is empty; Beakr does not store empty files")
    mime_type = guess_mime_type(path)
    if not project_id:
        project_id = yield from _personal_project_id()
    return (
        yield from multipart_upload_flow(
            path,
            project_id=project_id,
            folder_id=folder_id,
            title=title,
            artifact_type=artifact_type or detect_artifact_type(mime_type, path.name),
            description=description,
            mime_type=mime_type,
            ingest=ingest,
            relative_path=relative_path if relative_path != path.name else None,
        )
    )


def upload_relative_path(path: Path, root: Path | None = None) -> str | None:
    """The path a file is recorded under: where it sat on the uploader's machine.

    Beakr records it on the file (``relative_path``), names the file by it in
    Knowledge reads (``location.display_path``) and resolves it in ``read_file``,
    so a source cited as ``deals/acme/memo.pdf › p. 7`` is the path the uploader
    knows. It never decides the file's folder.

    - With ``root`` (a folder being uploaded): the file's path relative to the
      folder's parent, so it starts with the folder's own name
      (``docs/guides/a.md`` for ``guides/a.md`` under ``docs/``).
    - Without: the path as given when it is relative and names directories
      (``deals/acme/memo.pdf``); ``None`` for a bare name or an absolute path,
      which say nothing a reader could follow.

    Always POSIX separators, never absolute, never ``..``.
    """
    if root is not None:
        # ``resolve`` only to name ``.`` by the directory it is; the file's own
        # path is taken as listed, so a symlinked file keeps its place in the tree.
        relative = PurePosixPath(root.resolve().name, path.relative_to(root).as_posix())
    else:
        if path.is_absolute():
            return None
        relative = PurePosixPath(path.as_posix())
    parts = [part for part in relative.parts if part not in ("", ".")]
    if len(parts) < 2 or ".." in parts:
        return None
    return "/".join(parts)


def _personal_project_id() -> Flow:
    """The caller's personal project, where a file with no project is filed."""
    projects = yield from collect_pages(ops.build_projects_list_projects())
    for project in projects:
        if project.get("project_type") == "personal":
            return str(project["id"])
    raise ValueError("No personal project found; pass project_id")


def iter_folder_files(root: Path, *, include_hidden: bool = False) -> list[Path]:
    """Files under ``root`` in a stable order, skipping dotfiles and dot-directories."""
    files = []
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root)
        if not include_hidden and any(part.startswith(".") for part in relative.parts):
            continue
        if candidate.is_file():
            files.append(candidate)
    return files


def upload_folder_flow(
    root: Path,
    *,
    project_id: str,
    folder_id: str | None = None,
    mirror_folders: bool = True,
    include_hidden: bool = False,
    ingest: bool = True,
    root_folder: bool = True,
) -> Flow:
    """Upload every file under ``root``; with ``mirror_folders``, recreate subdirectories.

    Each file records its path relative to ``root``'s parent (``relative_path``,
    :func:`upload_relative_path`), whether or not its folders are mirrored. With
    ``root_folder`` (the default) the mirrored tree starts with a folder named after
    ``root``, so the folders match those recorded paths; without it ``root``'s
    contents go straight into ``folder_id``. Existing folders with the same name in
    the same place are reused, so uploading two trees that share subfolder names,
    or the same tree again, fills the folders that are already there.

    Returns ``[{"path", "artifact", "version"}]`` in upload order.
    """
    folder_ids: dict[Path, str | None] = {Path("."): folder_id}
    children: dict[str, dict[str, str]] = {}
    results: list[dict[str, Any]] = []
    for file_path in iter_folder_files(root, include_hidden=include_hidden):
        relative_dir = file_path.parent.relative_to(root)
        if root_folder:
            relative_dir = Path(root.name) / relative_dir
        target_folder = folder_id
        if mirror_folders:
            target_folder = yield from _ensure_folder(
                relative_dir, folder_ids, project_id, children
            )
        uploaded = yield from upload_flow(
            file_path,
            project_id=project_id,
            folder_id=target_folder,
            ingest=ingest,
            relative_path=upload_relative_path(file_path, root),
        )
        results.append({"path": str(file_path), **uploaded})
    return results


def _ensure_folder(
    relative_dir: Path,
    folder_ids: dict[Path, str | None],
    project_id: str,
    children: dict[str, dict[str, str]],
) -> Flow:
    """The folder for ``relative_dir``: an existing one of that name, else a new one."""
    if relative_dir in folder_ids:
        return folder_ids[relative_dir]
    parent_id = yield from _ensure_folder(relative_dir.parent, folder_ids, project_id, children)
    existing = yield from _child_folders(parent_id, project_id, children)
    found = existing.get(relative_dir.name)
    if found is None:
        try:
            created = yield ops.build_folders_create_folder(
                body=clean_body(
                    {
                        "name": relative_dir.name,
                        "project_id": project_id,
                        "parent_folder_id": parent_id,
                    }
                )
            )
            found = str(created["id"])
        except ValidationError:
            # Created by someone else since the listing: use theirs.
            children.pop(parent_id or "", None)
            existing = yield from _child_folders(parent_id, project_id, children)
            found = existing.get(relative_dir.name)
            if found is None:
                raise
        existing[relative_dir.name] = found
    folder_ids[relative_dir] = found
    return found


def _child_folders(
    parent_id: str | None, project_id: str, children: dict[str, dict[str, str]]
) -> Flow:
    """``{name: id}`` of the folders directly under ``parent_id`` (the project root if None)."""
    key = parent_id or ""
    if key not in children:
        call = ops.build_folders_list_folders(
            project_id=project_id,
            parent_folder_id=parent_id,
            root_only=None if parent_id else True,
        )
        items = yield from collect_pages(call)
        children[key] = {str(item["name"]): str(item["id"]) for item in items}
    return children[key]


#: Files one ``/v1/bulk`` batch may carry (the API's ``ingress.bulk_upload.max_files``).
MAX_BATCH_FILES = 1000


def batch_relative_paths(
    paths: Path | str | list[Path | str], *, include_hidden: bool = False
) -> dict[str, str]:
    """``{path in Beakr: relative_path}`` for the :func:`batch_entries` of ``paths``.

    A directory's files record their path from its parent, starting with the
    directory's own name; a file given with directories records that path (see
    :func:`upload_relative_path`). Entries whose recorded path would equal their
    path in Beakr are left out.
    """
    items = [paths] if isinstance(paths, (str, Path)) else list(paths)
    recorded: dict[str, str] = {}
    for item in items:
        path = Path(item)
        if path.is_dir():
            for file_path in iter_folder_files(path, include_hidden=include_hidden):
                name = file_path.relative_to(path).as_posix()
                relative = upload_relative_path(file_path, path)
                if relative and relative != name:
                    recorded[name] = relative
        elif path.is_file():
            relative = upload_relative_path(path)
            if relative and relative != path.name:
                recorded[path.name] = relative
    return recorded


def batch_entries(
    paths: Path | str | list[Path | str], *, include_hidden: bool = False
) -> list[tuple[Path, str]]:
    """``(file, path in Beakr)`` for each file to upload, in a stable order.

    A file is uploaded under its own name; a directory's files keep their
    paths relative to it (its subdirectories become folders). Dotfiles and
    dot-directories inside a directory are skipped unless ``include_hidden``.
    """
    items = [paths] if isinstance(paths, (str, Path)) else list(paths)
    entries: list[tuple[Path, str]] = []
    for item in items:
        path = Path(item)
        if path.is_dir():
            for file_path in iter_folder_files(path, include_hidden=include_hidden):
                entries.append((file_path, file_path.relative_to(path).as_posix()))
        elif path.is_file():
            entries.append((path, path.name))
        else:
            raise FileNotFoundError(f"Not a file or directory: {path}")
    seen: dict[str, Path] = {}
    for file_path, name in entries:
        if name in seen:
            raise ValueError(f"{file_path} and {seen[name]} would both be uploaded as {name}")
        seen[name] = file_path
    return entries


def upload_many_flow(
    entries: list[tuple[Path, str]],
    *,
    project_id: str | None,
    folder_id: str | None = None,
    ingest: bool = True,
    relative_paths: Mapping[str, str] | None = None,
) -> Flow:
    """Upload ``entries`` as one ``/v1/bulk`` batch; returns the batch's completion.

    ``relative_paths`` maps an entry's path in Beakr to the path recorded on its
    file (:func:`batch_relative_paths`); entries it leaves out record none.

    ``prepare`` creates the folders the paths name and a presigned URL per
    file, each file's bytes are PUT there (no Beakr credential), and
    ``complete`` records the files and starts indexing them as one job
    (``job_id``, ``status_url``).
    """
    if not entries:
        raise ValueError("No files to upload")
    if len(entries) > MAX_BATCH_FILES:
        raise ValueError(
            f"{len(entries)} files: one batch takes at most {MAX_BATCH_FILES}; split them"
        )
    if not project_id:
        project_id = yield from _personal_project_id()
    mime_types = {name: guess_mime_type(file_path) for file_path, name in entries}
    prepared = yield ops.build_uploads_prepare_bulk_upload(
        body=clean_body(
            {
                "target_project_id": project_id,
                "parent_folder_id": folder_id,
                "files": [
                    {
                        "path": name,
                        "content_type": mime_types[name],
                        "content_length": file_path.stat().st_size,
                    }
                    for file_path, name in entries
                ],
            }
        )
    )
    targets = {upload["path"]: upload for upload in prepared["uploads"]}
    completed_files = []
    for file_path, name in entries:
        target = targets[name]
        data = file_path.read_bytes()
        yield Call(
            "PUT",
            str(target["url"]),
            content=data,
            headers=dict(target.get("headers") or {}) or {"Content-Type": mime_types[name]},
            external=True,
            authenticated=False,
            response="none",
        )
        completed_files.append(
            clean_body(
                {
                    "path": name,
                    "key": target["key"],
                    "title": file_path.name[:200],
                    "artifact_type": detect_artifact_type(mime_types[name], file_path.name),
                    "content_hash": hashlib.sha256(data).hexdigest(),
                    "relative_path": (relative_paths or {}).get(name),
                }
            )
        )
    return (
        yield ops.build_uploads_complete_bulk_upload(
            body={
                "batch_id": prepared["batch_id"],
                "files": completed_files,
                "ingest_to_wiki": ingest,
            }
        )
    )
