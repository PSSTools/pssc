"""bc compiles each solve problem once per run (``interp/solve_cache.py``).

A cone's problem, or an action's own, is compiled to a dv-solve context the
first time it is solved; later solves pin and solve it between a checkpoint
and a restore. Reuse must be invisible: a run gives exactly the log it gives
when every solve compiles afresh (``SolveCache(capacity=0)``). Every solve has
a restart budget; exhausting it is a located error, never a hang.
"""
from typing import List

import pytest

from zuspec.be.bc.interp import (NativeBlobBackend, SolveBudgetError, SolveCache,
                                 run_model)

from .test_activity_bc_runs import _lower
from .test_lookahead import _EX180, _EX183, _EX184, _HDR, _PARENT

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

SEEDS = range(200)

#: an action with its own problem (no cone), traversed many times; a cone
#: traversed in a loop; and both in one run
_REPEAT = """
    action B {
        rand bit[8] x, y;
        constraint { x < y; x + y == 100; }
        exec body { message(NONE, "%u %u", x, y); }
    }
    action T { activity { repeat (20) { do B; do A; } } }"""

_LOOPED_CONE = """
    action P {
        A a, b;
        constraint { a.val + b.val == 12; a.val < b.val; }
        activity { a; b; }
    }
    action T { activity { repeat (10) { do P; do B; } } }
    action B {
        rand bit[8] x; constraint { x in [3, 5, 7]; }
        exec body { message(NONE, "%u", x); }
    }"""

MODELS = {"ex183": _EX183, "ex184": _EX184, "ex180": _EX180, "parent": _PARENT,
          "repeat": _REPEAT, "looped_cone": _LOOPED_CONE}


def _log(model, seed, cache) -> List[str]:
    lines: List[str] = []
    run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
              out=lines.append, solve_cache=cache)
    return lines


@pytest.mark.parametrize("name", sorted(MODELS))
def test_reuse_gives_the_values_a_fresh_compile_gives(name):
    model = _lower(_HDR + MODELS[name] + "\n}\n")
    for seed in SEEDS:
        assert _log(model, seed, SolveCache()) == _log(model, seed, SolveCache(capacity=0)), \
            "seed %d" % seed


def test_a_problem_is_compiled_once_per_run():
    """20 traversals of B (its own problem) and of A: two compiles."""
    model = _lower(_HDR + _REPEAT + "\n}\n")
    cache = SolveCache()
    _log(model, 1, cache)
    assert cache.compiles == 2
    assert cache.hits == 38


def test_a_cone_is_compiled_once_per_set_of_constraints_in_force():
    """Each P's cone is solved for a, then for b, with different constraints
    in force; ten iterations reuse those two problems (and B's own). A solve
    the cone's last solution answers (B6e) needs no problem at all."""
    model = _lower(_HDR + _LOOPED_CONE + "\n}\n")
    cache = SolveCache()
    _log(model, 1, cache)
    assert cache.compiles <= 3
    assert cache.hits + cache.reused >= 27


def test_a_shared_cache_spans_runs():
    model = _lower(_HDR + _REPEAT + "\n}\n")
    cache = SolveCache()
    for seed in range(5):
        _log(model, seed, cache)
    assert cache.compiles == 2


def test_the_cache_is_bounded():
    """Capacity 1 with two problems alternating: every solve recompiles,
    and the values are still the fresh-compile values."""
    model = _lower(_HDR + _REPEAT + "\n}\n")
    cache = SolveCache(capacity=1)
    assert _log(model, 3, cache) == _log(model, 3, SolveCache(capacity=0))
    assert len(cache) == 1
    assert cache.compiles == 40


#: ten values, all different, from nine: no solution, and nothing short of
#: search shows it
_VARS = ["v%d" % i for i in range(10)]


def _pigeons(path):
    return " ".join(["%s%s < 9;" % (path, v) for v in _VARS]
                    + ["%s%s != %s%s;" % (path, a, path, b)
                       for i, a in enumerate(_VARS) for b in _VARS[i + 1:]])


_HARD = """
    action T {
        rand bit[8] %s;
        constraint { %s }
        exec body { message(NONE, "%%u", v0); }
    }""" % (", ".join(_VARS), _pigeons(""))

_HARD_CONE = """
    action F {
        rand bit[8] %s;
        exec body { message(NONE, "%%u", v0); }
    }
    action T {
        F f;
        constraint { %s }
        activity { f; }
    }""" % (", ".join(_VARS), _pigeons("f."))


@pytest.mark.parametrize("src,where", [(_HARD, "'T'"), (_HARD_CONE, "'f'")])
def test_an_exhausted_budget_is_a_located_error(src, where):
    model = _lower(_HDR + src + "\n}\n")
    with pytest.raises(SolveBudgetError) as ei:
        run_model(model, seed=1, solve_backend=NativeBlobBackend(),
                  out=lambda _: None, max_restarts=1)
    msg = str(ei.value)
    assert where in msg
    assert "gave up after 1 restarts" in msg


def test_a_budget_belongs_to_one_cache():
    model = _lower(_HDR + _REPEAT + "\n}\n")
    with pytest.raises(ValueError):
        run_model(model, solve_cache=SolveCache(), max_restarts=5,
                  out=lambda _: None)
