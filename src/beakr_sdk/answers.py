"""Turn an agent run's event stream into an answer a program can show.

A run streams ``content_delta`` events (some marked ``is_thinking``), ``sources``
events keyed by citation key, and a closing ``stream_complete`` carrying the
final text. Its citations are ``{{key}}`` tokens. :class:`AnswerBuilder` folds
those events into::

    {"text": "... lasts 300 seconds (runtime.md › lines 4-9). ...",
     "sources": [<source record>, ...], "run_id": "..."}

Once the run has finished, the answer is the engine's own rendering from
``GET /v1/runs/{run_id}``: each citation shown as the readable reference in
parentheses (``(memo.pdf › p. 7)``), and one public source record
(:mod:`beakr_sdk.sources`) per cited document in order of first use. Only a
server that does not send that rendering gets the answer assembled from the
stream, with each ``{{key}}`` numbered ``[n]`` and ``sources[n-1]`` behind it.
:class:`TextStream` numbers streamed deltas ``[n]`` per citation key, holding
back only a token that is still being written.
"""

from __future__ import annotations

import re
from typing import Any

from beakr_sdk.errors import BeakrError
from beakr_sdk.sources import public_source

__all__ = [
    "AnswerBuilder",
    "RunFailedError",
    "RunNeedsInputError",
    "TextStream",
    "number_citations",
]

CITATION_TOKEN = re.compile(r"\{\{([^}]+)\}\}")

#: A citation key is short; text held back waiting for a closing ``}}`` longer
#: than this is ordinary text and is released rather than stalling the stream.
_MAX_PENDING = 256

_FAILED = frozenset({"error", "failed", "cancelled"})


class RunFailedError(BeakrError):
    """The run ended without an answer (``error``, ``failed`` or ``cancelled``)."""


class RunNeedsInputError(RunFailedError):
    """The run stopped to ask the person something (status ``interrupted``).

    The agent parks on a question -- a decision, a missing detail, an
    authorization check -- instead of answering, and sends no answer text.
    ``question`` is the run's ``{text, options, context}`` as ``GET
    /v1/runs/{run_id}`` reports it (``None`` if the server did not say);
    answer it by starting a run in ``thread_id``.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        thread_id: str | None = None,
        question: dict[str, Any] | None = None,
        interrupt_kind: str | None = None,
    ) -> None:
        super().__init__(message, code="interrupted")
        self.run_id = run_id
        self.thread_id = thread_id
        self.question = question
        self.interrupt_kind = interrupt_kind


def number_citations(text: str, order: list[str]) -> str:
    """Turn ``{{key}}`` citation tokens into ``[n]`` in order of first use.

    ``order`` collects each key the first time it is seen; pass the same list
    across calls (a streamed answer) to keep the numbering stable.
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in order:
            order.append(key)
        return f"[{order.index(key) + 1}]"

    return CITATION_TOKEN.sub(replace, text)


class TextStream:
    """Numbers citations in streamed text even when a token spans deltas.

    Everything that cannot be the start of an unfinished ``{{key}}`` token is
    released immediately; only the shortest suffix that still could be one
    (a trailing ``{``, or an unclosed ``{{...``) is held for the next delta.
    """

    def __init__(self, order: list[str] | None = None) -> None:
        self.order: list[str] = order if order is not None else []
        self.pending = ""

    def feed(self, delta: str) -> str:
        text = number_citations(self.pending + delta, self.order)
        cut = len(text)
        opening = text.rfind("{{")
        if opening != -1 and "}}" not in text[opening:]:
            cut = opening
        elif text.endswith("{"):
            cut = len(text) - 1
        if len(text) - cut > _MAX_PENDING:
            cut = len(text)
        self.pending = text[cut:]
        return text[:cut]

    def finish(self) -> str:
        rest, self.pending = self.pending, ""
        return rest


def answer_delta(event: dict[str, Any]) -> str:
    """The visible answer text an event adds (empty for thinking and other events)."""
    data = event.get("data") or {}
    if event.get("type") == "content_delta" and not data.get("is_thinking"):
        return str(data.get("delta") or "")
    return ""


class AnswerBuilder:
    """Folds a run's events into ``{text, sources, run_id}``."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._deltas: list[str] = []
        self._final_text: str | None = None
        self._sources: dict[str, dict[str, Any]] = {}
        self._status = "completed"
        self._error: str | None = None
        self.thread_id: str | None = None
        self.interrupt_kind: str | None = None

    def feed(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        data = event.get("data") or {}
        if event.get("thread_id"):
            self.thread_id = str(event["thread_id"])
        delta = answer_delta(event)
        if delta:
            self._deltas.append(delta)
        elif kind == "sources":
            for source in data.get("sources") or []:
                if isinstance(source, dict) and source.get("key"):
                    # A later event for the same key carries more (quote, path).
                    self._sources[str(source["key"])] = {
                        **self._sources.get(str(source["key"]), {}),
                        **source,
                    }
        elif kind == "error":
            self._error = str(data.get("message") or data.get("error") or "the run failed")
        elif kind == "stream_complete":
            self._status = str(data.get("status") or self._status)
            if data.get("interrupt_kind"):
                self.interrupt_kind = str(data["interrupt_kind"])
            if isinstance(data.get("final_text"), str):
                self._final_text = data["final_text"]

    @property
    def interrupted(self) -> bool:
        """The run parked on a question for the person instead of answering."""
        return self._status == "interrupted"

    def result(self, *, run: dict[str, Any] | None = None) -> dict[str, Any]:
        """The answer; raises :class:`RunFailedError` when the run ended without one.

        ``run`` is the run as ``GET /v1/runs/{run_id}`` reports it after the
        stream ended. Its ``answer`` -- the engine's own rendering, text with
        readable references in parentheses and the cited sources in order -- is the answer when
        present; the streamed events only fill in what those source records
        leave out (the file id of a document Beakr holds). Without it (an older
        server) the answer is assembled from the stream. An interrupted run
        raises :class:`RunNeedsInputError` with the run's ``question`` rather
        than returning an empty answer.
        """
        state = run if isinstance(run, dict) else {}
        status = str(state.get("status") or self._status)
        if self.interrupted or status == "interrupted":
            question = state.get("question") if isinstance(state.get("question"), dict) else None
            asked = question.get("text") if question else None
            raise RunNeedsInputError(
                f"Run {self.run_id} is waiting for an answer"
                + (f": {asked}" if asked else " (it asked the person a question)"),
                run_id=self.run_id,
                thread_id=self.thread_id or state.get("thread_id"),
                question=question,
                interrupt_kind=self.interrupt_kind or state.get("interrupt_kind"),
            )
        if self._error is not None or self._status in _FAILED or status in _FAILED:
            reason = self._error or f"the run ended {status if status in _FAILED else self._status}"
            raise RunFailedError(f"Run {self.run_id}: {reason}", code=status)
        answer = state.get("answer")
        if isinstance(answer, dict) and isinstance(answer.get("text"), str):
            return {
                "text": answer["text"].strip(),
                "sources": [
                    self._complete(record)
                    for record in answer.get("sources") or []
                    if isinstance(record, dict)
                ],
                "run_id": self.run_id,
            }
        raw = self._final_text if self._final_text is not None else "".join(self._deltas)
        order: list[str] = []
        text = number_citations(raw, order)
        sources = [
            public_source(self._sources.get(key, {"key": key}), citation_key=key) for key in order
        ]
        return {"text": text.strip(), "sources": sources, "run_id": self.run_id}

    def _complete(self, record: dict[str, Any]) -> dict[str, Any]:
        """The engine's source record, with what the streamed source adds (its file id)."""
        key = record.get("citation_key")
        streamed = self._sources.get(str(key), {}) if key else {}
        return public_source({**streamed, **record}, citation_key=key)
