"""PSS source through the bc path, end to end.

parse -> IR -> pss_lower -> ZBC -> serialize/deserialize -> VM, with dv-solve
doing the constraint solving, entered through `pssc.testing.bc_session`. This is the
stack pssunit is moving to, so what it covers is the seam between packages
rather than any one package's internals -- the failures it catches are the ones
each project's own suite cannot see.

Skipped cleanly where the bc backend is not importable.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("zuspec.be.bc")

import pssc.driver as drv                                       # noqa: E402
from pssc.testing import bc_session                             # noqa: E402


def _lower(tmp_path, src, root="dut_c"):
    p = tmp_path / "d.pss"
    p.write_text(src)
    return bc_session.lower(drv.translate([str(p)]), root)


def _run(tmp_path, src, action, fields=(), root="dut_c", seed=1):
    model = _lower(tmp_path, src, root)
    lines = []
    result, _ = bc_session.run(model, bc_session.entries(model)[action], seed,
                               field_names=fields, out=lines.append)
    return "".join(l + "\n" for l in lines), result


def _fields(result, names):
    return {n: result.obj.get_field_name(n) for n in names}


_WITH_FIELD = """
import std_pkg::*;
component dut_c {
    action hello_a {
        rand bit[8] n;
        constraint { n in [7..7]; }
        exec body {
            message(LOW, "n=%d", n);
        }
    }
}
"""


def test_a_solved_field_reaches_the_output(tmp_path):
    """The whole chain in one assertion.

    dv-solve solves `n in [7..7]`, writes the result to the object slot,
    LD_FIELD reads it back, and `message` formats it. A break anywhere along
    that path changes this string.
    """
    out, result = _run(tmp_path, _WITH_FIELD, "hello_a", fields=["n"])
    assert out == "n=7\n"
    assert _fields(result, ["n"]) == {"n": 7}


def test_the_constraint_is_actually_enforced(tmp_path):
    """Not just "a value appeared" -- the *constrained* value appeared.

    Run several seeds: an unconstrained or dropped constraint would vary.
    """
    for seed in range(5):
        out, _ = _run(tmp_path, _WITH_FIELD, "hello_a", fields=["n"], seed=seed)
        assert out == "n=7\n", f"seed={seed}"


def test_a_field_reference_survives_lowering(tmp_path):
    """Field refs in exec code used to reach bc as ExprAttribute and fail.

    Constraints were resolved to ExprRefField and exec blocks were not, so `n`
    worked inside `constraint {}` and not inside `exec body`.
    """
    from zuspec.be.bc.model import Op
    model = _lower(tmp_path, _WITH_FIELD)
    ops = [i.op for c in model.coros for i in c.code]
    assert Op.LD_FIELD in ops


def test_output_is_captured_rather_than_printed(tmp_path, capsys):
    """The `out` callback gives per-test capture without a stdout redirect.

    ZBC formats the text and hands it to the callback, so nothing of the test's output
    reaches the real stdout at any point.
    """
    out, _ = _run(tmp_path, _WITH_FIELD, "hello_a", fields=["n"])
    assert out == "n=7\n"
    assert capsys.readouterr().out == ""


def test_escapes_are_decoded_once_along_the_whole_chain(tmp_path):
    r"""`\n` becomes a newline, `\\n` stays a backslash and an `n`.

    Guards against a decode reappearing in a back end now that the parser owns
    it: a second pass would turn the `\\n` case into a newline too.
    """
    src = """
import std_pkg::*;
component dut_c {
    action esc_a {
        exec body {
            message(LOW, "tab:\\there");
            message(LOW, "kept: a\\\\nb");
        }
    }
}
"""
    out, _ = _run(tmp_path, src, "esc_a")
    assert out == "tab:\there\nkept: a\\nb\n"


_WITH_CALL = """
import std_pkg::*;
component helper_c {
    function void doit(int a) {
        message(LOW, "doit: %d", a);
    }
}
component dut_c {
    helper_c h;
    action call_a {
        exec body {
            message(LOW, "before");
            comp.h.doit(30);
            message(LOW, "after");
        }
    }
}
"""


def test_a_user_function_is_called_with_its_argument(tmp_path):
    """A PSS function with a body is not an import -- no host supplies it.

    It lowers to a coroutine whose parameters are its leading frame locals, and
    INVOKE seeds them from the caller's registers.
    """
    out, _ = _run(tmp_path, _WITH_CALL, "call_a")
    assert out == "before\ndoit: 30\nafter\n"


def test_the_callee_runs_to_completion_before_the_next_statement(tmp_path):
    """Ordering is the observable part: `after` must follow `doit`, not race it."""
    out, _ = _run(tmp_path, _WITH_CALL, "call_a")
    assert out.index("doit: 30") < out.index("after")


def test_arguments_are_passed_by_value(tmp_path):
    """Two calls with different arguments must not share a value."""
    src = """
import std_pkg::*;
component helper_c {
    function void doit(int a) { message(LOW, "v=%d", a); }
}
component dut_c {
    helper_c h;
    action two_a {
        exec body {
            comp.h.doit(11);
            comp.h.doit(22);
        }
    }
}
"""
    out, _ = _run(tmp_path, src, "two_a")
    assert out == "v=11\nv=22\n"


_WITH_STATE = """
import std_pkg::*;
component counter_c {
    int n;
    function void bump(int by) {
        n = n + by;
        message(LOW, "n=%d", n);
    }
}
component dut_c {
    counter_c a;
    counter_c b;
    action t {
        exec body {
            comp.a.bump(5);
            comp.a.bump(7);
            comp.b.bump(1);
        }
    }
}
"""


def test_component_state_persists_across_calls(tmp_path):
    """A read-modify-write of component state, surviving between calls.

    This is what the `rt_c` ledger needs: counters that accumulate rather than
    resetting each time a function is entered.
    """
    out, _ = _run(tmp_path, _WITH_STATE, "t")
    assert out.startswith("n=5\nn=12\n")


def test_two_instances_of_a_component_do_not_share_state(tmp_path):
    """The property that makes this *elaboration* rather than type-keyed storage.

    `a` and `b` are the same component type. Keying storage by type -- the
    obvious shortcut -- would have `b` continue `a`'s count and print 13.
    """
    out, _ = _run(tmp_path, _WITH_STATE, "t")
    assert out == "n=5\nn=12\nn=1\n"


def test_the_callee_binds_to_its_own_component_not_the_callers(tmp_path):
    """A function on a child component must see the child's fields.

    Inheriting the caller's object would read the root's slot 0 -- a plausible
    number from the wrong object, which is worse than an error.
    """
    src = """
import std_pkg::*;
component inner_c {
    int v;
    function void show() { message(LOW, "inner=%d", v); }
}
component dut_c {
    int v;
    inner_c c;
    action t {
        exec body { comp.c.show(); }
    }
}
"""
    out, _ = _run(tmp_path, src, "t")
    assert out == "inner=0\n"


def test_verbosity_suppression_happens_in_the_runtime(tmp_path):
    """A HIGH message is dropped against the default MEDIUM threshold."""
    src = """
import std_pkg::*;
component dut_c {
    action quiet_a {
        exec body {
            message(HIGH, "hidden");
            message(LOW, "shown");
        }
    }
}
"""
    out, _ = _run(tmp_path, src, "quiet_a")
    assert out == "shown\n"


# --- the example the design's definition of done names ----------------------

#: The example lives in the pssunit project, not in pssc; point
#: PSSUNIT_EXAMPLES at its examples/ directory to run this test.
EXAMPLE = Path(os.environ.get("PSSUNIT_EXAMPLES", "/nonexistent")) / "wb_dma"


@pytest.mark.skipif(not EXAMPLE.is_dir(),
                    reason="examples/wb_dma not present (set PSSUNIT_EXAMPLES)")
def test_the_wb_dma_example_runs_on_the_bc_path():
    """Both tests of the tracked example, through the whole stack.

    This is the thing every gap so far has been measured against: builtins,
    field references in exec code, and calling a component's `target function`
    with a body. It exercises a real multi-file model rather than a fixture.
    """
    from pssc.testing.discover import discover
    from pssc.testing.sources import all_sources

    ctx = drv.translate(all_sources([str(EXAMPLE)]))
    items = discover(ctx)
    assert len(items) == 2

    model = bc_session.lower(ctx, items[0].comp)
    out = {}
    for it in items:
        name = it.qname.split("::")[-1]
        lines = []
        bc_session.run(model, bc_session.entries(model)[name], 1,
                       out=lines.append)
        out[name] = "".join(l + "\n" for l in lines)

    assert out == {
        "smoke_a":   "Hello\nHello from doit: 20\n",
        "smoke_1_a": "Hello\nHello from doit: 30\n",
    }
