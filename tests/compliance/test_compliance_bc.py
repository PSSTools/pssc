"""pss-corpus executable tier on pssc's bytecode backend (COMPLIANCE-DESIGN.md §12).

One pytest case per (test, seed). The verdict is the corpus checker's, never
pssc's: this file only runs the adapter and reads the result.

Expected failures live in ``expected/bc.toml`` -- facts about *this backend*,
which is why they are here and not in the corpus (§11.4). Each is strict: a
listed test that starts passing fails the run until its entry is removed, so the
list cannot go stale. An UNSUPPORTED verdict is only acceptable when listed.
"""
import os

import pytest

try:
    import tomllib
except ImportError:                                      # Python < 3.11
    import tomli as tomllib

from conftest import CORPUS
from pss_corpus import PASS, check_run, discover

from adapters import pssc_bc

_EXPECTED = os.path.join(os.path.dirname(__file__), "expected", "bc.toml")


def _expected():
    with open(_EXPECTED, "rb") as fp:
        return tomllib.load(fp).get("expected", {})


EXPECTED = _expected()
TESTS = list(discover(os.path.join(CORPUS, "compliance")))
CASES = [(t, s) for t in TESTS for s in t.seeds]


def test_expected_list_names_real_tests():
    ids = {t.id for t in TESTS}
    stale = sorted(set(EXPECTED) - ids)
    assert not stale, f"expected/bc.toml names tests that do not exist: {stale}"


@pytest.mark.parametrize("test,seed", CASES, ids=[f"{t.id}-s{s}" for t, s in CASES])
def test_bc(tmp_path, test, seed):
    pssc_bc.run(test.sources, test.root, seed, str(tmp_path))
    v = check_run(test, str(tmp_path), seed=seed)
    exp = EXPECTED.get(test.id)
    if exp is None:
        assert v.verdict == PASS, (f"{v.verdict}: {v.reason}\n{v.detail}\n--- log ---\n"
                                   + (tmp_path / "log.txt").read_text())
        return
    want = exp.get("verdict", "FAIL")
    if v.verdict == PASS:
        pytest.fail(f"{test.id} now PASSES on bc: remove it from expected/bc.toml "
                    f"({exp.get('reason', '')})")
    assert v.verdict == want, f"expected {want} ({exp.get('reason')}), got {v.verdict}: {v.reason}"
    pytest.xfail(f"{want}: {exp.get('reason', '')}")
