"""A public source record says whose words its excerpt is.

The engine documents ``evidence.excerpt_kind`` (``quote``, ``description`` or
``person``) on a source record, and ``excerpt_kind`` (``description`` or
``person``) on a compact record's excerpt whose text is not the source's own
words. ``public_source`` keeps only fields it names, so without carrying it a
caller could no longer tell a quote from Beakr's description of a figure.
The deprecated ``fidelity``, ``evidence_kind`` and ``anchor_status`` stay
beside it until they are removed from the contract.
"""

from __future__ import annotations

import pytest

from beakr_sdk.sources import public_source

QUOTE = "Dosing starts at 5 mg."


@pytest.mark.parametrize("kind", ["quote", "description", "person"])
def test_a_source_record_keeps_whose_words_its_excerpt_is(kind: str) -> None:
    record = public_source(
        {"name": "protocol.pdf", "evidence": {"excerpt": QUOTE, "excerpt_kind": kind}}
    )
    assert record["evidence"] == {"excerpt": QUOTE, "excerpt_kind": kind}


def test_the_deprecated_trust_fields_stay_beside_it() -> None:
    record = public_source(
        {
            "name": "protocol.pdf",
            "evidence": {
                "excerpt": QUOTE,
                "excerpt_kind": "quote",
                "fidelity": "verbatim",
                "evidence_kind": "quote",
                "anchor_status": "anchored",
            },
        }
    )
    assert record["evidence"] == {
        "excerpt": QUOTE,
        "excerpt_kind": "quote",
        "fidelity": "verbatim",
        "evidence_kind": "quote",
        "anchor_status": "anchored",
    }


def test_a_kind_the_contract_does_not_name_is_left_out() -> None:
    record = public_source(
        {"name": "memo.pdf", "evidence": {"excerpt": QUOTE, "excerpt_kind": "guess"}}
    )
    assert record["evidence"] == {"excerpt": QUOTE}


def test_an_engine_without_the_field_gains_nothing() -> None:
    record = public_source({"name": "memo.pdf", "evidence": {"excerpt": QUOTE}})
    assert "excerpt_kind" not in record["evidence"]


def test_a_compact_records_excerpt_keeps_its_kind_only_when_not_a_quote() -> None:
    record = public_source(
        {
            "name": "results.pdf",
            "excerpts": [
                {"citation_key": "artifact:a-u1", "text": QUOTE},
                {
                    "citation_key": "artifact:a-u2",
                    "text": "A bar chart of dose by week.",
                    "excerpt_kind": "description",
                },
                {
                    "citation_key": "artifact:a-u3",
                    "text": "Per Dana, the trial ended.",
                    "excerpt_kind": "person",
                },
                # A quote carries no kind on an excerpt; one sent anyway is dropped.
                {"citation_key": "artifact:a-u4", "text": QUOTE, "excerpt_kind": "quote"},
            ],
        }
    )
    assert record["excerpts"] == [
        {"citation_key": "artifact:a-u1", "text": QUOTE},
        {
            "citation_key": "artifact:a-u2",
            "text": "A bar chart of dose by week.",
            "excerpt_kind": "description",
        },
        {
            "citation_key": "artifact:a-u3",
            "text": "Per Dana, the trial ended.",
            "excerpt_kind": "person",
        },
        {"citation_key": "artifact:a-u4", "text": QUOTE},
    ]
