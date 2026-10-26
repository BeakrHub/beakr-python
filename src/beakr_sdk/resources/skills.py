"""Skills: SKILL.md folders your agent loads on demand (``/v1/skills``).

One list covers every skill you can use or install, each labelled with its
``visibility`` (``private``, ``org``, ``public``), ``owner`` (``me``, ``org``,
``system`` for built-ins, ``anyone`` for public skills from outside your
organization), ``origin`` (``created``, ``mined``, ``imported``, ``system``) and
whether it is ``installed`` in your library; every label is also a filter.

Example::

    mine = beakr.skills.list(owner="me")
    available = beakr.skills.list(visibility="public", installed=False)
    skill = beakr.skills.create_from_folder("skills/plate-reader")
    beakr.skills.download(skill["id"], "exported/")
    for proposal in beakr.skills.proposals.list():
        beakr.skills.proposals.accept(proposal["id"])
"""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from beakr_sdk._calls import Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource
from beakr_sdk.resources._destination import names_directory

__all__ = ["SKILL_MD", "SkillProposals", "Skills", "bundle_sha256", "read_skill_folder"]

SKILL_MD = "SKILL.md"

#: Names a skill folder on disk carries that are not part of the skill.
_IGNORED_NAMES = frozenset({".DS_Store", "__pycache__", ".git"})


def _values(value: str | Iterable[str] | None) -> list[str] | None:
    """One filter value or several, as the repeated query parameter the API reads."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


def bundle_sha256(files: Mapping[str, str]) -> str:
    """The digest Beakr reports as a skill's ``files_sha256``.

    ``sha256`` over ``path\\0sha256(content)`` lines in path order, as the
    server computes it, so a client can send the ``expected_sha256`` of files it
    holds without reading them back.
    """
    lines = [
        f"{path}\0{hashlib.sha256((files[path] or '').encode('utf-8')).hexdigest()}"
        for path in sorted(files)
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _safe_relative(path: str) -> str:
    """``path`` as a relative POSIX path inside the skill, or ``ValueError``."""
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError(f"Skill file path {path!r} leaves the skill folder.")
    return pure.as_posix()


def _decode(name: str, data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(
            f"{name} is not UTF-8 text; a skill's files are text (SKILL.md, scripts, "
            "references, templates)."
        ) from None


def _folder_files(folder: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for item in sorted(folder.rglob("*")):
        relative = item.relative_to(folder)
        if any(part in _IGNORED_NAMES or part.startswith(".") for part in relative.parts):
            continue
        if item.is_file():
            files[relative.as_posix()] = _decode(relative.as_posix(), item.read_bytes())
    return files


def _zip_files(archive: Path) -> dict[str, str]:
    with zipfile.ZipFile(archive) as opened:
        entries = {
            _safe_relative(info.filename): opened.read(info)
            for info in opened.infolist()
            if not info.is_dir()
            and not any(
                part in _IGNORED_NAMES or part.startswith(".") or part == "__MACOSX"
                for part in PurePosixPath(info.filename).parts
            )
        }
    # A zip of the folder (``plate-reader/SKILL.md``), as Beakr's own export and
    # most archivers write it, is read as the folder's contents.
    if SKILL_MD not in entries:
        tops = {PurePosixPath(name).parts[0] for name in entries}
        if len(tops) == 1:
            top = tops.pop()
            entries = {
                PurePosixPath(name).relative_to(top).as_posix(): data
                for name, data in entries.items()
                if name != top
            }
    return {name: _decode(name, data) for name, data in entries.items()}


def read_skill_folder(path: str | Path) -> dict[str, str]:
    """A SKILL.md folder or a ``.zip`` of one, as the ``{path: text}`` files the API takes.

    Hidden files (``.git``, ``.DS_Store``) are left out. Raises ``ValueError``
    when there is no ``SKILL.md`` or a file is not UTF-8 text.
    """
    source = Path(path)
    if source.is_dir():
        files = _folder_files(source)
    elif source.is_file() and zipfile.is_zipfile(source):
        files = _zip_files(source)
    elif source.is_file() and source.name == SKILL_MD:
        files = {SKILL_MD: _decode(SKILL_MD, source.read_bytes())}
    else:
        raise ValueError(f"{source} is not a skill folder, a SKILL.md or a .zip of one.")
    if SKILL_MD not in files:
        raise ValueError(f"{source} has no {SKILL_MD} at its top level.")
    return files


def _write_folder(folder: Path, files: Mapping[str, str]) -> Path:
    # Every path is checked before anything is written, so a bundle naming a
    # path outside the folder leaves no partial copy behind.
    targets = {folder / _safe_relative(name): content for name, content in files.items()}
    folder.mkdir(parents=True, exist_ok=True)
    for target, content in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return folder


class SkillProposals(Resource[M]):
    """Skills Beakr drafted from work you repeated, waiting for you to keep or dismiss."""

    @returns_all(ops.build_skills_list_mined_skill_proposals)
    def list(self, *, status: str | None = None) -> Any:
        """Proposals by ``status``: ``pending`` (the default), ``accepted`` or ``dismissed``."""
        return self._list_all(ops.build_skills_list_mined_skill_proposals(status=status))

    @returns(ops.build_skills_accept_mined_skill_proposal)
    def accept(self, proposal_id: str, *, force: bool | None = None) -> Any:
        """Make the draft a private skill of yours, installed in your library.

        ``force`` creates it even when it repeats a skill you have
        (``ConflictError`` with code ``duplicate_candidate`` otherwise).
        """
        return self._run(ops.build_skills_accept_mined_skill_proposal(proposal_id, force=force))

    @returns(ops.build_skills_dismiss_mined_skill_proposal)
    def dismiss(self, proposal_id: str) -> Any:
        return self._run(ops.build_skills_dismiss_mined_skill_proposal(proposal_id))


class Skills(Resource[M]):
    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.proposals: SkillProposals[M] = SkillProposals(client)

    @returns_all(ops.build_skills_list_skills)
    def list(
        self,
        *,
        visibility: str | Iterable[str] | None = None,
        owner: str | Iterable[str] | None = None,
        origin: str | Iterable[str] | None = None,
        installed: bool | None = None,
        search: str | None = None,
        domain: str | None = None,
        include_content: bool | None = None,
    ) -> Any:
        """Every skill you can use or install, by name, following every page.

        Each filter takes one value or several: ``owner=["me", "org"]``.
        ``installed=False`` lists the skills you could add to your library;
        ``include_content=True`` adds each skill's ``files`` (the server then
        pages 25 skills at a time). Retired skills are not listed.
        """
        return self._list_all(
            ops.build_skills_list_skills(
                visibility=_values(visibility),
                owner=_values(owner),
                origin=_values(origin),
                installed=installed,
                search=search,
                domain=domain,
                include_content=include_content,
            )
        )

    @returns(ops.build_skills_get_skill)
    def get(self, skill: str, *, include_content: bool | None = None) -> Any:
        """One skill by id or slug, with ``latest_revision``.

        A slug resolves to your own skill first, then a built-in, then one you
        installed, then your organization's; pass the id for any other.
        """
        return self._run(ops.build_skills_get_skill(skill, include_content=include_content))

    @returns(ops.build_skills_get_skill_bundle)
    def bundle(self, skill_id: str) -> Any:
        """The SKILL.md folder: ``{skill_id, slug, files, file_count, files_sha256}``.

        ``files`` maps each path to its text; send ``files_sha256`` back as
        ``expected_sha256`` when replacing them.
        """
        return self._run(ops.build_skills_get_skill_bundle(skill_id))

    def download(self, skill_id: str, destination: str | Path, *, as_zip: bool = False) -> Any:
        """Save a skill's SKILL.md folder, or its zip with ``as_zip``. Returns the path.

        When ``destination`` is a directory (an existing one, or a path ending
        in ``/`` that is created) the skill is saved inside it, as ``<slug>/``
        or ``<slug>.zip``; otherwise ``destination`` is the folder (or zip
        file) itself.
        """
        target = Path(destination)
        into_directory = names_directory(destination)

        def flow() -> Flow:
            if as_zip:
                data = yield ops.build_skills_download_skill_bundle_zip(skill_id)
                if into_directory:
                    bundle = yield ops.build_skills_get_skill_bundle(skill_id)
                    path = target / f"{bundle['slug']}.zip"
                else:
                    path = target
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return path
            bundle = yield ops.build_skills_get_skill_bundle(skill_id)
            folder = target / str(bundle["slug"]) if into_directory else target
            return _write_folder(folder, bundle.get("files") or {})

        return self._drive(flow())

    def create(
        self,
        files: Mapping[str, str],
        *,
        visibility: str | None = None,
        source: str | None = None,
        slug_hint: str | None = None,
    ) -> Any:
        """Create a skill of yours from its files (``{path: text}``, with ``SKILL.md``).

        It is installed in your library and private unless ``visibility`` says
        otherwise; ``"public"`` publishes it, every file included, to every
        Beakr organization. ``source`` records where the folder came from
        (``claude-code``, ``codex``...). Returns the skill as :meth:`get` does.
        """
        body = clean_body(
            {
                "files": dict(files),
                "visibility": visibility,
                "source": source,
                "slug_hint": slug_hint,
            }
        )
        return self._run(ops.build_skills_create_skill(body=body))

    def create_from_folder(
        self,
        path: str | Path,
        *,
        visibility: str | None = None,
        source: str | None = None,
        slug_hint: str | None = None,
    ) -> Any:
        """Create a skill from a SKILL.md folder or a ``.zip`` of one (see :meth:`create`)."""
        return self.create(
            read_skill_folder(path), visibility=visibility, source=source, slug_hint=slug_hint
        )

    def upload_bundle(
        self,
        skill_id: str,
        files: Mapping[str, str] | str | Path,
        *,
        replace: bool = True,
        expected_sha256: str | None = None,
        force: bool = False,
    ) -> Any:
        """Replace the files of a skill you own, from ``{path: text}`` or a folder or zip.

        ``replace=False`` writes the files given and keeps the rest. Pass
        ``expected_sha256`` (the ``files_sha256`` you read, or
        :func:`bundle_sha256` of the files you started from) and a change made
        since is refused with ``ConflictError`` (``skill_conflict``) instead of
        overwritten; ``force=True`` overwrites anyway.
        """
        payload = read_skill_folder(files) if isinstance(files, (str, Path)) else dict(files)
        body: dict[str, Any] = {"files": payload, "replace": replace}
        if expected_sha256 is not None:
            body["expected_sha256"] = expected_sha256
        if force:
            body["force"] = True
        return self._run(ops.build_skills_replace_skill_bundle(skill_id, body=body))

    @returns(ops.build_skills_install_skill)
    def install(self, skill_id: str) -> Any:
        """Add a skill you can see to your library, so your agent is told about it."""
        return self._run(ops.build_skills_install_skill(skill_id))

    @returns(ops.build_skills_uninstall_skill)
    def uninstall(self, skill_id: str) -> Any:
        """Remove a skill from your library; the skill itself is kept."""
        return self._run(ops.build_skills_uninstall_skill(skill_id))

    @returns(ops.build_skills_delete_skill)
    def delete(self, skill_id: str) -> Any:
        """Delete a skill you wrote, for everyone who installed it."""
        return self._run(ops.build_skills_delete_skill(skill_id))
