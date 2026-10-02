"""Reusing compiled solve problems changes no corpus run (nvme-bench plan B2).

bc compiles each solve problem once per run and solves it between a
checkpoint and a restore (be-bc ``interp/solve_cache.py``). That must be
invisible: every corpus test that runs on bc gives, on each of its seeds,
exactly the log -- and the same error, if it stops with one -- that it gives
when every solve compiles afresh (``SolveCache(capacity=0)``).
"""
import pytest

from pss_corpus import discover

from conftest import CORPUS

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

TESTS = {t.id: t for t in discover(f"{CORPUS}/compliance")}


def _lower(t):
    import pssc
    from pssc.ast2ir import AstToIrTranslator
    from zuspec.ir.core.xf import PSSToScenarioPass
    from zuspec.be.bc.lower import lower_module

    try:
        parser = pssc.Parser()
        parser.parse(t.sources)
        ctx = AstToIrTranslator().translate(parser.link(), files=parser.file_map)
        if ctx.errors:
            return None
        comp, _, action = t.root.rpartition("::")
        module = PSSToScenarioPass(root=comp or None, exports=[action]).lower(ctx)
        return lower_module(module, entry_action=action, solve_unconstrained=True)
    except Exception:
        return None                    # not a run; the verdict tests cover it


def _run(model, seed, cache):
    from zuspec.be.bc.interp import NativeBlobBackend, run_model
    lines = []
    try:
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append, solve_cache=cache)
    except Exception as e:
        return lines, "%s: %s" % (type(e).__name__, e)
    return lines, None


@pytest.mark.parametrize("tid", sorted(TESTS))
def test_a_reused_problem_solves_as_a_fresh_one(tid):
    from zuspec.be.bc.interp import SolveCache

    t = TESTS[tid]
    model = _lower(t)
    if model is None:
        pytest.skip("does not lower on bc")
    for seed in t.seeds:
        assert _run(model, seed, SolveCache()) == _run(model, seed, SolveCache(capacity=0)), \
            "seed %d" % seed
