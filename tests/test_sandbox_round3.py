"""SDK rough edges found against sandbox on 2026-11-14.

* ``knowledge.query("SELECT ...")`` raised ``ValueError: dictionary update
  sequence element #0 has length 1``: the SQL, passed positionally, landed in
  ``spec`` and was turned into a dict.
* The MCP tool and the CLI call it ``integrations ... call``; the SDK had only
  ``call_tool``.
* There was no accessor for "re-read this source" from a public record's
  ``retrieval``.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_sdk.sources import public_source, read_call

from .conftest import BASE

COMMAND = f"{BASE}/v1/knowledge/wiki/command"
SQL = "SELECT entity, COUNT(*) AS n FROM facts GROUP BY entity"
REPLY = {"content": [{"type": "text", "text": "1 row"}], "outcome": {"status": "success"}}


@respx.mock
def test_query_takes_the_sql_positionally(client) -> None:
    route = respx.post(COMMAND).respond(200, json=REPLY)

    client.knowledge.query(SQL)
    client.knowledge.query(sql=SQL)

    sent = [json.loads(call.request.content) for call in route.calls]
    assert sent[0] == sent[1]
    assert sent[0]["command"] == "query" and sent[0]["sql"] == SQL and "spec" not in sent[0]


@respx.mock
async def test_query_takes_the_sql_positionally_async(async_client) -> None:
    route = respx.post(COMMAND).respond(200, json=REPLY)

    await async_client.knowledge.query(SQL)

    assert json.loads(route.calls.last.request.content)["sql"] == SQL


def test_query_refuses_sql_twice_and_neither(client) -> None:
    with pytest.raises(ValueError):
        client.knowledge.query(SQL, sql=SQL)
    with pytest.raises(ValueError):
        client.knowledge.query()


@respx.mock
def test_a_spec_is_still_sent_as_a_spec(client) -> None:
    route = respx.post(COMMAND).respond(200, json=REPLY)

    client.knowledge.query({"from": "facts"})

    assert json.loads(route.calls.last.request.content)["spec"] == {"from": "facts"}


@respx.mock
def test_integrations_call_is_call_tool(client) -> None:
    route = respx.post(f"{BASE}/v1/integrations/tools/granola_get_note/call").respond(
        200, json={"content": "note"}
    )

    client.integrations.call("granola_get_note", {"note_id": "not_1"}, connector_id="c1")

    assert json.loads(route.calls.last.request.content) == {
        "arguments": {"note_id": "not_1"},
        "connector_id": "c1",
    }


@respx.mock
async def test_integrations_call_is_call_tool_async(async_client) -> None:
    route = respx.post(f"{BASE}/v1/integrations/tools/gmail_recent/call").respond(
        200, json={"content": "mail"}
    )

    await async_client.integrations.call("gmail_recent")

    assert json.loads(route.calls.last.request.content) == {"arguments": {}}


def test_public_source_keeps_retrieval_and_read_call_gives_the_call() -> None:
    slack = {
        "name": "Slack: #lightfield-beakr",
        "provider": "slack",
        "retrieval": {"tool": "slack_list_messages", "arguments": {"channel_id": "C0AMUSP0YMB"}},
    }
    record = public_source(slack)

    assert record["retrieval"] == slack["retrieval"]
    assert read_call(record) == ("slack_list_messages", {"channel_id": "C0AMUSP0YMB"})


def test_read_call_is_none_for_a_file_handle_and_for_no_retrieval() -> None:
    handle = public_source(
        {"name": "memo.pdf", "retrieval": {"tool": "gdrive_download", "arguments": {"handle": "h"}}}
    )
    assert handle["retrieval"] == {"tool": "gdrive_download", "arguments": {"handle": "h"}}
    assert read_call(handle) is None
    assert read_call(public_source({"name": "x"})) is None
    assert read_call({"retrieval": {"tool": "", "arguments": {"a": 1}}}) is None
