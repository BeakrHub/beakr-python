"""An exact-number guide must not turn non-finite CSV values into cited facts."""

import importlib.util
from pathlib import Path

import pytest
import respx

from beakr_sdk import Beakr


def _helpers():
    path = Path(__file__).with_name("test_example_exact_numbers.py")
    spec = importlib.util.spec_from_file_location("exact_number_review_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@respx.mock
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_csv_cells_are_refused_before_a_fact_is_proposed(value):
    example_tests = _helpers()
    example = example_tests._load()
    originals = dict(example_tests.ORIGINALS)
    originals[example_tests.RUN_2] = ("spr_run_2.csv", f"sample,kd_nM\nD,{value}\n".encode())
    _by_title, _downloads, writes = example_tests._mock_api(originals)
    with Beakr(api_key="bk_live_test", base_url=example_tests.API) as client:
        with pytest.raises(ValueError):
            result = example.exact_mean(client, "KX2 binding assay", "kd_nM")
            example.propose_fact(client, "KX2 binding assay", "kd_nM", result)
    assert not writes.called
