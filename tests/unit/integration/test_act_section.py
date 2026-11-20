"""P4.10a/b2: a model's activation tables, cone blobs included, are in its
image (ACT section)."""

import pytest

from zuspec.be.bc.format import spec
from zuspec.be.bc.format.reader import read_image
from zuspec.be.bc.interp import NativeBlobBackend, run_model
from zuspec.be.bc.interp.activation import ScopeUnsatError
from zuspec.be.bc.interp.solve_cache import SolveCache
from zuspec.be.bc.model import ZbcModel

from . import act_roundtrip
from .test_activity_bc_runs import _lower
from .test_bc_cone_reuse import _PIPE, _STATE

_ACT = spec.sec_kind("ZBC_SEC_ACT")


def _kinds(model):
    return [s.kind for s in read_image(model.to_bytes()).sections]


def test_an_image_carries_its_activations():
    for src in (_PIPE.replace("@BODY@", "action T { activity { repeat (4) { do P; } } }"),
                _STATE):
        model = _lower(src)
        assert model.activations and _kinds(model).count(_ACT) == 1
        back = ZbcModel.from_bytes(model.to_bytes())
        act_roundtrip.check(model.activations, back.image_activations)


def test_an_image_without_activations_has_no_act_section():
    # A root action is an activation of one node; a model of functions only
    # has none.
    model = _lower("""import std_pkg::*;
component pss_top {
    action T { rand bit[4] x; rand bit[4] y; exec body { y = x; } }
}
""")
    model.activations = {}
    assert _ACT not in _kinds(model)
    assert ZbcModel.from_bytes(model.to_bytes()).image_activations == {}


def _lines(model, seed):
    lines = []
    run_model(model, seed=seed, solve_backend=NativeBlobBackend(), out=lines.append,
              solve_cache=SolveCache())
    return lines


def test_a_model_read_from_its_image_runs_its_activities():
    # P4.10b2: the parsed tables have no constraint expressions; they solve
    # from the cone blobs ACT carries, with the same values.
    for src in (_PIPE.replace("@BODY@", "action T { activity { repeat (4) { do P; } } }"),
                _STATE):
        model = _lower(src)
        back = ZbcModel.from_bytes(model.to_bytes())
        for seed in range(5):
            assert _lines(back, seed) == _lines(model, seed), seed


def test_an_unsat_message_from_the_image_names_the_constraints():
    model = _lower("""import std_pkg::*;
component pss_top {
    action S { rand bit[8] v; constraint v > 10; }
    action T { activity { do S with { v < 5; }; } }
}
""")
    back = ZbcModel.from_bytes(model.to_bytes())
    for m in (model, back):
        with pytest.raises(ScopeUnsatError, match=r"in force: #0\.v > 10; #0\.v < 5; with"):
            _lines(m, 0)
