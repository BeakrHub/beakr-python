"""The public source record: where a cited source lives and what it says.

One shape for every surface that shows a source -- ``knowledge.pages.sources()``,
``runs.answer()``, ``beakr kb sources``/``kb cat`` and the stdio MCP server --
so a program reads ``filename``, ``provider`` and ``location`` the same way
wherever the source came from::

    {
      "source_ref": "beakr-source:v1:...",   # opaque; reuse it in a write citation
      "citation_key": "artifact:...-u1",     # the {{key}} token in page or answer text
      "name": "Q3 plan.pdf",
      "filename": "Q3 plan.pdf",
      "provider": "google_drive",            # "upload" for a file uploaded to Beakr
      "source_kind": "artifact",
      "stance": "support",                   # page sources: how it bears on the section
      "location": {
        "url": "https://...",                # the original, for integration files
        "display_path": "Team/Plans/Q3 plan.pdf",  # read_file takes it, with " › <unit>"
        "unit_locator": "p. 7",              # where the cited passage sits
        "upload_path": "plans/Q3 plan.pdf",  # the uploader's relative path, when different
        "provider_item_id": "...",
        "file_id": "...",                    # Beakr's copy: `beakr files download <id>`
        "version_id": "...",                 # the version the passage was read from
      },
      "evidence": {"excerpt", "locator", "fidelity", "evidence_kind", "anchor_status"},
      "status": {"observed_at", "availability", "changed_since_cited"},
      "section": {"id", "title", "event_start", "event_end", "date_precision"},
      "excerpts": [{"citation_key", "section_id", "unit_locator", "text"}],  # compact reads
      "cited_passages": 3,                   # compact reads: passages of it the page cites
    }

Empty fields are left out. Backend row fields (internal unit, connector or
version ids, hashes) are never forwarded.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

__all__ = ["UPLOAD_PROVIDER", "download_hint", "public_source", "source_where"]

#: ``provider`` of a source uploaded to Beakr rather than synced from a connector.
UPLOAD_PROVIDER = "upload"


def public_source(source: dict[str, Any], *, citation_key: str | None = None) -> dict[str, Any]:
    """Keep only the public source contract; never forward backend row fields."""
    meta = _object(source, "meta")
    public_location = _object(source, "location")
    public_evidence = _object(source, "evidence")
    public_status = _object(source, "status")
    public_section = _object(source, "section")
    public_retrieval = _object(source, "retrieval")

    def first(*keys: str) -> Any:
        for key in keys:
            value = source.get(key)
            if value not in (None, "", []):
                return value
            value = meta.get(key)
            if value not in (None, "", []):
                return value
        return None

    name = str(first("name", "source_title", "title") or "Untitled source")
    kind = first("source_kind", "unit_kind", "type", "source_type")
    path = public_location.get("display_path") or first("display_path", "path", "local_path")
    filename = first("filename", "file_name")
    if not filename and path:
        filename = PurePosixPath(str(path).rstrip("/")).name
    if not filename and str(kind or "").lower() in {"file", "document", "artifact"}:
        filename = name
    stance = source.get("stance")

    url = public_location.get("url") or first("url", "web_view_url", "full_text_url", "pdf_url")
    file_id = public_location.get("file_id") or _uploaded_file_id(source, meta)
    provider = first("provider", "source_provider")
    if not provider and (first("origin") == "upload" or (file_id and not url)):
        # A file Beakr holds itself: no connector to name, and no original
        # elsewhere -- its bytes are read back by file id.
        provider = UPLOAD_PROVIDER

    item: dict[str, Any] = {
        "source_ref": first("source_ref"),
        "citation_key": citation_key or first("citation_key", "key"),
        "name": name,
        "filename": filename,
        "provider": provider,
        "source_kind": kind,
        "stance": stance if isinstance(stance, str) else None,
    }
    location = {
        "url": url,
        "display_path": path,
        "unit_locator": public_location.get("unit_locator"),
        "upload_path": public_location.get("upload_path"),
        "provider_item_id": public_location.get("provider_item_id")
        or first("provider_file_id", "remote_file_id"),
        "file_id": file_id,
        "version_id": public_location.get("version_id") if file_id else None,
    }
    evidence = {
        "excerpt": public_evidence.get("excerpt") or first("excerpt", "excerpt_text", "quote"),
        "locator": public_evidence.get("locator") or first("locator", "chunk_ref"),
        "fidelity": public_evidence.get("fidelity") or first("fidelity"),
        "evidence_kind": public_evidence.get("evidence_kind") or first("evidence_kind"),
        "anchor_status": public_evidence.get("anchor_status") or first("anchor_status"),
    }
    source_status = {
        "observed_at": public_status.get("observed_at") or first("observed_at"),
        "availability": public_status.get("availability") or first("source_availability"),
        "changed_since_cited": public_status.get("changed_since_cited")
        if "changed_since_cited" in public_status
        else first("source_moved"),
    }
    section = {
        "id": public_section.get("id") or source.get("section_id"),
        "title": public_section.get("title") or first("section_title"),
        "event_start": public_section.get("event_start") or first("event_start"),
        "event_end": public_section.get("event_end") or first("event_end"),
        "date_precision": public_section.get("date_precision") or first("date_precision"),
    }
    for key, value in (
        ("location", location),
        ("evidence", evidence),
        ("status", source_status),
        ("section", section),
    ):
        cleaned = {k: v for k, v in value.items() if v not in (None, "")}
        if cleaned:
            item[key] = cleaned
    # A compact record carries its document's passages as ``excerpts``, each with
    # its token and where it sits (``unit_locator``), and how many it cites.
    # Dropping them left a Sources listing with no excerpt for compact reads.
    excerpts = [
        {
            key: str(excerpt[key])
            for key in ("citation_key", "section_id", "unit_locator", "text")
            if excerpt.get(key) not in (None, "")
        }
        for excerpt in source.get("excerpts") or []
        if isinstance(excerpt, dict) and excerpt.get("text")
    ]
    if excerpts:
        item["excerpts"] = excerpts
    if isinstance(source.get("cited_passages"), int):
        item["cited_passages"] = source["cited_passages"]
    retrieval_tool = public_retrieval.get("tool")
    retrieval_args = public_retrieval.get("arguments")
    if (
        isinstance(retrieval_tool, str)
        and retrieval_tool
        and isinstance(retrieval_args, dict)
        and isinstance(retrieval_args.get("handle"), str)
        and retrieval_args["handle"]
    ):
        item["retrieval"] = {
            "tool": retrieval_tool,
            "arguments": {"handle": retrieval_args["handle"]},
        }
    return {key: value for key, value in item.items() if value not in (None, "")}


def _object(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _uploaded_file_id(source: dict[str, Any], meta: dict[str, Any]) -> str | None:
    """The Files API id of a source stored in Beakr as a file, else ``None``.

    Every document Beakr holds -- uploaded, or synced from a connector -- is a
    file whose bytes ``files.download(id)`` returns. Its ``artifact_id`` is that
    id; the version, unit and chunk ids beside it are internal and never shown.
    """
    file_id = source.get("artifact_id") or meta.get("artifact_id")
    return file_id if isinstance(file_id, str) and file_id else None


def download_hint(record: dict[str, Any]) -> str | None:
    """The command that fetches a source's bytes when it has no original to link to.

    A connector file's original lives with its provider (``location.url``); an
    uploaded file exists only in Beakr, so it is read back by its file id.
    """
    location = record.get("location") or {}
    file_id = location.get("file_id")
    if not file_id or location.get("url"):
        return None
    return f"beakr files download {file_id}"


def source_where(record: dict[str, Any]) -> str:
    """One line saying where a source lives: ``filename · provider · url-or-path``.

    For an uploaded file, the file id and the command that downloads it.
    """
    location = record.get("location") or {}
    label = record.get("filename") or record.get("name")
    identity = [str(value) for value in (label, record.get("provider")) if value]
    if location.get("url"):
        identity.append(str(location["url"]))
    elif location.get("display_path") and location["display_path"] != label:
        # A path that is just the filename again says nothing new.
        identity.append(str(location["display_path"]))
    hint = download_hint(record)
    if hint:
        identity.append(f"file {location['file_id']} (`{hint}`)")
    return " · ".join(identity)
