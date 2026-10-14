"""Compute an exact number from the original files a Knowledge card cites.

The pattern from the "Exact numbers from original files" guide
(the ``exact-numbers-from-originals`` guide in Beakr's developer docs):
Knowledge tells you where a number lives; the number itself comes from the
original file, computed in code.

1. Find the card and list the sources it cites. A file source carries the
   file's ``artifact_id``, its citation ``key`` and an opaque ``source_ref``.
2. Download each cited original (the exact bytes that were uploaded).
3. Compute the value from those bytes, here the mean of one CSV column, with
   decimal arithmetic so nothing is lost to floating point.
4. Propose the result back onto the card, citing the files it came from. The
   write is a proposal that a person accepts.

Run it with ``BEAKR_API_KEY`` set (``kb:read``, plus ``kb:write`` to propose the
result)::

    python examples/exact_numbers.py "KX2 binding assay" kd_nM --project-id <project id>
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from beakr_sdk import Beakr
from beakr_sdk.resources.knowledge import result_text


@dataclass
class CitedFile:
    """One original file a card cites."""

    artifact_id: str
    title: str
    key: str
    source_ref: str


@dataclass
class ExactValue:
    """A value computed from original files, with what it was computed from."""

    value: Decimal
    count: int
    files: list[CitedFile]


def cited_files(beakr: Beakr, card: str, *, project_id: str | None = None) -> list[CitedFile]:
    """The uploaded files a card cites, once each, in the order the card lists them."""
    page = beakr.knowledge.pages.by_title(card, project_id=project_id)
    files: dict[str, CitedFile] = {}
    for source in beakr.knowledge.pages.sources(page["id"]):
        artifact_id = (source.get("location") or {}).get("file_id")
        if source.get("source_kind") != "artifact" or not artifact_id:
            continue  # a web page, a message or another card: not a file to recompute from
        files.setdefault(
            str(artifact_id),
            CitedFile(
                artifact_id=str(artifact_id),
                title=str(source.get("name") or artifact_id),
                key=str(source.get("citation_key") or ""),
                source_ref=str(source.get("source_ref") or ""),
            ),
        )
    return list(files.values())


def column_values(data: bytes, column: str, *, source: str = "file") -> list[Decimal]:
    """Every number in one CSV column, exactly as written. Blank cells are skipped.

    A cell that is not a finite number (text, ``NaN``, ``Infinity``) is refused:
    a missing or impossible measurement must never become part of a cited result.
    """
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    if reader.fieldnames is None or column not in reader.fieldnames:
        raise KeyError(f"No column {column!r}; the columns are {reader.fieldnames}")
    values: list[Decimal] = []
    for line, row in enumerate(reader, start=2):
        cell = (row.get(column) or "").strip()
        if not cell:
            continue
        try:
            value = Decimal(cell)
        except InvalidOperation as error:
            raise ValueError(
                f"{source}, line {line}: {cell!r} in {column!r} is not a number"
            ) from error
        if not value.is_finite():
            raise ValueError(
                f"{source}, line {line}: {cell!r} in {column!r} is not a finite number"
            )
        values.append(value)
    return values


def exact_mean(
    beakr: Beakr,
    card: str,
    column: str,
    *,
    project_id: str | None = None,
    suffix: str = ".csv",
) -> ExactValue:
    """The mean of ``column`` across every CSV the card cites, from the original files."""
    used: list[CitedFile] = []
    values: list[Decimal] = []
    with tempfile.TemporaryDirectory() as directory:
        for cited in cited_files(beakr, card, project_id=project_id):
            if not cited.title.lower().endswith(suffix):
                continue
            path = beakr.files.download(cited.artifact_id, Path(directory))
            values += column_values(Path(path).read_bytes(), column, source=cited.title)
            used.append(cited)
    if not values:
        raise LookupError(f"{card!r} cites no {suffix} file with values in {column!r}")
    return ExactValue(value=sum(values, Decimal(0)) / len(values), count=len(values), files=used)


def propose_fact(
    beakr: Beakr, card: str, attribute: str, result: ExactValue, *, scope: str | None = None
) -> Any:
    """Stage the value as a Facts row on the card, citing each file it came from."""
    return beakr.knowledge.add_fact(
        card,
        attribute=attribute,
        value=f"{result.value.normalize()}",
        evidence=" ".join(f"{{{{{cited.key}}}}}" for cited in result.files),
        citations=[{"key": cited.key, "source_ref": cited.source_ref} for cited in result.files],
        rationale=(
            f"Mean of {result.count} values computed from the original files: "
            + ", ".join(cited.title for cited in result.files)
        ),
        scope=scope,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("card", help="The card's title")
    parser.add_argument("column", help="The CSV column to average")
    parser.add_argument("--project-id", help="The card's project id")
    parser.add_argument("--propose", action="store_true", help="Propose the result as a fact")
    args = parser.parse_args(argv)

    with Beakr() as beakr:
        result = exact_mean(beakr, args.card, args.column, project_id=args.project_id)
        print(f"mean {args.column} = {result.value.normalize()} (n={result.count})")
        for cited in result.files:
            print(f"  from {cited.title} ({cited.source_ref})")
        if args.propose:
            staged = propose_fact(beakr, args.card, args.column, result, scope=args.project_id)
            print(result_text(staged))
    return 0


if __name__ == "__main__":
    sys.exit(main())
