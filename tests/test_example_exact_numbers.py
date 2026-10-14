"""``examples/exact_numbers.py`` computes what the guide says it computes.

The "Exact numbers from original files" guide in Beakr's developer docs embeds this
example. These tests run it against a mocked API and real CSV bytes, so the
guide cannot describe a recipe that does not work: the card's file sources are
found by ``artifact_id``, the originals are downloaded (not the card's summary),
the arithmetic is exact, non-file sources and non-CSV files are left out, and
the proposed fact cites the files it came from by key and ``source_ref``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from beakr_sdk import Beakr
from tests._engine import engine_checkout

API = "https://api.test"
BUCKET = "https://files.test"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "exact_numbers.py"
GUIDE = "docs/developers/guides/exact-numbers-from-originals.md"

PAGE_ID = "11111111-1111-1111-1111-111111111111"
RUN_1 = "22222222-2222-2222-2222-222222222222"
RUN_2 = "33333333-3333-3333-3333-333333333333"
PROTOCOL = "44444444-4444-4444-4444-444444444444"

# Values chosen so binary floating point gets the mean wrong: the float mean of
# these six is 0.35000000000000003; the exact mean is 0.35. Run 2 starts with a
# byte-order mark, as spreadsheet exports often do.
ORIGINALS = {
    RUN_1: ("spr_run_1.csv", b"sample,kd_nM\nA,0.1\nB,0.2\nC,\n"),
    RUN_2: ("spr_run_2.csv", b"\xef\xbb\xbfsample,kd_nM\nD,0.3\nE,0.4\nF,0.5\nG,0.6\n"),
    PROTOCOL: ("SPR protocol.pdf", b"%PDF-1.7 not a table"),
}


def _load():
    spec = importlib.util.spec_from_file_location("exact_numbers", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # A dataclass resolves its annotations through sys.modules while the module runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def example():
    return _load()


@pytest.fixture
def beakr():
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        yield client


def _source(artifact_id: str | None, title: str, key: str, **extra) -> dict:
    return {
        "source_type": "artifact" if artifact_id else "web",
        "source_id": artifact_id or "https://example.org/kx2",
        "artifact_id": artifact_id,
        "source_title": title,
        "key": key,
        "source_ref": f"beakr-source:v1:{key}",
        "stance": "supports",
        **extra,
    }


def _mock_api(originals=ORIGINALS):
    by_title = respx.get(f"{API}/v1/knowledge/wiki/pages/by-title").mock(
        return_value=httpx.Response(200, json={"id": PAGE_ID, "title": "KX2 binding assay"})
    )
    sources = [
        _source(RUN_1, "spr_run_1.csv", "artifact:run1"),
        _source(RUN_1, "spr_run_1.csv", "artifact:run1", section_id="facts"),  # cited twice
        _source(None, "KX2 vendor page", "web:kx2"),
        _source(RUN_2, "spr_run_2.csv", "artifact:run2"),
        _source(PROTOCOL, "SPR protocol.pdf", "artifact:protocol"),
    ]
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE_ID}/sources").mock(
        return_value=httpx.Response(200, json={"data": sources, "has_more": False})
    )

    def artifact(request, artifact_id):
        name, _ = originals[artifact_id]
        return httpx.Response(
            200,
            json={
                "id": artifact_id,
                "title": name,
                "original_filename": name,
                "current_version_id": f"v-{artifact_id}",
            },
        )

    respx.get(url__regex=rf"{API}/v1/artifacts/(?P<artifact_id>[0-9a-f-]+)$").mock(
        side_effect=artifact
    )

    def signed(request):
        artifact_id = request.url.path.split("/")[-2].removeprefix("v-")
        return httpx.Response(200, json={"url": f"{BUCKET}/{artifact_id}?sig=1"})

    respx.get(url__regex=rf"{API}/v1/artifacts/versions/[^/]+/download-url").mock(
        side_effect=signed
    )
    downloads = respx.get(url__regex=rf"{BUCKET}/(?P<artifact_id>[0-9a-f-]+)").mock(
        side_effect=lambda request, artifact_id: httpx.Response(
            200, content=originals[artifact_id][1]
        )
    )
    writes = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Staged proposal pr-9."}],
                "outcome": {"status": "ok"},
            },
        )
    )
    return by_title, downloads, writes


@respx.mock
def test_the_mean_is_computed_exactly_from_the_original_csvs(example, beakr) -> None:
    by_title, downloads, _writes = _mock_api()

    result = example.exact_mean(beakr, "KX2 binding assay", "kd_nM", project_id="proj-1")

    assert result.value == Decimal("0.35")
    assert sum([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]) / 6 != 0.35  # what float arithmetic would say
    assert result.count == 6  # the blank cell is skipped, not counted as zero
    assert [cited.title for cited in result.files] == ["spr_run_1.csv", "spr_run_2.csv"]
    assert by_title.calls[0].request.url.params["title"] == "KX2 binding assay"
    assert by_title.calls[0].request.url.params["project_id"] == "proj-1"
    # Each CSV is downloaded once, though the card cites run 1 twice; the PDF
    # and the web page are never fetched.
    fetched = sorted(call.request.url.path.strip("/") for call in downloads.calls)
    assert fetched == sorted([RUN_1, RUN_2])


@respx.mock
def test_the_proposed_fact_cites_the_files_it_was_computed_from(example, beakr) -> None:
    _by_title, _downloads, writes = _mock_api()
    result = example.exact_mean(beakr, "KX2 binding assay", "kd_nM")

    staged = example.propose_fact(beakr, "KX2 binding assay", "kd_nM", result, scope="proj-1")

    body = json.loads(writes.calls[0].request.content)
    assert body["action"] == "add_fact" and body["page"] == "KX2 binding assay"
    assert body["attribute"] == "kd_nM" and body["value"] == "0.35"
    assert body["evidence"] == "{{artifact:run1}} {{artifact:run2}}"
    assert body["citations"] == [
        {"key": "artifact:run1", "source_ref": "beakr-source:v1:artifact:run1"},
        {"key": "artifact:run2", "source_ref": "beakr-source:v1:artifact:run2"},
    ]
    assert "Mean of 6 values" in body["rationale"] and body["scope"] == "proj-1"
    assert staged["content"][0]["text"] == "Staged proposal pr-9."


@respx.mock
def test_a_cell_that_is_not_a_number_is_refused_not_skipped(example, beakr) -> None:
    broken = dict(ORIGINALS)
    broken[RUN_2] = ("spr_run_2.csv", b"sample,kd_nM\nD,0.3\nE,n/a\n")
    _mock_api(broken)

    with pytest.raises(ValueError, match="spr_run_2.csv, line 3: 'n/a'"):
        example.exact_mean(beakr, "KX2 binding assay", "kd_nM")


@respx.mock
def test_a_missing_column_names_the_columns_there_are(example, beakr) -> None:
    _mock_api()

    with pytest.raises(KeyError, match="kd_nM"):
        example.exact_mean(beakr, "KX2 binding assay", "kd_pM")


@respx.mock
def test_a_card_citing_no_csv_says_so(example, beakr) -> None:
    _mock_api()

    with pytest.raises(LookupError, match="cites no .xlsx file"):
        example.exact_mean(beakr, "KX2 binding assay", "kd_nM", suffix=".xlsx")


@respx.mock
def test_the_command_line_prints_the_value_and_its_files(example, monkeypatch, capsys) -> None:
    _mock_api()
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("BEAKR_BASE_URL", API)

    assert example.main(["KX2 binding assay", "kd_nM", "--project-id", "proj-1"]) == 0

    out = capsys.readouterr().out
    assert "mean kd_nM = 0.35 (n=6)" in out
    assert "from spr_run_1.csv (beakr-source:v1:artifact:run1)" in out


def test_the_guide_embeds_this_example_unchanged() -> None:
    # The guide is optional: the engine's documented-core cut removed it, and an
    # example outlives its guide. When the engine has it, it must embed this file.
    guide = engine_checkout("docs/api/public-openapi.json").parents[2] / GUIDE
    if not guide.exists():
        pytest.skip(f"the engine has no {GUIDE}")
    blocks = re.findall(r"```python\n(.*?)```", guide.read_text(), flags=re.DOTALL)
    assert EXAMPLE.read_text() in blocks
