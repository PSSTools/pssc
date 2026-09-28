"""``sync_pkg::channel_c<Te, DEPTH>`` -- IR translation and SV projection.

A channel is the one core-library type whose implementation belongs to the
RUNTIME rather than to the generated model, and every defect in getting there
looked like ordinary generated code:

  * the specialization reached the IR as a plain ``DataTypeComponent``, so its
    element type and depth -- the whole content of ``channel_c<bit,1>`` -- were
    gone by the time a backend saw it;
  * the backend then lowered it as a user component, emitting
    ``interface class channel_c_if; endclass`` and a ``class channel_c`` with a
    constructor and no methods. That names every type the model mentions and
    fails only at the first ``get``.

So the absence assertions below are load-bearing, not decoration: `has_not` is
what distinguishes "the channel came from the runtime" from "the channel was
generated, empty".
"""
import argparse
import os
import shutil
import subprocess

import pytest

from pssc import driver

from .conftest import available_cpp_compilers, diagnostic_text
from zuspec.ir.core import DataTypeChannel


_MODEL = """
import sync_pkg::*;

component chan_c {
    channel_c<bit, 1>       wake;
    channel_c<bit[8], 4>    deep;

    target function bit take() {
        bit t;
        t = wake.get();
        return t;
    }
}

component chan_top_c {
    chan_c ch[2];

    target function void notify() {
        bool posted;
        foreach (ch[i]) {
            posted = ch[i].wake.try_put(1);
        }
    }

    export target function notify;
}
"""


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    out = tmp_path_factory.mktemp("chan_sv")
    src = out / "chan.pss"
    src.write_text(_MODEL)
    ns = argparse.Namespace(progseq_root="chan_top_c",
                            progseq_package="chan_pkg",
                            output_dir=str(out))
    res = driver.compile([str(src)], target="sv-progseq", opts=ns)
    return out, res, str(src)


@pytest.fixture(scope="module")
def sv(gen):
    out, _, _ = gen
    return (out / "chan_pkg.sv").read_text()


# --- IR --------------------------------------------------------------------

def test_channel_reaches_the_ir_with_its_template_arguments(gen):
    """Element type AND depth. A channel that arrives as a bare component has
    neither, and no backend can invent them."""
    _, _, src = gen
    ctx = driver.translate([src]).ir_context
    fields = {f.name: f.datatype for f in ctx.type_m["chan_c"].fields}

    assert isinstance(fields["wake"], DataTypeChannel)
    assert fields["wake"].depth == 1
    assert fields["wake"].element_type.bits == 1

    assert isinstance(fields["deep"], DataTypeChannel)
    assert fields["deep"].depth == 4
    assert fields["deep"].element_type.bits == 8


def test_depth_defaults_to_one(tmp_path):
    """§21.9.1's default, and it is behaviour rather than sizing: depth 1 is
    what makes a channel coalesce."""
    src = tmp_path / "d.pss"
    src.write_text("import sync_pkg::*;\n"
                   "component d_c { channel_c<bit> c;\n"
                   "  target function void f() { c.put(1); } }\n")
    ctx = driver.translate([str(src)]).ir_context
    dt = {f.name: f.datatype for f in ctx.type_m["d_c"].fields}["c"]
    assert dt.depth == 1


def test_zero_depth_is_rejected(tmp_path):
    """"DEPTH, if specified, shall be positive". Caught here because SV's
    `mailbox #(T) m = new(0)` is UNBOUNDED -- a zero that got through would
    produce a channel that never coalesces, with no error anywhere."""
    src = tmp_path / "z.pss"
    src.write_text("import sync_pkg::*;\n"
                   "component z_c { channel_c<bit, 0> c;\n"
                   "  target function void f() { c.put(1); } }\n")
    res = driver.translate([str(src)])
    assert any("DEPTH shall be positive" in e for e in res.errors), res.errors


# --- SV projection ---------------------------------------------------------

def test_channel_comes_from_the_runtime_not_the_model(sv):
    """The parameterized runtime class is referenced; no channel class or
    interface is generated for it."""
    assert "channel_c #(bit, 1) wake;" in sv
    assert "channel_c #(bit [7:0], 4) deep;" in sv
    assert "class channel_c" not in sv
    assert "channel_c_if" not in sv


def test_channels_are_constructed(sv):
    """An unconstructed handle is null, and SV reports that at the first
    `get`/`try_put` -- far from the constructor that should have built it."""
    assert "wake = new();" in sv
    assert "deep = new();" in sv


def test_channel_member_keeps_its_pss_name(sv):
    """No `m_` prefix, and not `protected` -- as every member now.

    Another component reaches it THROUGH an instance handle
    (`ch[i].wake.try_put(1)`), and the trailing `.wake` there is emitted from
    the PSS name. Renaming the declaration to `m_wake` produced code that
    spelled one field two ways; `protected` made the cross-component access a
    compile error.
    """
    assert "    channel_c #(bit, 1) wake;" in sv
    assert "protected channel_c" not in sv
    assert "m_wake" not in sv
    assert ".wake.try_put(1);" in sv


def test_blocking_get_becomes_a_task_call(sv):
    """`t = c.get()` -> `c.get(t)`. `get` blocks, so it is a task, and a task
    has no return value -- `x = some_task()` is a syntax error."""
    assert "wake.get(t);" in sv
    assert "= wake.get()" not in sv


def test_non_blocking_calls_keep_their_return_value(sv):
    """`try_put`/`try_get` are functions returning a bit and must NOT get the
    output-argument rewrite that `get` needs."""
    assert "posted = ch[i].wake.try_put(1);" in sv


# --- the runtime it depends on ---------------------------------------------

def test_runtime_package_provides_the_channel(gen):
    out, _, _ = gen
    core = (out / "pssc_reg_pkg.sv").read_text()
    assert "class channel_c #(type Te = bit, int DEPTH = 1);" in core
    for fn in ("task get(", "task put(", "function bit try_get(",
               "function bit try_put("):
        assert fn in core, fn


# --- the backends' channel support -----------------------------------------

def test_cpp_lowers_a_depth_1_channel(tmp_path):
    """The C++ target implements the non-blocking end, as the C target does.

    This test used to assert the opposite -- that C++ REFUSED channels -- and
    that was right for as long as there was no C++ runtime: lowering one anyway
    emitted a class with no channel member whose bodies still called
    `wake.get()`, an API that looks complete, exits 0 and does not compile.
    `share/cpp/pssc_chan.hpp` is what changed the answer.

    TYPED, unlike the C runtime. C has one channel struct with a `uint64_t`
    payload, so a generated body has to widen the model's own local to match;
    `pssc::chan1<T>` carries the declared element type instead, which is what
    lets the member and the local it drains into be checked against each other.
    """
    out = _gen_cpp(tmp_path, _C_MODEL, root="chan_top_c", ns="chan_top")
    h = (out / "chan_top.hpp").read_text()
    assert "pssc::chan1<bool> wake;" in h
    assert "this->wake.try_get(t)" in h
    assert "this->ch_[i].wake.try_put(1)" in h
    # ...which is legal only because the child names its parent a friend:
    # PSS component state is reachable along the hierarchy.
    assert "friend class chan_top;" in h
    # The runtime is copied in beside the generated header, and ONLY because
    # the model has a channel -- an unused header reads as a dependency the
    # platform has to satisfy.
    assert (tmp_path / "pssc_chan.hpp").exists()


def test_cpp_omits_the_channel_runtime_when_nothing_uses_it(tmp_path):
    model = """
component bare_c {
    target function int twice(int n) { return n + n; }
}
"""
    out = _gen_cpp(tmp_path, model, root="bare_c", ns="bare")
    assert not (out / "pssc_chan.hpp").exists()
    assert "pssc_chan.hpp" not in (out / "bare.hpp").read_text()


def test_cpp_refuses_a_blocking_channel_op_by_name(tmp_path):
    """`get`/`put` still need a scheduler, and this backend still has none."""
    model = _C_MODEL.replace("ok = wake.try_get(t);", "t = wake.get();")
    with pytest.raises(Exception) as exc:
        _gen_cpp(tmp_path, model, root="chan_c", ns="chan")
    msg = diagnostic_text(exc)
    assert "'get'" in msg
    assert "suspend" in msg
    assert "try_get" in msg
    assert not (tmp_path / "chan.hpp").exists()


def test_cpp_compiles_its_channel_lowering(tmp_path):
    """A generated channel access is only type-checked when something compiles
    it -- and the local a `try_get` drains into is the part most likely to be
    wrong, since the C backend had to widen it."""
    if not available_cpp_compilers():
        pytest.skip("no C++ compiler on PATH")
    out = _gen_cpp(tmp_path, _C_MODEL, root="chan_top_c", ns="chan_top")
    main = out / "main.cpp"
    main.write_text(
        '#include "chan_top.hpp"\n'
        'int main() { pssc::mmio_mem m; '
        'auto t = chan_top::chan_top::create(m); t->notify(); return 0; }\n')
    res = subprocess.run(
        [available_cpp_compilers()[0], "-std=c++17", "-Wall", "-Wextra",
         "-Werror", "-c", str(main), "-I", str(out), "-o", str(out / "main.o")],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


#: Depth-1 channels driven only through try_put/try_get -- what the C runtime
#: implements, and the shape the WB DMA model's `inflight` guard uses.
_C_MODEL = """
import sync_pkg::*;

component chan_c {
    channel_c<bit, 1>       wake;

    target function bit take() {
        bit  t;
        bool ok;
        ok = wake.try_get(t);
        return t;
    }
}

component chan_top_c {
    chan_c ch[2];

    target function void notify() {
        bool posted;
        foreach (ch[i]) {
            posted = ch[i].wake.try_put(1);
        }
    }
}
"""


def _gen_cpp(tmp_path, model, root="chan_c", ns="chan"):
    src = tmp_path / "chan.pss"
    src.write_text(model)
    opts = argparse.Namespace(progseq_root=root, cpp_namespace=ns,
                              cpp_dispatch="virtual", cpp_single_header=True,
                              progseq_core_copy=True, output_dir=str(tmp_path))
    driver.compile([str(src)], target="cpp-progseq", opts=opts)
    return tmp_path


def _gen_c(tmp_path, model, root="chan_c"):
    src = tmp_path / "chan.pss"
    src.write_text(model)
    ns = argparse.Namespace(progseq_root=root, c_prefix="chan",
                            output_dir=str(tmp_path))
    return driver.compile([str(src)], target="c-progseq", opts=ns)


def test_c_lowers_a_depth_1_channel(tmp_path):
    """The non-blocking end needs no scheduler, so the C target implements it.

    This is the half of `channel_c` that a polling profile actually uses: PSS
    3.1 has no mutable component attribute (§9.1.6), so a depth-1 channel is the
    only way to spell a latch, and the WB DMA model's `inflight` guard is one.
    Refusing the whole type -- which is what this backend used to do -- refused
    the latch along with the wait.
    """
    _gen_c(tmp_path, _C_MODEL)
    h = (tmp_path / "chan.h").read_text()
    c = (tmp_path / "chan.c").read_text()
    assert "pssc_chan1_t wake;" in h
    assert "pssc_chan1_init(&self->wake);" in c
    assert "pssc_chan1_try_get(&s->wake, &t)" in c
    # The runtime is copied in beside the generated files, so the directory
    # still compiles on its own.
    assert (tmp_path / "pssc_chan.h").exists()


def test_c_refuses_a_blocking_channel_op_by_name(tmp_path):
    """`get`/`put` are the two operations that need a scheduler.

    Named in the diagnostic rather than reported as "channels are unsupported":
    the model is one `try_get` away from working, and the old message sent the
    reader looking for a missing runtime instead of at the one call.

    Since P1.T1 the gate refuses first, so the diagnostic names `get` and the
    function it appears in rather than the receiver expression `wake.get()`.
    That is a small loss of precision bought for reporting every offending call
    at once, before anything is written; the emitter's own message, which does
    spell the receiver, remains the backstop.
    """
    model = _C_MODEL.replace("ok = wake.try_get(t);", "t = wake.get();")
    with pytest.raises(Exception) as exc:
        _gen_c(tmp_path, model)
    msg = diagnostic_text(exc)
    assert "'get'" in msg                      # the one call, not "channels"
    assert "suspend" in msg                    # why
    assert "try_get" in msg                    # and what to do instead
    assert not (tmp_path / "chan.h").exists()  # refused before writing


def test_c_refuses_a_deeper_channel_by_depth(tmp_path):
    """Depth > 1 is a ring buffer, which the runtime does not implement.

    Rejected at the STRUCT MEMBER, so it fails even if no operation touches the
    channel -- a member silently omitted from the handle is the failure mode
    this whole check exists for.
    """
    model = _C_MODEL.replace("channel_c<bit, 1>       wake;",
                             "channel_c<bit, 1>       wake;\n"
                             "    channel_c<bit[8], 4>    deep;")
    with pytest.raises(ValueError, match="depth 4"):
        _gen_c(tmp_path, model)


@pytest.mark.skipif(not shutil.which("verilator"), reason="verilator not on PATH")
def test_generated_package_lints_clean(gen):
    out, _, _ = gen
    r = subprocess.run(
        ["verilator", "--lint-only", "-sv", "--timing",
         str(out / "pssc_reg_pkg.sv"), str(out / "chan_pkg.sv")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --- discarded channel results (SV) -----------------------------------------

_DISCARD_MODEL = """
import sync_pkg::*;

component disc_c {
    channel_c<bit, 1>       wake;
    channel_c<bit[8], 1>    wide;

    // Every shape the model actually uses, all discarding the result.
    target function void ops() {
        wake.get();                 // blocking receive, value thrown away
        wake.try_put(1);            // predicate, answer ignored
        wide.get();                 // ...and a channel wider than `bit`
    }

    export target function ops;
}
"""


def _gen_sv_disc(tmp_path):
    src = tmp_path / "disc.pss"
    src.write_text(_DISCARD_MODEL)
    ns = argparse.Namespace(progseq_root="disc_c", output_dir=str(tmp_path))
    driver.compile([str(src)], target="op-model-sv", opts=ns)
    return (tmp_path / "disc_c_pkg.sv").read_text()


def test_sv_supplies_a_temp_for_a_discarded_blocking_get(tmp_path):
    """`sync_pkg` declares `target function Te get();` -- no arguments, returns
    the element. SV cannot: `get` blocks, so it must be a `task`, and a task
    returns nothing, so the runtime declares `task get(output Te t);`.

    A PSS `c.get();` that discards the value therefore still has to supply
    somewhere to put it. Emitting it verbatim yields `wake.get();`, which is not
    a syntax error but a MISSING ARGUMENT -- caught only at elaboration, which is
    why it survived into a generated package that had been reviewed.

    This is the `wait_hint()` case: the wake token deliberately carries no
    information, so discarding it is the correct model.
    """
    sv = _gen_sv_disc(tmp_path)
    assert "wake.get();" not in sv, "the no-argument form does not elaborate"
    assert "wake.get(pssc_discard);" in sv


def test_the_discard_temp_matches_the_channel_element_width(tmp_path):
    """A `bit` temp against a `channel_c<bit[8]>` is a WIDTHTRUNC warning, and
    the lint gate fails on warnings -- so getting the type merely "close" is the
    same as not fixing it."""
    sv = _gen_sv_disc(tmp_path)
    assert "bit [7:0] pssc_discard;" in sv or "bit[7:0] pssc_discard;" in sv, sv


def test_sv_voids_a_discarded_channel_predicate(tmp_path):
    """`try_put`/`try_get` are SV *functions* returning a bit. Ignoring a
    non-void function's return is legal but warns (IEEE 1800-2023 13.4.1), and
    the lint gate treats that as failure. `void'(...)` states the intent.

    Paired with the assertion that it is NOT applied to `get`, which is a task:
    `void'(task())` is a syntax error, so over-applying this would break the
    very thing the test above fixes.
    """
    sv = _gen_sv_disc(tmp_path)
    assert "void'(wake.try_put(1));" in sv
    assert "void'(wake.get" not in sv


_NESTED_DISCARD = """
import sync_pkg::*;

component leaf_c {
    channel_c<bit, 1> wake;
}

component nest_c {
    leaf_c ch[2];

    // The notify_irq() shape: the channel belongs to the CHILD.
    target function void notify() {
        foreach (ch[i]) {
            ch[i].wake.try_put(1);
        }
    }

    export target function notify;
}
"""


def test_a_discarded_predicate_on_a_child_channel_is_voided_too(tmp_path):
    """The receiver is `ch[i].wake`, so a test keyed on "is this a channel field
    of the component being lowered" misses it -- and the same construct then
    comes out `void'`-wrapped in one function and bare in another.

    Verilator does not currently warn on the indexed form, which is precisely
    why this needs a test rather than the lint gate: it is a difference that
    would surface on a change of simulator, long after the fact.
    """
    src = tmp_path / "nest.pss"
    src.write_text(_NESTED_DISCARD)
    ns = argparse.Namespace(progseq_root="nest_c", output_dir=str(tmp_path))
    driver.compile([str(src)], target="op-model-sv", opts=ns)
    sv = (tmp_path / "nest_c_pkg.sv").read_text()

    assert "void'(ch[i].wake.try_put(1));" in sv, sv
