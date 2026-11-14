"""A public source record keeps the call that re-reads a connected item.

Root cause: ``public_source`` forwarded ``retrieval`` only when its arguments
carried a download ``handle``. The API attaches a different call to a cited
meeting note, email or Slack thread -- the provider's read tool and the item's
own id (``granola_get_note`` with ``note_id``) -- and that one was dropped, so
``knowledge.pages.sources()``, ``beakr kb sources --json`` and the stdio MCP
server's Sources list offered only the share link, whose id the read tool
refuses. The engine's ``formatting.public_source`` keeps both forms; this
mirrors it.
"""

from __future__ import annotations

import pytest

from beakr_sdk.sources import public_source

NOTE_CALL = {"tool": "granola_get_note", "arguments": {"note_id": "not_LiveRf00000001"}}
THREAD_CALL = {
    "tool": "slack_get_thread",
    "arguments": {"channel_id": "C04K10YTMN0", "thread_ts": "1742744865.524899"},
}


@pytest.mark.parametrize("call", [NOTE_CALL, THREAD_CALL])
def test_a_connected_items_read_call_is_kept(call: dict) -> None:
    record = public_source({"name": "note", "source_ref": "beakr-source:v1:n", "retrieval": call})
    assert record["retrieval"] == call


def test_a_file_retrieval_still_keeps_only_its_handle() -> None:
    record = public_source(
        {
            "name": "memo.pdf",
            "retrieval": {
                "tool": "drive_download",
                "arguments": {"handle": "beakr://external/x", "connector_id": "internal"},
            },
        }
    )
    assert record["retrieval"] == {
        "tool": "drive_download",
        "arguments": {"handle": "beakr://external/x"},
    }


@pytest.mark.parametrize(
    "retrieval",
    [
        {"tool": "granola_get_note", "arguments": {"note_id": {"nested": "value"}}},
        {"tool": "granola_get_note", "arguments": {}},
        {"tool": "granola_get_note", "arguments": {"note_id": True}},
        {"tool": "granola_get_note", "arguments": {"note_id": ""}},
        {"tool": "", "arguments": {"note_id": "not_abc"}},
        {"tool": "drive_download", "arguments": {"handle": ""}},
        {"arguments": {"note_id": "not_abc"}},
    ],
)
def test_a_call_that_is_not_a_tool_with_scalar_arguments_is_dropped(retrieval: dict) -> None:
    assert "retrieval" not in public_source({"name": "note", "retrieval": retrieval})
