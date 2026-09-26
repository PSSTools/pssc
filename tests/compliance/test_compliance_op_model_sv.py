"""pss-corpus executable tier on pssc's op-model-sv backend (COMPLIANCE-DESIGN.md §12).

The same tests the bc suite runs, unchanged: each test's root action is made the
operation model's entry point (`--export-action`), and the generated package is
built with a small testbench under Verilator and run
(`adapters/pssc_op_model_sv.py`). The verdict is the corpus checker's.

Marked `sim`: every test is a Verilator build. Skipped when Verilator is not on
PATH -- the simulator is this backend's platform, not an input the gate checks.

Expected failures live in ``expected/op-model-sv.toml``, with the same strict
rules as ``bc.toml``: a listed test that starts passing fails the run until its
entry is removed, and one that fails with a different verdict fails too.
"""
import os

import pytest

try:
    import tomllib
except ImportError:                                      # Python < 3.11
    import tomli as tomllib

from conftest import CORPUS
from pss_corpus import PASS, check_run, discover

from adapters import pssc_op_model_sv

pytestmark = [pytest.mark.sim,
              pytest.mark.skipif(not pssc_op_model_sv.verilator(),
                                 reason="verilator not on PATH")]

_EXPECTED = os.path.join(os.path.dirname(__file__), "expected", "op-model-sv.toml")


def _expected():
    with open(_EXPECTED, "rb") as fp:
        return tomllib.load(fp).get("expected", {})


EXPECTED = _expected()
TESTS = list(discover(os.path.join(CORPUS, "compliance")))
#: One seed per test: an operation model has no randomness, so every seed of a
#: test would be the same run.
CASES = [(t, t.seeds[0]) for t in TESTS]


def test_expected_list_names_real_tests():
    ids = {t.id for t in TESTS}
    stale = sorted(set(EXPECTED) - ids)
    assert not stale, f"expected/op-model-sv.toml names tests that do not exist: {stale}"


@pytest.mark.parametrize("test,seed", CASES, ids=[t.id for t, _ in CASES])
def test_op_model_sv(tmp_path, test, seed):
    pssc_op_model_sv.run(test.sources, test.root, seed, str(tmp_path))
    v = check_run(test, str(tmp_path), seed=seed)
    exp = EXPECTED.get(test.id)
    if exp is None:
        assert v.verdict == PASS, (f"{v.verdict}: {v.reason}\n{v.detail}\n--- log ---\n"
                                   + (tmp_path / "log.txt").read_text())
        return
    want = exp.get("verdict", "FAIL")
    if v.verdict == PASS:
        pytest.fail(f"{test.id} now PASSES on op-model-sv: remove it from "
                    f"expected/op-model-sv.toml ({exp.get('reason', '')})")
    assert v.verdict == want, f"expected {want} ({exp.get('reason')}), got {v.verdict}: {v.reason}"
    pytest.xfail(f"{want}: {exp.get('reason', '')}")
