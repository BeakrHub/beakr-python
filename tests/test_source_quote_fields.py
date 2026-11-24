"""A public source record keeps the passage around a quote and when it was recorded.

The engine documents ``context_excerpt`` and ``source_recorded_at`` on a
citation's quote: beside ``excerpt`` on a page sources row
(``GET /v1/knowledge/wiki/pages/{page_id}/sources``) and inside ``evidence`` on
a source record (``command``, ``write``, ``research``). ``public_source`` used
to keep only ``excerpt`` and ``locator`` of them, so both were lost.
"""

from __future__ import annotations

from beakr_sdk.sources import public_source

QUOTE = "Dosing starts at 5 mg."
CONTEXT = "The protocol was amended in March. Dosing starts at 5 mg. Escalation follows."


def test_a_source_record_keeps_its_evidence_quote_fields() -> None:
    record = public_source(
        {
            "name": "protocol.pdf",
            "evidence": {
                "excerpt": QUOTE,
                "context_excerpt": CONTEXT,
                "locator": "p. 7",
                "source_recorded_at": "2026-03-04T14:02:00-05:00",
            },
        }
    )
    assert record["evidence"] == {
        "excerpt": QUOTE,
        "context_excerpt": CONTEXT,
        "locator": "p. 7",
        "source_recorded_at": "2026-03-04T14:02:00-05:00",
    }


def test_a_page_sources_row_keeps_its_quote_fields() -> None:
    # The page sources route carries them at the top level, beside ``excerpt``.
    record = public_source(
        {
            "source_title": "standup",
            "excerpt": QUOTE,
            "context_excerpt": CONTEXT,
            "source_recorded_at": "2026-03-04",
        }
    )
    assert record["evidence"] == {
        "excerpt": QUOTE,
        "context_excerpt": CONTEXT,
        "source_recorded_at": "2026-03-04",
    }


def test_a_record_without_them_gains_nothing() -> None:
    record = public_source({"name": "memo.pdf", "evidence": {"excerpt": QUOTE}})
    assert record["evidence"] == {"excerpt": QUOTE}
