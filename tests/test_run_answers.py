"""``runs.answer`` / ``runs.stream_text`` and the public source record.

Getting an answer used to mean hand-parsing the run's events (skip thinking
deltas, join ``content_delta``, resolve ``{{key}}`` tokens against ``sources``
events); every integrator rewrote that loop and shipped raw tokens to end
users (dogfood friction rows 8 and 12). These pin the helpers that do it once.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_sdk import RunFailedError, RunNeedsInputError
from beakr_sdk.answers import TextStream
from beakr_sdk.sources import public_source, source_where

from .conftest import BASE

_SSE = {"content-type": "text/event-stream"}
_RUN = "11111111-1111-1111-1111-111111111111"


def _frames(*events: dict) -> str:
    return "".join(f"id: c{i}\ndata: {json.dumps(e)}\n\n" for i, e in enumerate(events))


_EVENTS = (
    {"type": "content_delta", "data": {"delta": "Let me think", "is_thinking": True}},
    {
        "type": "sources",
        "data": {
            "sources": [
                {
                    "key": "rag:a1",
                    "type": "rag",
                    "title": "runtime.md",
                    "artifact_id": "8c03c92e-d904-43be-b055-d364a606df74",
                    "artifact_version_id": "internal-version",
                }
            ]
        },
    },
    {"type": "sources", "data": {"sources": [{"key": "rag:a1", "quote": "300 seconds"}]}},
    {"type": "content_delta", "data": {"delta": "Leases last 300s.{{rag:"}},
    {"type": "content_delta", "data": {"delta": "a1}}"}},
    {
        "type": "stream_complete",
        "data": {"status": "completed", "final_text": "Leases last 300s.{{rag:a1}}"},
    },
)


def _mock_run(events: tuple[dict, ...] = _EVENTS, *, run: dict | None = None) -> respx.Route:
    """The run's start, its event stream, and ``GET /v1/runs/{id}`` after it ends.

    ``run`` defaults to a completed run with no ``answer`` (a server from before
    the rendered answer), so the answer comes from the stream.
    """
    respx.post(f"{BASE}/v1/runs").respond(json={"run_id": _RUN, "thread_id": "t1"})
    respx.get(f"{BASE}/v1/runs/{_RUN}").respond(
        json=run or {"run_id": _RUN, "thread_id": "t1", "status": "completed"}
    )
    return respx.get(f"{BASE}/v1/runs/{_RUN}/events").respond(text=_frames(*events), headers=_SSE)


@respx.mock
def test_answer_renders_citations_and_returns_their_source_records(client) -> None:
    _mock_run()
    answer = client.runs.answer("How long is a lease?", project_id="p1")
    assert answer["text"] == "Leases last 300s.[1]"
    assert answer["run_id"] == _RUN
    [source] = answer["sources"]
    assert source["citation_key"] == "rag:a1"
    assert source["name"] == "runtime.md"
    assert source["location"]["file_id"] == "8c03c92e-d904-43be-b055-d364a606df74"
    assert source["evidence"]["excerpt"] == "300 seconds"
    # Internal version ids never reach a program.
    assert "internal-version" not in json.dumps(source)


@respx.mock
async def test_answer_on_the_async_client(async_client) -> None:
    _mock_run()
    answer = await async_client.runs.answer("How long is a lease?")
    assert answer["text"] == "Leases last 300s.[1]"


# runs.answer starts its run through runs.start, so it carries the same
# Knowledge toggle: on by default (the server reads a missing toggle as off),
# off only when asked.
@respx.mock
def test_answer_passes_the_knowledge_toggle_to_its_run(client) -> None:
    _mock_run()
    client.runs.answer("How long is a lease?")
    client.runs.answer("How long is a lease?", knowledge=False)
    starts = [c for c in respx.calls if c.request.method == "POST"]
    scopes = [json.loads(c.request.content)["scope"] for c in starts]
    assert scopes == [{"knowledge_graph": True}, {"knowledge_graph": False}]


@respx.mock
def test_answer_is_the_engines_rendering_with_the_streamed_file_id(client) -> None:
    # GET /v1/runs `answer` is the single source of truth for a completed run;
    # the stream only adds what the engine's record leaves out (the file id).
    engine_record = {
        "source_ref": "beakr-source:v1:key:x",
        "citation_key": "rag:a1",
        "name": "runtime.md",
        "filename": "runtime.md",
        "source_kind": "rag",
        "location": {"display_path": "runtime.md"},
        "evidence": {"excerpt": "Engine excerpt"},
    }
    _mock_run(
        run={
            "run_id": _RUN,
            "thread_id": "t1",
            "status": "completed",
            "answer": {
                "text": "Engine-rendered 300s.[1]",
                "raw_text": "Engine-rendered 300s.{{rag:a1}}",
                "sources": [engine_record],
            },
        }
    )
    answer = client.runs.answer("How long is a lease?")
    assert answer["text"] == "Engine-rendered 300s.[1]"
    [source] = answer["sources"]
    assert source["evidence"]["excerpt"] == "Engine excerpt"
    assert source["location"] == {
        "display_path": "runtime.md",
        "file_id": "8c03c92e-d904-43be-b055-d364a606df74",
    }
    assert source["provider"] == "upload"


@respx.mock
def test_a_failed_run_raises_instead_of_returning_an_empty_answer(client) -> None:
    _mock_run(
        ({"type": "stream_complete", "data": {"status": "failed"}},),
        run={"run_id": _RUN, "thread_id": "t1", "status": "failed"},
    )
    with pytest.raises(RunFailedError, match="ended failed"):
        client.runs.answer("anything")


@respx.mock
def test_a_run_that_parks_on_a_question_raises_it_instead_of_an_empty_answer(client) -> None:
    # Root cause of dogfood row 9: an out-of-scope question made the agent
    # stop on an ask-user decision ("Are you authorized ...?"); no answer text
    # was streamed, and the run came back as an empty answer.
    _mock_run(
        (
            {
                "type": "stream_complete",
                "thread_id": "t1",
                "data": {"status": "interrupted", "interrupt_kind": "decision"},
            },
        )
    )
    question = {"text": "Are you authorized?", "options": ["Yes", "No"], "context": None}
    respx.get(f"{BASE}/v1/runs/{_RUN}").respond(
        json={"run_id": _RUN, "thread_id": "t1", "status": "interrupted", "question": question}
    )
    with pytest.raises(RunNeedsInputError, match="Are you authorized") as caught:
        client.runs.answer("What is customer B's codename?")
    assert caught.value.question == question
    assert caught.value.thread_id == "t1" and caught.value.interrupt_kind == "decision"
    assert isinstance(caught.value, RunFailedError)


@respx.mock
def test_stream_text_skips_thinking_and_numbers_split_tokens(client) -> None:
    _mock_run()
    chunks = list(client.runs.stream_text(_RUN))
    assert "".join(chunks) == "Leases last 300s.[1]"
    assert all("{{" not in chunk for chunk in chunks)


def test_text_stream_releases_braces_that_never_close() -> None:
    stream = TextStream()
    assert stream.feed("a {b") == "a {b"
    assert stream.feed(" {{x") == " "
    assert stream.finish() == "{{x"


def test_page_source_row_maps_to_the_public_record() -> None:
    row = {
        "source_ref": "beakr-source:v1:1",
        "key": "artifact:v1-u1",
        "source_type": "artifact",
        "source_title": "plan.md",
        "section_id": "sec_description",
        "stance": "support",
        "file_name": "plan.md",
        "origin": "upload",
        "artifact_id": "f1",
        "artifact_version_id": "v1",
        "doc_sha256": "abc",
        "excerpt": "quote",
        "fidelity": "verbatim",
    }
    record = public_source(row)
    assert record == {
        "source_ref": "beakr-source:v1:1",
        "citation_key": "artifact:v1-u1",
        "name": "plan.md",
        "filename": "plan.md",
        "source_kind": "artifact",
        "stance": "support",
        "provider": "upload",
        "location": {"file_id": "f1"},
        "evidence": {"excerpt": "quote", "fidelity": "verbatim"},
        "section": {"id": "sec_description"},
    }
    assert source_where(record) == "plan.md · upload · file f1 (`beakr files download f1`)"


def test_a_connector_source_points_at_its_original_not_a_download() -> None:
    record = public_source(
        {
            "name": "Q3 plan",
            "file_name": "Q3 plan.pdf",
            "provider": "google_drive",
            "url": "https://drive.example/q3",
            "artifact_id": "f2",
        }
    )
    assert source_where(record) == "Q3 plan.pdf · google_drive · https://drive.example/q3"


@respx.mock
def test_pages_sources_returns_public_records(client) -> None:
    respx.get(f"{BASE}/v1/knowledge/wiki/pages/pg1/sources").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"key": "artifact:v-u1", "source_title": "a.md", "doc_sha256": "x"}],
                "next_cursor": None,
                "has_more": False,
            },
        )
    )
    [record] = client.knowledge.pages.sources("pg1")
    assert record == {"citation_key": "artifact:v-u1", "name": "a.md"}


def test_public_source_keeps_where_the_passage_sits_and_compact_excerpts() -> None:
    # Regression: compact Knowledge records carry their passages as ``excerpts``
    # (each with its token and ``unit_locator``); public_source dropped them, and
    # the new location fields, so a Sources listing showed no excerpt or place.
    record = public_source(
        {
            "name": "memo.pdf",
            "citation_key": "artifact:v1-u1",
            "provider": "upload",
            "location": {
                "file_id": "f1",
                "display_path": "deals/acme/memo.pdf",
                "unit_locator": "p. 7",
                "upload_path": "acme/memo.pdf",
                "internal_unit_id": "secret",
            },
            "excerpts": [
                {"citation_key": "artifact:v1-u1", "unit_locator": "p. 7", "text": "Rent is $5"},
                {"citation_key": "artifact:v1-u2", "text": ""},
                {"citation_key": "artifact:v1-u3", "unit_locator": "p. 9", "text": "Term: 5y"},
            ],
            "cited_passages": 3,
        }
    )
    assert record["location"] == {
        "display_path": "deals/acme/memo.pdf",
        "unit_locator": "p. 7",
        "upload_path": "acme/memo.pdf",
        "file_id": "f1",
    }
    assert record["excerpts"] == [
        {"citation_key": "artifact:v1-u1", "unit_locator": "p. 7", "text": "Rent is $5"},
        {"citation_key": "artifact:v1-u3", "unit_locator": "p. 9", "text": "Term: 5y"},
    ]
    assert record["cited_passages"] == 3
