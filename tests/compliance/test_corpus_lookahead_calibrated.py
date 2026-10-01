"""The corpus's lookahead tests test lookahead (activity plan P1.7, gate 3).

Each is run on bc with lookahead switched off (``PSSToScenarioPass(
lookahead=False)``, the P1.6 calibration switch), over the seeds the corpus
runs it with. It must fail on at least one: a lookahead test that a greedy
tool passes on all its seeds proves nothing about lookahead, whatever the
checker says.
"""
import pytest

from pss_corpus import discover

from conftest import CORPUS

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

LOOKAHEAD = ["act.lookahead.001", "act.lookahead.sub.001", "act.constraint.001",
             "act.symbol.001"]

TESTS = {t.id: t for t in discover(f"{CORPUS}/compliance")}


@pytest.mark.parametrize("tid", LOOKAHEAD)
def test_a_tool_without_lookahead_fails_on_some_corpus_seed(tid):
    import pssc
    from pssc.ast2ir import AstToIrTranslator
    from zuspec.ir.core.xf import PSSToScenarioPass
    from zuspec.be.bc.lower import lower_module
    from zuspec.be.bc.interp import NativeBlobBackend, run_model
    from zuspec.be.bc.interp.activation import ScopeUnsatError

    t = TESTS[tid]
    parser = pssc.Parser()
    parser.parse(t.sources)
    ctx = AstToIrTranslator().translate(parser.link(), files=parser.file_map)
    assert not ctx.errors
    comp, _, action = t.root.rpartition("::")
    module = PSSToScenarioPass(root=comp, exports=[action], lookahead=False).lower(ctx)
    model = lower_module(module, entry_action=action, solve_unconstrained=True)
    unsat = 0
    for seed in t.seeds:
        try:
            run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                      out=lambda _: None)
        except ScopeUnsatError:
            unsat += 1
    assert unsat > 0, f"{tid}: no lookahead, yet all {len(t.seeds)} seeds solve"
