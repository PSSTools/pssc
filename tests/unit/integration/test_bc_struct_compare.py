"""``==`` and ``!=`` between two struct attributes, on bc.

``a == b`` holds when every scalar of ``a`` equals the same scalar of ``b``;
``a != b`` when any differs. ir-core expands the comparison where references
are resolved to slots (``constraints._aggregate_compare``), so it works in an
action's own problem and across the action tree (``prep.io == io``: a
parent's attribute and its child's).
"""
import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model
from zuspec.ir.core.xf.validate import UnsupportedConstructError

from .test_activity_bc_runs import _lower
from .test_lookahead import _HDR

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_TYPES = """
    struct in_s { rand bit[2] p; rand bit[2] q; }
    struct s_s { rand bit[2] a; rand bit[3] b; rand in_s n; }
    struct t_s { rand bit[2] a; rand bit[3] c; }
"""


def _runs(src, seeds=range(100)):
    model = _lower(_HDR + _TYPES + src + "\n}\n")
    out = []
    for seed in seeds:
        lines = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(), out=lines.append)
        out.append([int(v) for ln in lines for v in ln.split()])
    return out


_PRINT = 'message(NONE, "%u %u %u %u", s.a, s.b, s.n.p, s.n.q);'


def test_equal_within_one_action():
    got = _runs("""
    action T {
        rand s_s s, r;
        constraint s == r;
        exec body { %s message(NONE, "%%u %%u %%u %%u", r.a, r.b, r.n.p, r.n.q); }
    }""".replace("%s", _PRINT).replace("%%", "%"))
    for v in got:
        assert v[:4] == v[4:]
    assert len({tuple(v[:4]) for v in got}) > 20


def test_not_equal_is_any_scalar_differing():
    got = _runs("""
    action T {
        rand s_s s, r;
        constraint s.a == r.a; constraint s.b == r.b; constraint s.n.p == r.n.p;
        constraint s != r;
        exec body { %s message(NONE, "%%u %%u %%u %%u", r.a, r.b, r.n.p, r.n.q); }
    }""".replace("%s", _PRINT).replace("%%", "%"))
    for v in got:
        assert v[:3] == v[4:7] and v[3] != v[7]


def test_equal_across_the_action_tree():
    """The model's `prep.io == io`: the child holds the parent's values."""
    got = _runs("""
    action C { rand s_s s; exec body { %s } }
    action T {
        rand s_s s;
        constraint s.b == 5;
        C c;
        constraint c.s == s;
        activity { c; }
        exec post_solve { %s }
    }""".replace("%s", _PRINT))
    for v in got:
        assert v[:4] == v[4:] and v[1] == 5
    assert len({tuple(v) for v in got}) > 10


def test_a_nested_struct():
    got = _runs("""
    action T {
        rand s_s s, r;
        constraint s.n == r.n;
        exec body { message(NONE, "%u %u %u %u", s.n.p, s.n.q, r.n.p, r.n.q); }
    }""")
    assert all(v[:2] == v[2:] for v in got)


def test_different_struct_types_are_refused():
    with pytest.raises(UnsupportedConstructError, match="different types"):
        _runs("""
        action T {
            rand s_s s; rand t_s t;
            constraint s == t;
            exec body { message(NONE, "%u", s.a); }
        }""", seeds=range(1))
