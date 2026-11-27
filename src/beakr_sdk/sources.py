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
      "evidence": {"excerpt", "excerpt_kind", "context_excerpt", "locator",
                   "source_recorded_at",
                   "fidelity", "evidence_kind", "anchor_status"},  # last three deprecated
      "status": {"observed_at", "availability", "changed_since_cited"},
      "section": {"id", "title", "event_start", "event_end", "date_precision"},
      "excerpts": [{"citation_key", "section_id", "unit_locator", "text",
                    "excerpt_kind"}],      # compact reads
      "cited_passages": 3,                   # compact reads: passages of it the page cites
      "filed_by": {"user_name", "agent", "client", "run", "session", "text"},
      "written_by": {"authors": [{"name", "org_member"}], "by_agent", "text"},  # recorded authors
      "retrieval": {"tool": "granola_get_note", "arguments": {"note_id": "not_..."}},
    }

``retrieval`` is the call that re-reads a connected item. For a meeting note, an
email or a Slack thread it is the provider's read tool and that item's own id:
pass both to ``integrations.call(tool, arguments)`` (or ``call_tool``). For a
connector file it carries a download ``handle`` and nothing else.
:func:`read_call` gives the tool and arguments, or ``None`` for a file. To
re-read every source a Knowledge read cites::

    from beakr_sdk.sources import public_source, read_call

    reply = beakr.knowledge.command("sources", page="Acme")
    for record in (public_source(s) for s in reply["sources"]):
        call = read_call(record)
        if call is not None:  # a connected item: read it from the provider
            tool, arguments = call
            print(beakr.integrations.call(tool, arguments)["content"])
        elif record.get("location", {}).get("file_id"):  # a file: read its text
            print(beakr.files.read(record["source_ref"])["text"])

``evidence.excerpt_kind`` says whose words ``excerpt`` is: ``quote`` for the
source's own words (the only kind to show in quotation marks), ``description``
for Beakr's own wording (a description of a figure, a computed value, or words
not found in the source as written) and ``person`` for a person's attribution
or a record in a system outside Beakr. A compact record's excerpt carries
``excerpt_kind`` only when its text is not the source's own words. An engine
from before the field sends neither, and the record then leaves it out.

``filed_by`` names who wrote an inline ``conversation``, ``agent_note`` or
``user_note`` citation, by name only: never the person's ``user_id``, which the
page sources route keeps in ``meta.filed_by``. ``written_by`` names who wrote a
file that records its authors (``authored_by`` on the upload): ``authors`` (each
``name`` and ``org_member``), ``by_agent`` (an agent wrote it for them) and a
``text`` line ("Jane Doe and Bob Smith").

Empty fields are left out. Backend row fields (internal unit, connector or
version ids, hashes) are never forwarded.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

__all__ = ["UPLOAD_PROVIDER", "download_hint", "public_source", "read_call", "source_where"]

#: ``provider`` of a source uploaded to Beakr rather than synced from a connector.
UPLOAD_PROVIDER = "upload"

#: Whose words a source's ``evidence.excerpt`` is (``SourceEvidence.excerpt_kind``).
EXCERPT_KINDS = frozenset({"quote", "description", "person"})

#: Whose words a compact record's excerpt ``text`` is when they are not the
#: source's own (``SourceExcerpt.excerpt_kind``); a quote carries no kind.
PASSAGE_EXCERPT_KINDS = frozenset({"description", "person"})

#: The fields of ``filed_by`` a public record keeps, as the engine's
#: ``citation_filer.PUBLIC_FILER_FIELDS`` plus its ``text`` line: names, never ids.
PUBLIC_FILER_FIELDS = ("user_name", "agent", "client", "run", "session", "text")

#: The fields of each ``written_by.authors`` entry a public record keeps.
WRITTEN_BY_AUTHOR_FIELDS = ("name", "org_member")


def _display_authors(names: list[str]) -> str | None:
    """``Jane Doe``, ``Jane Doe and Bob Smith``, ``Jane Doe, Bob Smith and Ana Li``.

    The engine's ``api/dto/authorship.display_authors``, so a record built here
    reads the same line the engine's own source records carry.
    """
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _written_by_from_stamp(entries: list[Any]) -> dict[str, Any] | None:
    """A citation's stored ``written_by`` stamp as ``{authors, by_agent, text}``, or None.

    A page sources row (``GET /v1/knowledge/wiki/pages/{page_id}/sources``) carries
    the citation's ``written_by`` as the compiler stored it: one ``{name, user_id?,
    by_agent?}`` entry per author, a list (or, from early dev builds, a single
    entry). The engine turns that stamp into ``SourceWrittenBy`` with
    ``compiler/source_authorship.public_written_by``; this is the same mapping. A
    member is reported as ``org_member`` only, never by id.
    """
    stamped = [
        entry
        for entry in entries
        if isinstance(entry, dict) and " ".join(str(entry.get("name") or "").split())
    ]
    if not stamped:
        return None
    names = [" ".join(str(entry["name"]).split()) for entry in stamped]
    authors = [
        {"name": name, "org_member": bool(entry.get("user_id"))}
        for name, entry in zip(names, stamped)
    ]
    by_agent = any(entry.get("by_agent") is True for entry in stamped)
    line = _display_authors(names)
    return {
        "authors": authors,
        "by_agent": by_agent,
        "text": f"an agent for {line}" if by_agent else line,
    }


def _public_written_by(value: Any) -> dict[str, Any] | None:
    """``written_by`` as ``{authors, by_agent, text}`` (``SourceWrittenBy``), or None.

    ``value`` is either the public ``SourceWrittenBy`` a Knowledge read's source
    record carries, or the stored stamp a page sources row carries (see
    :func:`_written_by_from_stamp`). Each author keeps its ``name`` and
    ``org_member`` only, never a member id.
    """
    if isinstance(value, list):
        return _written_by_from_stamp(value)
    if not isinstance(value, dict):
        return None
    if "authors" not in value and "text" not in value and value.get("name"):
        return _written_by_from_stamp([value])
    authors = [
        {
            field: author[field] if field == "org_member" else str(author[field])
            for field in WRITTEN_BY_AUTHOR_FIELDS
            if author.get(field) not in (None, "")
        }
        for author in value.get("authors") or []
        if isinstance(author, dict) and author.get("name")
    ]
    public: dict[str, Any] = {}
    if authors:
        public["authors"] = authors
    if isinstance(value.get("by_agent"), bool):
        public["by_agent"] = value["by_agent"]
    if value.get("text"):
        public["text"] = str(value["text"])
    return public if authors or public.get("text") else None


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
        "excerpt_kind": _known(
            public_evidence.get("excerpt_kind") or first("excerpt_kind"), EXCERPT_KINDS
        ),
        # The passage around the quote, and when the source system recorded the
        # quoted part. A page sources row carries both beside ``excerpt``.
        "context_excerpt": public_evidence.get("context_excerpt") or first("context_excerpt"),
        "locator": public_evidence.get("locator") or first("locator", "chunk_ref"),
        "source_recorded_at": public_evidence.get("source_recorded_at")
        or first("source_recorded_at"),
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
        _public_excerpt(excerpt)
        for excerpt in source.get("excerpts") or []
        if isinstance(excerpt, dict) and excerpt.get("text")
    ]
    if excerpts:
        item["excerpts"] = excerpts
    if isinstance(source.get("cited_passages"), int):
        item["cited_passages"] = source["cited_passages"]
    retrieval = _public_retrieval(public_retrieval)
    if retrieval:
        item["retrieval"] = retrieval
    # Who wrote an inline citation; a page sources row carries it in ``meta``.
    filed_by = first("filed_by")
    if isinstance(filed_by, dict):
        public_filed = {
            field: str(filed_by[field])
            for field in PUBLIC_FILER_FIELDS
            if filed_by.get(field) not in (None, "")
        }
        if public_filed:
            item["filed_by"] = public_filed
    # Who wrote a file that records its authors; a page sources row carries it in ``meta``.
    written_by = _public_written_by(first("written_by"))
    if written_by:
        item["written_by"] = written_by
    return {key: value for key, value in item.items() if value not in (None, "")}


def _public_excerpt(excerpt: dict[str, Any]) -> dict[str, str]:
    """One passage of a compact record, with whose words it is when not the source's."""
    passage = {
        key: str(excerpt[key])
        for key in ("citation_key", "section_id", "unit_locator", "text")
        if excerpt.get(key) not in (None, "")
    }
    kind = _known(excerpt.get("excerpt_kind"), PASSAGE_EXCERPT_KINDS)
    if kind:
        passage["excerpt_kind"] = kind
    return passage


def _known(value: Any, allowed: frozenset[str]) -> str | None:
    """``value`` when it is one of the documented ``allowed`` kinds, else ``None``."""
    return value if isinstance(value, str) and value in allowed else None


def _public_retrieval(retrieval: dict[str, Any]) -> dict[str, Any] | None:
    """The call that re-reads a connected item, in its public form, or ``None``.

    A connector file's retrieval keeps its download ``handle`` and nothing else.
    A text connector's item (a Granola note, an email or Slack thread) keeps the
    provider read tool and its scalar arguments, as the engine's
    ``native_source_fetch.public_native_call`` forwards them: the item's share
    link carries a different id that the read tool refuses, so dropping the call
    left a caller with no way to re-read the item. The API has already limited
    the tool to a known read; anything nested or empty is still dropped here.
    """
    tool = retrieval.get("tool")
    arguments = retrieval.get("arguments")
    if not isinstance(tool, str) or not tool or not isinstance(arguments, dict) or not arguments:
        return None
    handle = arguments.get("handle")
    if "handle" in arguments:
        if isinstance(handle, str) and handle:
            return {"tool": tool, "arguments": {"handle": handle}}
        return None
    clean: dict[str, Any] = {}
    for name, value in arguments.items():
        if not isinstance(name, str) or isinstance(value, bool):
            return None
        if not isinstance(value, (str, int)) or value == "":
            return None
        clean[name] = value
    return {"tool": tool, "arguments": clean}


def read_call(record: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """``(tool, arguments)`` that re-read a public record's connected item, or ``None``.

    For :meth:`beakr_sdk.resources.integrations.Integrations.call`. ``None`` for a
    record with no ``retrieval`` and for a connector file's download ``handle``,
    which is fetched with ``files.download_link`` rather than a provider tool.
    """
    retrieval = record.get("retrieval") if isinstance(record, dict) else None
    if not isinstance(retrieval, dict):
        return None
    tool, arguments = retrieval.get("tool"), retrieval.get("arguments")
    if not isinstance(tool, str) or not tool or not isinstance(arguments, dict) or not arguments:
        return None
    if "handle" in arguments:
        return None
    return tool, dict(arguments)


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
