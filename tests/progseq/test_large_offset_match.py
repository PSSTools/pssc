"""A register group's offset function as a large `match` over string names.

A register generator writes `get_offset_of_instance` as one flat `match`
over every instance name of the group -- hundreds of choices, and nothing in
PSS bounds the count. Two passes made that quadratic and then deep:

* `match_overlap` took every pair of string choices as possibly
  overlapping (it knew only integer intervals) and put an `error()` guard
  in front of the `match`: N*(N-1)/2 conjunctions in an `||` chain one level
  deeper per pair. Every recursive walk after it ran out of stack at 30
  choices; at 500 the guard had 124750 terms.
* `_OffsetEval` RAN the function once per instance, case by case: N*N tests
  per group, repeated by every reader of the register walk.

Distinct string literals are disjoint, as distinct integer intervals are;
repeated ones, intersecting ranges and run-time values are still guarded.
What the guard and an arm's value list build is a balanced `||`, and the
offset function answers each name by one lookup. These tests hold the
addresses -- first, last, `default`, interleaved arrays -- on every target,
and the scale beyond the size that first failed.
"""
from __future__ import annotations

import dataclasses as dc
import sys

import pytest

import zuspec.ir.core as ir

from pssc.driver import CompileError
from pssc.targets import match_overlap
from pssc.targets.body_walker import any_of

from . import trace_harness as th


def _bank(n, *, last_by_default=False):
    """A group of ``n`` 32-bit registers 4 bytes apart, placed by one flat
    `match`; with ``last_by_default`` the last has no choice of its own and
    takes the `default` offset."""
    named = n - 1 if last_by_default else n
    fields = "\n".join(f"  reg_c<word_s> WORD_{i};" for i in range(n))
    cases = "\n".join(f'      ["WORD_{i}"]: return {i * 4};'
                      for i in range(named))
    dflt = (n - 1) * 4 if last_by_default else -1
    return f"""
struct word_s : packed_s<> {{ bit[32] value; }}
pure component bank_c : reg_group_c {{
{fields}
  function bit[64] get_offset_of_instance(string name) {{
    match (name) {{
{cases}
      default: return {dflt};
    }}
  }}
}}
"""


def _top(n, body):
    return f"""
component pss_top {{
  bank_c regs;
  solve function void initialize(addr_handle_t base) {{
    regs.set_handle(base);
  }}
  target function void run() {{
{body}
  }}
}}
"""


def _bank_run(n, **kw):
    mid = n // 2
    return _bank(n, **kw) + _top(n, f"""
    regs.WORD_0.write_val(1);
    regs.WORD_{n - 1}.write_val(2);
    bit[32] v = regs.WORD_{mid}.read_val();
    message(LOW, "v=%d", v);""")


def _want(n, mid_val=0):
    return ["write 32 0x1000 0x1",
            f"write 32 {hex(0x1000 + (n - 1) * 4)} 0x2",
            f"read 32 {hex(0x1000 + (n // 2) * 4)} {hex(mid_val)}",
            f"v={mid_val}"]


def _all_targets(tmp_path, pss, mem=None, targets=th.TARGETS,
                 args=(0x1000,)):
    """The trace of ``pss`` on each of ``targets`` that can run here."""
    targets = [t for t in targets if not th.available(t)]
    builds = []
    for t in targets:
        d = tmp_path / t
        d.mkdir()
        builds.append(th.prepare(t, d, pss, args, mem))
    return dict(zip(targets, th.run_many(builds)))


# --- addresses, on every target ----------------------------------------------

def test_first_middle_and_last_choice(tmp_path):
    traces = _all_targets(tmp_path, _bank_run(500),
                          mem={0x1000 + 250 * 4: 7})
    assert traces
    for t, trace in traces.items():
        assert trace == _want(500, 7), t


def test_the_default_choice_places_a_register(tmp_path):
    """The last register is named by no choice; `default` places it."""
    traces = _all_targets(tmp_path, _bank_run(500, last_by_default=True))
    for t, trace in traces.items():
        assert trace == _want(500), t


def test_interleaved_register_arrays(tmp_path):
    """Two arrays of one repeated record, a word apart: each element at its
    own base plus the shared stride, read and written at 32 bits."""
    pss = """
struct word_s : packed_s<> { bit[32] value; }
pure component bank_c : reg_group_c {
  reg_c<word_s> WORD_0[4];
  reg_c<word_s> WORD_1[4];
  function bit[64] get_offset_of_instance(string name) {
    match (name) { default: return -1; }
  }
  function bit[64] get_offset_of_instance_array(string name, int index) {
    match (name) {
      ["WORD_0"]: return 0x100 + index * 0x10;
      ["WORD_1"]: return 0x104 + index * 0x10;
      default: return -1;
    }
  }
}
""" + _top(0, """
    regs.WORD_0[3].write_val(1);
    regs.WORD_1[3].write_val(2);
    repeat (i : 2) {
      bit[32] v = regs.WORD_1[i].read_val();
      message(LOW, "v=%d", v);
    }""")
    traces = _all_targets(tmp_path, pss, mem={0x1104: 5, 0x1114: 6})
    for t, trace in traces.items():
        assert trace == ["write 32 0x1130 0x1", "write 32 0x1134 0x2",
                         "read 32 0x1104 0x5", "v=5",
                         "read 32 0x1114 0x6", "v=6"], t


def test_a_name_no_choice_places_is_refused(tmp_path):
    """The -1 sentinel for a declared register is the model's error, named,
    before any file is written."""
    pss = _bank(40).replace('      ["WORD_39"]: return 156;\n', "") \
        + _top(40, "    regs.WORD_0.write_val(1);")
    with pytest.raises(CompileError) as e:
        th.compile_model(tmp_path, pss, "c")
    (err,) = e.value.errors
    assert "'WORD_39'" in str(err) and "-1" in str(err)


# --- which choices are guarded -----------------------------------------------

def _c(v):
    return ir.ExprConstant(value=v)


def _r(lo, hi):
    return ir.ExprRange(lower=_c(lo), upper=_c(hi))


_RUNTIME = ir.ExprRefLocal(name="hi")


@pytest.mark.parametrize("arms, pairs", [
    ([[_c("A")], [_c("B")], [_c("C")]], []),
    ([[_c("A"), _c("B")], [_c("C"), _c("D")]], []),
    ([[_c("A"), _c("B")], [_c("C")], [_c("B")]], [(0, 2)]),
    ([[_c("A")], [_c("A")], [_c("A")]], [(0, 1), (0, 2), (1, 2)]),
    ([[_r(0, 3)], [_r(4, 9)], [_c(10)]], []),
    ([[_r(0, 3)], [_c(7)], [_r(3, 5)], [_r(6, 9)]], [(0, 2), (1, 3)]),
    ([[_c(1), _r(20, 30)], [_c(25)]], [(0, 1)]),
    ([[_c("A")], [ir.ExprRange(lower=_c(0), upper=_RUNTIME)], [_c("B")]],
     [(0, 1), (1, 2)]),
    ([[_c(0)], [_RUNTIME]], [(0, 1)]),
])
def test_overlapping_pairs(arms, pairs):
    assert match_overlap.overlapping_pairs(arms) == pairs


def _depth(e):
    """Nesting depth of ``e``, walked without recursion."""
    best, work = 0, [(e, 1)]
    while work:
        n, d = work.pop()
        best = max(best, d)
        for f in dc.fields(n):
            v = getattr(n, f.name)
            if isinstance(v, ir.Expr):
                work.append((v, d + 1))
    return best


def test_any_of_is_balanced():
    terms = [_c(i) for i in range(100000)]
    assert _depth(any_of(terms)) <= 18


def test_distinct_names_scale_linearly():
    """Distinct strings are found disjoint without trying every pair."""
    arms = [[_c(f"WORD_{i}")] for i in range(200000)]
    assert match_overlap.overlapping_pairs(arms) == []


_GUARDED = """
component pss_top {
  target function void go(string s) {
    match (s) {
      ["A"]: message(LOW, "a");
      ["B", "C"]: message(LOW, "bc");
      ["C"]: message(LOW, "c");
      default: message(LOW, "other");
    }
  }
  target function void run() { go("A"); go("B"); go("D"); go("C"); }
}
"""


def test_a_repeated_string_choice_is_still_an_error(tmp_path):
    """"C" is in two choices: the values only one choice holds run as
    before, and "C" is the error 20.7.10 d makes it. (C and C++ have no
    string, ruling R3.)"""
    traces = _all_targets(tmp_path, _GUARDED, targets=("py", "sv"), args=())
    assert "py" in traces
    for t, trace in traces.items():
        assert trace[:3] == ["a", "bc", "other"], t
        assert len(trace) == 4 and trace[3].startswith(("error", "fatal")), \
            (t, trace)


# --- scale --------------------------------------------------------------------

@pytest.mark.parametrize("n", [500, 5000])
@pytest.mark.parametrize("target", ["c", "sv"])
def test_large_banks_generate_at_the_default_recursion_limit(
        tmp_path, target, n):
    assert sys.getrecursionlimit() <= 1000 or pytest.skip(
        "the recursion limit was raised by something else in this process")
    out = th.compile_model(tmp_path, _bank_run(n), target)
    text = "".join(p.read_text() for p in out.iterdir() if p.is_file())
    # No pair of distinct names was guarded.
    assert match_overlap.MESSAGE not in text
    assert f"WORD_{n - 1}" in text
