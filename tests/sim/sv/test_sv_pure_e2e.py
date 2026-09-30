"""End-to-end PSS source -> sv-pure -> Verilator (plan tasks C5b/E2, M2 exit).

Drives the real ``sv-pure`` target through ``pssc.compile`` on PSS atomic
actions and simulates the generated SV. Proves the structured IR path
(core IR -> structured constraints/translators -> SV) end-to-end with no DPI.
"""
import pytest

import pssc
from ._verilator import HAVE_VERILATOR, verilator_build_run


def _compile_pure(tmp_path, pss_text, export):
    src = tmp_path / "model.pss"
    src.write_text(pss_text)
    out = tmp_path / "out"
    res = pssc.compile(src, target="sv-pure", output_dir=str(out),
                       export_actions=[export], raise_on_error=False)
    return res, out


_SAT = """
component pss_top {
    action A {
        rand bit[8] x;
        rand bit[8] y;
        constraint c_x { x == 42; }
        constraint c_y { y in [10..20]; }
    }
}
"""

_UNSAT = """
component pss_top {
    action A {
        rand bit[8] x;
        constraint c1 { x == 1; }
        constraint c2 { x == 2; }
    }
}
"""


def test_sv_pure_used_for_atomic_subset(tmp_path):
    """The sv-pure path (not the sv-native fallback) handles an atomic action:
    the generated package has the structured class and no oo_api scaffolding."""
    res, out = _compile_pure(tmp_path, _SAT, "A")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "class pss_top__A extends zsp_action;" in gen
    assert "rand bit [7:0] x;" in gen
    assert "constraint c_x {" in gen
    # sv-native oo_api scaffolding must be absent on the pure path
    assert "export_api_impl" not in gen
    assert "factory_if" not in gen


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_atomic_satisfiable_simulates(tmp_path):
    """PSS atomic action with satisfiable constraints -> compiles + randomizes
    successfully under the SV solver."""
    res, out = _compile_pure(tmp_path, _SAT, "A")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "ZSP_PURE_DONE" in log, f"did not complete:\n{log}"


_COMPOUND = """import std_pkg::*;
component pss_top {
    action LA { exec body { message(LOW, "LEAF_A"); } }
    action LB { exec body { message(LOW, "LEAF_B"); } }
    action Seq { LA a; LB b; activity { a; b; } }
    action Par { LA a; LB b; activity { parallel { a; b; } } }
}
"""


def test_sv_pure_compound_in_subset(tmp_path):
    """Compound actions (sequence/parallel of traversals) are within the subset
    and produce an activity() task lowered from the activity graph."""
    res, out = _compile_pure(tmp_path, _COMPOUND, "Seq")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "class pss_top__Seq extends zsp_action;" in gen
    assert "pss_top__LA a;" in gen
    assert "virtual task activity();" in gen
    # a's traversal lifecycle precedes b's (sequence ordering)
    assert gen.index("a.randomize()") < gen.index("b.randomize()")


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_sequence_orders_traversals(tmp_path):
    """A `sequence { a; b; }` activity runs a before b under simulation."""
    res, out = _compile_pure(tmp_path, _COMPOUND, "Seq")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "ZSP_PURE_DONE" in log
    assert "LEAF_A" in log and "LEAF_B" in log
    assert log.index("LEAF_A") < log.index("LEAF_B"), f"wrong order:\n{log}"


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_parallel_runs_both(tmp_path):
    """A `parallel { a; b; }` activity (fork/join) runs both branches."""
    res, out = _compile_pure(tmp_path, _COMPOUND, "Par")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "fork" in gen and "join" in gen
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "LEAF_A" in log and "LEAF_B" in log and "ZSP_PURE_DONE" in log


_BUFFER = """import std_pkg::*;
component pss_top {
    buffer Data { rand bit[8] x; }
    pool Data dpool;
    bind dpool *;
    action Producer { output Data out_d; }
    action Consumer {
        input Data in_d;
        constraint c { in_d.x == 5; }
        exec body { message(NONE, "consumed x=%0d", in_d.x); }
    }
    action Test {
        Producer p;
        Consumer c;
        activity { p; c; bind p.out_d c.in_d; }
    }
}
"""


def test_sv_pure_buffer_forwarding_structure(tmp_path):
    """E7: a consumer input constraint is forwarded onto the producer's buffer
    solve (no DPI), and the buffer is bound producer->consumer."""
    res, out = _compile_pure(tmp_path, _BUFFER, "Test")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "class Data extends zsp_buffer;" in gen
    # forwarded constraint applied to the producer's buffer object directly
    assert "p.out_d.randomize() with {" in gen
    assert "(x == 5)" in gen
    # buffer bound to consumer input
    assert "c.in_d = p.out_d;" in gen
    # no DPI on the pure path
    assert "DPI" not in gen and "zsp_dpi" not in gen


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_buffer_forwarding_simulates(tmp_path):
    """E7 end-to-end: the back-propagating `in.x == 5` resolves natively — the
    consumer observes the producer-generated value 5 (a random 8-bit value would
    almost never be 5 without forwarding)."""
    res, out = _compile_pure(tmp_path, _BUFFER, "Test")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "consumed x=5" in log, f"forwarding did not back-propagate:\n{log}"
    assert "ZSP_PURE_DONE" in log


_INFER = """import std_pkg::*;
component pss_top {
    buffer Data { rand bit[8] x; }
    pool Data dpool;
    bind dpool *;
    action Producer { output Data out_d; constraint co { out_d.x == 9; } }
    action Consumer {
        input Data in_d;
        exec body { message(NONE, "got x=%0d", in_d.x); }
    }
    action Test {
        Consumer c;
        activity { c; }
    }
}
"""


def test_sv_pure_inference_structure(tmp_path):
    """E8: an unbound consumer input infers its single-candidate producer,
    synthesized ahead of the consumer and bound to it."""
    res, out = _compile_pure(tmp_path, _INFER, "Test")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    # inferred producer instance synthesized before the consumer
    assert "_inf_c_in_d" in gen
    assert "c.in_d = _inf_c_in_d.out_d;" in gen
    assert "DPI" not in gen and "zsp_dpi" not in gen


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_inference_simulates(tmp_path):
    """E8 end-to-end: inferred producer's constraint drives the consumed value."""
    res, out = _compile_pure(tmp_path, _INFER, "Test")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "got x=9" in log, f"inference did not produce the value:\n{log}"
    assert "ZSP_PURE_DONE" in log


_STATE = """import std_pkg::*;
component pss_top {
    state S { rand bit[8] v; }
    pool S sp;
    bind sp *;
    action Init { output S o; constraint c { o.v == 3; } }
    action Step {
        input S i;
        output S o;
        constraint c { o.v == i.v; }
        exec body { message(NONE, "step v=%0d", i.v); }
    }
    action Test {
        Init a;
        Step b;
        activity { a; b; bind a.o b.i; }
    }
}
"""


def test_sv_pure_state_structure(tmp_path):
    """State flow: state types extend zsp_state; coupling constraint
    (`o.v == i.v`) is applied at the output's own solve, reading the input."""
    res, out = _compile_pure(tmp_path, _STATE, "Test")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "class S extends zsp_state;" in gen
    # coupling: b.o solved reading the (already-solved) input b.i
    assert "b.o.randomize() with {" in gen
    assert "(v == b.i.v)" in gen
    assert "DPI" not in gen


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_state_coupling_simulates(tmp_path):
    """State coupling propagates the value through the chain: Init sets v=3,
    Step couples o.v==i.v and observes i.v=3."""
    res, out = _compile_pure(tmp_path, _STATE, "Test")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "step v=3" in log, f"coupling did not propagate:\n{log}"
    assert "ZSP_PURE_DONE" in log


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_state_flow_pattern_simulates(tmp_path):
    """The canonical state_flow.pss pattern lowers and simulates with no DPI."""
    import pathlib
    pss = pathlib.Path("tests/patterns/state_flow.pss").read_text()
    res, out = _compile_pure(tmp_path, pss, "do_test")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "DPI" not in gen
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "ZSP_PURE_DONE" in log


_STREAM = """import std_pkg::*;
component pss_top {
    stream Frame { rand bit[8] d; }
    pool Frame fp;
    bind fp *;
    action Prod { output Frame o; constraint c { o.d == 7; } }
    action Cons { input Frame i; exec body { message(NONE, "frame d=%0d", i.d); } }
    action Test {
        Prod p;
        Cons c;
        activity { parallel { p; c; } bind p.o c.i; }
    }
}
"""


def test_sv_pure_stream_structure(tmp_path):
    """Stream flow: a fork + channel realizes the concurrent producer/consumer;
    the producer publishes (put) and the consumer receives (get)."""
    res, out = _compile_pure(tmp_path, _STREAM, "Test")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "class Frame extends zsp_stream;" in gen
    assert "zsp_stream_channel #(Frame)" in gen
    assert "fork" in gen and "join" in gen
    assert ".put(p.o);" in gen and ".get(c.i);" in gen
    assert "DPI" not in gen


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_stream_simulates(tmp_path):
    """Stream end-to-end: producer solves d==7, publishes to the channel, and
    the concurrently-forked consumer receives and observes it."""
    res, out = _compile_pure(tmp_path, _STREAM, "Test")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "frame d=7" in log, f"stream did not deliver the value:\n{log}"
    assert "ZSP_PURE_DONE" in log


_RESOURCE = """import std_pkg::*;
component pss_top {
    resource R { rand bit[8] rid; }
    pool [4] R rp;
    bind rp *;
    action Wr { lock R r; exec body { message(NONE, "wr"); } }
    action Rd { share R r; exec body { message(NONE, "rd"); } }
    action Test {
        Wr a; Wr b;
        Rd c; Rd d;
        activity { parallel { a; b; } parallel { c; d; } }
    }
}
"""


def test_sv_pure_resource_structure(tmp_path):
    """Resources: a pool is created; lock claims exclusively (claim/unlock),
    share claims for reading (claim_shared/unshare)."""
    res, out = _compile_pure(tmp_path, _RESOURCE, "Test")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "class R extends zsp_resource;" in gen
    assert "zsp_resource_pool #(R)" in gen
    assert ".claim();" in gen and ".unlock(" in gen          # lock
    assert ".claim_shared();" in gen and ".unshare(" in gen  # share
    assert "DPI" not in gen


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_resource_simulates(tmp_path):
    """Concurrent lockers claim distinct instances; concurrent sharers share."""
    res, out = _compile_pure(tmp_path, _RESOURCE, "Test")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert log.count("wr") == 2 and log.count("rd") == 2, log
    assert "ZSP_PURE_DONE" in log


_REPEAT = """import std_pkg::*;
component pss_top {
    action Leaf { exec body { message(NONE, "L"); } }
    action Test {
        rand int n;
        constraint c { n == 3; }
        activity { repeat (n) { do Leaf; } }
    }
}
"""


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_repeat_and_anon_traversal(tmp_path):
    """`repeat (n) { do Leaf; }`: solved count drives a loop; `do Leaf` is an
    anonymous traversal of a synthesized instance."""
    res, out = _compile_pure(tmp_path, _REPEAT, "Test")
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "repeat (n) begin" in gen
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert log.count("L") >= 3 and "ZSP_PURE_DONE" in log, log


def _run_pattern(tmp_path, pattern_file, export):
    import pathlib
    pss = pathlib.Path(pattern_file).read_text()
    res, out = _compile_pure(tmp_path, pss, export)
    assert not res.errors, res.errors
    gen = (out / "zsp_gen_pkg.sv").read_text()
    assert "DPI" not in gen and "zsp_dpi" not in gen
    return out


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_resource_share_pattern(tmp_path):
    """The full resource_share.pss pattern (nested compound actions, do X,
    repeat, lock + share, parallel) lowers and simulates with no DPI."""
    out = _run_pattern(tmp_path, "tests/patterns/resource_share.pss", "do_test")
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "ZSP_PURE_DONE" in log


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_producer_consumer_pattern(tmp_path):
    """The producer_consumer.pss buffer-pipeline pattern (repeat burst + nested
    struct payload) lowers and simulates with no DPI."""
    out = _run_pattern(tmp_path, "tests/patterns/producer_consumer.pss", "do_burst")
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc == 0, f"build/run failed:\n{log}"
    assert "ZSP_PURE_DONE" in log


@pytest.mark.skipif(not HAVE_VERILATOR, reason="verilator not available")
def test_sv_pure_constraints_are_enforced(tmp_path):
    """Contradictory constraints must make randomize() fail -- proving the
    structured constraints are actually emitted and enforced (a dropped
    constraint would let randomize succeed)."""
    res, out = _compile_pure(tmp_path, _UNSAT, "A")
    assert not res.errors, res.errors
    rc, log = verilator_build_run(out, top_module="zsp_test_top")
    assert rc != 0, f"expected randomize failure, got success:\n{log}"
    assert "randomize failed" in log, f"expected fatal on unsat:\n{log}"
    assert "ZSP_PURE_DONE" not in log
