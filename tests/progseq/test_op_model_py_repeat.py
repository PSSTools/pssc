"""`repeat ([i :] n)` in op-model-py (LRM 20.7.6).

The corpus has the plain cases (proc.repeat.*, proc.break*, proc.continue).
These are the ones where Python's `for` and PSS's `repeat` part ways -- the
count's single evaluation, and an index scoped to the loop -- and each is
driven, not grepped, where the claim is behaviour.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys

import pytest

from pssc import driver


def _compile(tmp_path, pss, target="op-model-py", **kw):
    src = tmp_path / "m.pss"
    src.write_text("import std_pkg::*;\nimport addr_reg_pkg::*;\n" + pss)
    ns = argparse.Namespace(progseq_root="pss_top",
                            output_dir=str(tmp_path / "out"), **kw)
    driver.compile([str(src)], target=target, opts=ns)
    return tmp_path / "out"


def _load(out_dir, rt="pssc_rt"):
    sys.path.insert(0, str(out_dir))
    try:
        for name in ("pss_top", "pssc_rt", "pssc_rt_async"):
            sys.modules.pop(name, None)
        return (importlib.import_module("pss_top"),
                importlib.import_module(rt))
    finally:
        sys.path.remove(str(out_dir))


def _run(tmp_path, body, extra=""):
    """Run `g()` of a pss_top whose body is *body*; its messages."""
    mod, rt = _load(_compile(tmp_path, f"""
component pss_top {{
  {extra}
  target function void g() {{
    {body}
  }}
}}"""))
    bus = rt.MemoryBus()
    mod.PssTop(bus).g()
    return [t for k, _, _, t in bus.log if k == "message"]


# --- the count ---------------------------------------------------------------

def test_the_count_is_evaluated_once(tmp_path):
    """The body changes what the count expression would yield; the loop still
    runs the number of times it said at the start."""
    assert _run(tmp_path, """
    int n = 3;
    repeat (n) { n = n + 1; message(NONE, "n=%d", n); }
    message(NONE, "after n=%d", n);""") == [
        "n=4", "n=5", "n=6", "after n=6"]


def test_a_count_with_a_side_effect_runs_it_once(tmp_path):
    assert _run(tmp_path, """
    repeat (bump()) { message(NONE, "k=%d", k); }""", extra="""
  int k;
  function int bump() { k = k + 1; return 2; }""") == ["k=1", "k=1"]


def test_a_count_read_from_a_register_is_read_once_in_the_async_form(tmp_path):
    """The read is hoisted in front of the loop -- once, where the count is
    evaluated -- and not into the body."""
    out = _compile(tmp_path, """
pure component regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CNT;
}
component pss_top {
  regs_c regs;
  int seen;
  solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
  target function void g() {
    repeat (i : regs.CNT.read_val()) { seen = seen + i + 1; }
  }
}""", py_await="async")
    mod, rt = _load(out, "pssc_rt_async")
    bus = rt.AsyncMemoryBus()
    dut = mod.PssTop(bus, 0x100)
    bus.mem[0x100] = 3
    asyncio.run(dut.g())
    assert dut.seen == 1 + 2 + 3
    assert [k for k, *_ in bus.log] == ["read"]


# --- the index ---------------------------------------------------------------

def test_an_index_does_not_overwrite_a_local_of_its_name(tmp_path):
    """PSS scopes the index to the loop; Python would scope it to the
    function and leave the last index in the local."""
    assert _run(tmp_path, """
    int i = 7;
    repeat (i : 3) { message(NONE, "in i=%d", i); }
    message(NONE, "after i=%d", i);""") == [
        "in i=0", "in i=1", "in i=2", "after i=7"]


def test_a_nested_index_of_the_same_name_does_not_leak_out(tmp_path):
    assert _run(tmp_path, """
    repeat (i : 2) {
      repeat (i : 3) { }
      message(NONE, "outer i=%d", i);
    }""") == ["outer i=0", "outer i=1"]


def test_an_index_named_like_a_parameter_leaves_the_parameter(tmp_path):
    mod, rt = _load(_compile(tmp_path, """
component pss_top {
  int total;
  target function void g(int n) {
    repeat (n : 2) { total = total + n; }
    total = total + n * 100;
  }
}"""))
    dut = mod.PssTop(rt.MemoryBus())
    dut.g(5)
    assert dut.total == (0 + 1) + 500


def test_sibling_loops_keep_the_plain_name(tmp_path):
    text = (_compile(tmp_path, """
component pss_top {
  int t;
  target function void g() {
    repeat (i : 2) { t = t + i; }
    repeat (i : 3) { t = t + i; }
    repeat (4) { t = t + 1; }
  }
}""") / "pss_top.py").read_text()
    assert text.count("for i in range(") == 2


def test_an_index_types_as_int_for_message(tmp_path):
    assert _run(tmp_path, """
    repeat (i : 2) { message(NONE, "%d %x", i - 1, i + 10); }""") == [
        "-1 a", "0 b"]


def test_a_local_declared_in_the_body_does_not_disturb_the_index(tmp_path):
    assert _run(tmp_path, """
    repeat (i : 3) {
      { int i = 9; message(NONE, "inner i=%d", i); }
      message(NONE, "index i=%d", i);
    }""") == ["inner i=9", "index i=0", "inner i=9", "index i=1",
              "inner i=9", "index i=2"]


def test_an_index_named_like_a_local_of_another_type_is_typed_as_int(tmp_path):
    """`message` checks each argument's type; the index's is `int` inside its
    loop, whatever a local of that name elsewhere in the function is."""
    assert _run(tmp_path, """
    bool i = true;
    repeat (i : 2) { message(NONE, "%d", i - 1); }
    message(NONE, "%n", i);""") == ["-1", "0", "true"]


# --- refused, rather than lowered to a different loop -------------------------

@pytest.mark.parametrize("write", ["i = 5;", "i += 1;"])
def test_a_body_that_assigns_its_index_is_refused(tmp_path, write):
    with pytest.raises(ValueError,
                       match="its body assigns the index 'i'"):
        _compile(tmp_path, f"""
component pss_top {{
  int t;
  target function void g() {{
    repeat (i : 4) {{ {write} t = t + 1; }}
  }}
}}""")


def test_a_backend_without_the_loop_refuses_it(tmp_path):
    """op-model-c has no `stmt_for` yet. It says so, and emits nothing."""
    with pytest.raises(Exception, match="stmt_for"):
        _compile(tmp_path, """
component pss_top {
  int t;
  target function void g() { repeat (4) { t = t + 1; } }
}""", target="op-model-c")
