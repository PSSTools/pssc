"""`message()` with format arguments in op-model-py (LRM 21.1.1).

The generated module formats with `_pss_fmt`, which it carries (source:
`targets/py/pss_format.py`), and tags each value with its PSS type so a host
`int` prints as the PSS value it stands for. The format is checked when the
model is generated.

Three claims:
  * the formatter agrees with the bc backend's, which the compliance corpus
    checks, over a grid of specifiers, values and types;
  * a bad format, or one the argument's type cannot take, is a BUILD error;
  * a generated model prints what the LRM says, and a model with no formatted
    message carries no formatter.
"""
from __future__ import annotations

import importlib
import itertools
import sys

import pytest

from pssc.targets.py import pss_format as pf
from pssc.testing import compile_op_model

# --- the formatter against bc's --------------------------------------------

_SPECS = ["%d", "%u", "%x", "%X", "%o", "%b", "%B", "%5d", "%-5d|", "%05d",
          "%+d", "% d", "%#x", "%#o", "%#b", "%.3d", "%.0d", "%8.3x"]
_INTS = [(8, False), (8, True), (32, True), (32, False), (64, False), (64, True),
         (1, False), (12, False)]
_VALUES = [0, 1, 5, 127, 128, 255, -1, -128, 0x7fffffff, 0xdeadbeef, -(1 << 31)]


def _bc_format():
    try:
        from zuspec.be.bc.interp.fmt import format_message
    except ImportError:
        pytest.skip("zuspec-be-bc is not installed")
    return format_message


@pytest.mark.parametrize("spec", _SPECS)
def test_integers_format_as_bc_formats_them(spec):
    bc = _bc_format()
    for (width, signed), v in itertools.product(_INTS, _VALUES):
        want = bc(spec, [{"kind": "int", "width": width, "signed": signed}],
                  [v & ((1 << 64) - 1)], [])
        got = pf._pss_fmt(spec, (v, ("i", width, signed)))
        assert got == want, (spec, width, signed, v)


def test_bool_enum_string_and_percent_format_as_bc_formats_them():
    bc = _bc_format()
    items = (("OFF", 0), ("SLOW", 5), ("NEG", -3))
    desc = {"kind": "enum", "width": 32, "signed": True,
            "items": [list(i) for i in items]}
    for fmt, val, ty, bdesc, bval, strings in [
        ("%n", True, ("b",), {"kind": "bool"}, 1, []),
        ("%n", False, ("b",), {"kind": "bool"}, 0, []),
        ("%d", True, ("b",), {"kind": "bool", "width": 1, "signed": False}, 1, []),
        ("[%-6n]", 5, ("e", items), desc, 5, []),
        ("%.2n", 5, ("e", items), desc, 5, []),
        ("%n", -3, ("e", items), desc, (-3) & ((1 << 64) - 1), []),
        ("%d", -3, ("e", items), desc, (-3) & ((1 << 64) - 1), []),
        ("<%5s>", "hi", ("s",), {"kind": "string"}, 0, ["hi"]),
        ("100%% %s", "x", ("s",), {"kind": "string"}, 0, ["x"]),
    ]:
        assert pf._pss_fmt(fmt, (val, ty)) == bc(fmt, [bdesc], [bval], strings), fmt


def test_a_percent_that_starts_no_specifier_is_refused():
    with pytest.raises(ValueError, match="does not start a valid format"):
        pf.parse_format("50%, off")


def test_floating_point_formats_are_refused():
    with pytest.raises(ValueError, match="%f is not supported"):
        pf.parse_format("%f")


def test_the_inlined_source_needs_nothing_but_the_standard_library():
    ns: dict = {}
    exec(pf.inline_source(), ns)
    assert ns["_pss_fmt"]("%u", (-1, ("i", 8, True))) == "255"
    assert "pssc" not in pf.inline_source()


# --- a generated model -----------------------------------------------------

_MODEL = """
import std_pkg::*;
enum mode_e { OFF, SLOW = 5, FAST }
struct cfg_s { bit[12] len; mode_e m; }
component pss_top {
  bit[8] b8;
  cfg_s cfg;
  target function bit[16] tag() { return 0xbeef; }
  target function void show(int a, bit[4] q) {
    bool ok = true;
    string st = "hi";
    mode_e m = FAST;
    message(NONE, "a=%d a_u=%u ok=%n st=%s m=%n m_d=%d", a, a, ok, st, m, m);
    message(NONE, "q=%x b8=%u b8p1=%d cfg.m=%n tag=%#x 100%%",
            q, b8, b8 + 1, cfg.m, tag());
  }
  target function void plain() {
    message(NONE, "no formatting here");
  }
}
"""


def _load(outcome, module):
    sys.path.insert(0, str(outcome.out_dir))
    try:
        for name in (module, "pssc_rt"):
            sys.modules.pop(name, None)
        return (importlib.import_module(module),
                importlib.import_module("pssc_rt"))
    finally:
        sys.path.remove(str(outcome.out_dir))


def _compile(tmp_path, pss, root="pss_top"):
    src = tmp_path / "m.pss"
    src.write_text("import std_pkg::*;\n" + pss)
    return compile_op_model("op-model-py", sources=[str(src)], root=root,
                            output_dir=str(tmp_path / "out"))


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    src = tmp_path_factory.mktemp("msg") / "m.pss"
    src.write_text(_MODEL)
    with compile_op_model("op-model-py", sources=[str(src)],
                          root="pss_top") as outcome:
        mod, rt = _load(outcome, "pss_top")
        yield mod, rt, outcome


def test_values_print_at_their_pss_type(gen):
    mod, rt, _ = gen
    bus = rt.MemoryBus()
    dut = mod.PssTop(bus)
    dut.b8 = 255
    dut.show(-1, 9)
    msgs = [t for k, _, _, t in bus.log if k == "message"]
    assert msgs == [
        "a=-1 a_u=4294967295 ok=true st=hi m=FAST m_d=6",
        # b8 + 1 is bit[32] (Table 22), so it is 256, not a wrapped 0.
        "q=9 b8=255 b8p1=256 cfg.m=OFF tag=0xbeef 100%",
    ]


def test_a_plain_message_is_passed_as_it_stands(gen):
    mod, rt, outcome = gen
    bus = rt.MemoryBus()
    mod.PssTop(bus).plain()
    assert bus.log[-1][3] == "no formatting here"
    assert "message('no formatting here')" in outcome.read("pss_top.py")


def test_a_model_with_no_formatted_message_carries_no_formatter(tmp_path):
    with _compile(tmp_path, """
component pss_top {
  target function void f() { message(NONE, "hello"); }
}""") as outcome:
        assert "_pss_fmt" not in outcome.read("pss_top.py")


@pytest.mark.parametrize("call,match", [
    ('message(NONE, "%d %d", a)', r"2 format specifier\(s\) for 1 argument"),
    ('message(NONE, "50%, off", a)', "does not start a valid format"),
    ('message(NONE, "%n", a)', r"%n cannot format a int"),
    ('message(NONE, "%s", a)', r"%s cannot format a int"),
    ('message(NONE, "%d", s)', r"%d cannot format a string"),
])
def test_a_bad_format_is_a_build_error(tmp_path, call, match):
    with pytest.raises(ValueError, match=match):
        with _compile(tmp_path, f"""
component pss_top {{
  target function void f(int a) {{ string s = "x"; {call}; }}
}}"""):
            pass
