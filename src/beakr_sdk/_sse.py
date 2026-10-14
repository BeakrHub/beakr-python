"""Server-sent events parsing for run streams and the event stream.

Streams send frames ``id: <id>\\ndata: <json>\\n\\n`` and ``: heartbeat``
comments. The parser is line-fed so the sync and async clients can share it.
A frame with an ``id`` and no data dispatches nothing but still moves
``last_event_id`` (as an EventSource does): an event stream may open with one
to say where it started, so a reconnect before any event misses nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: The event type that ends a run stream.
STREAM_COMPLETE = "stream_complete"


@dataclass
class SSEParser:
    """Accumulate lines into events; ``feed`` returns an event at each blank line."""

    _event_id: str | None = None
    _data: list[str] = field(default_factory=list)

    def feed(self, line: str) -> dict[str, Any] | None:
        line = line.rstrip("\r")
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "id":
            self._event_id = value
        elif name == "data":
            self._data.append(value)
        return None

    @property
    def last_event_id(self) -> str | None:
        """The most recent ``id`` the stream sent, with or without data."""
        return self._event_id

    def _dispatch(self) -> dict[str, Any] | None:
        if not self._data:
            return None
        raw = "\n".join(self._data)
        self._data = []
        try:
            event = json.loads(raw)
        except ValueError:
            event = {"type": "message", "data": raw}
        if not isinstance(event, dict):
            event = {"type": "message", "data": event}
        if self._event_id is not None:
            event.setdefault("event_id", self._event_id)
        return event
