"""A public source record names who wrote a conversation citation, by name only.

``public_source`` copies an allowlist of fields into the public record, and
``filed_by`` was not on it, so the writer of an inline ``conversation``,
``agent_note`` or ``user_note`` citation reached no ``knowledge.pages.sources()``
or ``beakr kb sources --json`` record although the contract's ``SourceRecord``
declares it. The engine's own ``public_source`` (engine #1687) keeps the
name-only form: ``user_name``, ``agent``, ``client``, ``run``, ``session`` and
``text``, never ``user_id``. This mirrors it.
"""

from __future__ import annotations

import httpx
import respx

from beakr_sdk.sources import public_source

from .conftest import BASE

USER_ID = "a8469189-0000-0000-0000-000000000000"
FILED_BY = {
    "user_name": "Local evaluation administrator",
    "user_id": USER_ID,
    "agent": "claude-code/2.1.304",
    "client": "beakr-mcp/0.4.0",
    "run": "stdio:8d2a",
    "session": "sess-41",
    "text": "Local evaluation administrator via claude-code/2.1.304",
}
PUBLIC = {key: value for key, value in FILED_BY.items() if key != "user_id"}


def test_filed_by_is_kept_in_its_public_form() -> None:
    record = public_source(
        {
            "source_ref": "beakr-source:v1:abc",
            "name": "fx-a retest chat",
            "source_kind": "conversation",
            "filed_by": FILED_BY,
        }
    )
    assert record["filed_by"] == PUBLIC
    assert USER_ID not in str(record)


def test_a_page_sources_row_carries_it_in_meta() -> None:
    record = public_source(
        {"key": "conversation:fx-a", "source_title": "fx-a chat", "meta": {"filed_by": FILED_BY}}
    )
    assert record["filed_by"] == PUBLIC
    assert USER_ID not in str(record)


def test_empty_and_unknown_filer_fields_are_left_out() -> None:
    record = public_source(
        {"name": "n", "filed_by": {"agent": "bot", "user_name": "", "email": "x@y.test"}}
    )
    assert record["filed_by"] == {"agent": "bot"}


def test_a_record_without_a_filer_has_no_filed_by() -> None:
    assert "filed_by" not in public_source({"name": "memo.pdf", "source_ref": "beakr-source:v1:x"})
    assert "filed_by" not in public_source({"name": "memo.pdf", "filed_by": {"user_id": USER_ID}})
    assert "filed_by" not in public_source({"name": "memo.pdf", "filed_by": "someone"})


@respx.mock
def test_pages_sources_names_the_filer(client) -> None:
    respx.get(f"{BASE}/v1/knowledge/wiki/pages/pg1/sources").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "key": "conversation:fx-a",
                        "source_title": "fx-a chat",
                        "source_type": "conversation",
                        "meta": {"filed_by": FILED_BY},
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )
    )
    [record] = client.knowledge.pages.sources("pg1")
    assert record["filed_by"] == PUBLIC
    assert USER_ID not in str(record)
