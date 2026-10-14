"""Knowledge pages over REST: ``client.knowledge.pages``.

The Knowledge door (``knowledge.command`` / ``knowledge.write``) is the shared
read/write vocabulary agents use. These routes return the same pages as
structured JSON for programs that want fields rather than rendered text:
listings, a page by id or title, its revisions, sources and provenance, the
link graph, and ingestion history.

Example::

    page = beakr.knowledge.pages.by_title("BK-7", project_id=project_id)
    for rev in beakr.knowledge.pages.revisions(page["id"])["data"]:
        print(rev["revision"], rev["created_at"])
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import Flow
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M, collect_pages
from beakr_sdk._typing import iterates, returns
from beakr_sdk.resources._base import Resource
from beakr_sdk.sources import public_source


class KnowledgePages(Resource[M]):
    @returns(ops.build_knowledge_list_wiki_pages)
    def list(
        self,
        *,
        project_id: str | None = None,
        page_type: str | None = None,
        parent_page_id: str | None = None,
        roots_only: bool | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        sort_by: str | None = None,
        archived: bool | None = None,
        org_only: bool | None = None,
        all_scopes: bool | None = None,
    ) -> Any:
        """One page of pages in a scope: ``{data, next_cursor, has_more}``.

        ``all_scopes`` lists every scope the caller can read; pass ``next_cursor``
        back as ``cursor`` for the next page, or use :meth:`iter`.
        """
        return self._run(
            ops.build_knowledge_list_wiki_pages(
                project_id=project_id,
                page_type=page_type,
                parent_page_id=parent_page_id,
                roots_only=roots_only,
                limit=limit,
                cursor=cursor,
                sort_by=sort_by,
                archived=archived,
                org_only=org_only,
                all=all_scopes,
            )
        )

    @returns(ops.build_knowledge_get_wiki_page_by_title)
    def by_title(self, title: str, *, project_id: str | None = None) -> Any:
        return self._run(
            ops.build_knowledge_get_wiki_page_by_title(title=title, project_id=project_id)
        )

    @returns(ops.build_knowledge_get_wiki_page)
    def get(self, page_id: str) -> Any:
        return self._run(ops.build_knowledge_get_wiki_page(page_id))

    @iterates(ops.build_knowledge_list_wiki_pages)
    def iter(
        self,
        *,
        project_id: str | None = None,
        page_type: str | None = None,
        parent_page_id: str | None = None,
        roots_only: bool | None = None,
        sort_by: str | None = None,
        all_scopes: bool | None = None,
        page_size: int | None = None,
    ) -> Any:
        """Every page in a scope, following the cursor."""
        return self._iter(
            ops.build_knowledge_list_wiki_pages(
                project_id=project_id,
                page_type=page_type,
                parent_page_id=parent_page_id,
                roots_only=roots_only,
                sort_by=sort_by,
                all=all_scopes,
                limit=page_size,
            )
        )

    @returns(ops.build_knowledge_get_wiki_page_revisions)
    def revisions(
        self, page_id: str, *, limit: int | None = None, cursor: str | None = None
    ) -> Any:
        """One page of revisions, newest first: ``{data, next_cursor, has_more, total}``."""
        return self._run(
            ops.build_knowledge_get_wiki_page_revisions(page_id, limit=limit, cursor=cursor)
        )

    def sources(self, page_id: str) -> Any:
        """Every source that fed the page, as public source records.

        Each record says where the source lives -- ``filename``, ``provider``,
        ``location.url`` / ``location.display_path`` for a connector file, and
        ``location.file_id`` for Beakr's copy (``files.download(file_id)``) --
        plus its ``stance``, ``section`` and recorded ``evidence``; the shape
        is documented in :mod:`beakr_sdk.sources` and matches what the MCP
        server and ``runs.answer`` return.
        """

        def flow() -> Flow:
            rows = yield from collect_pages(ops.build_knowledge_get_wiki_page_sources(page_id))
            return [public_source(row) for row in rows if isinstance(row, dict)]

        return self._drive(flow())

    @returns(ops.build_knowledge_search_wiki_pages)
    def search(
        self,
        q: str,
        *,
        project_id: str | None = None,
        limit: int | None = None,
        all_scopes: bool | None = None,
    ) -> Any:
        return self._run(
            ops.build_knowledge_search_wiki_pages(
                q=q, project_id=project_id, limit=limit, all=all_scopes
            )
        )
