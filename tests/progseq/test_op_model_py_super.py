"""Action inheritance in an exported action: shadowing and `super;`.

A derived action's `exec body` shadows its base's, and `super;` in it runs the
base's body at that point (LRM 17.1 Table 27, 20.1.4); a derived action that
declares no body runs its base's. The front end used to DROP `super;` -- an
exported `A1 : A { exec body { super; ... } }` ran only A1's statements and
said nothing. See `src/pssc/targets/export_action.py`.

Driven, not grepped: the generated module is imported and each entry called.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys

import pytest

from pssc import driver

_MODEL = """
import std_pkg::*;

component base_c {
  action B { exec body { message(NONE, "B"); } }
}

component pss_top : base_c {
  action A { exec body { message(NONE, "A"); } }

  // super first, super last
  action A1 : A { exec body { super; message(NONE, "A1"); } }
  action A1r : A { exec body { message(NONE, "A1r"); super; } }

  // no body of its own: A1's runs, super and all
  action A2 : A1 { }
  // super reaches through A2, which declares no body, to A1
  action A3 : A2 { exec body { super; message(NONE, "A3"); } }

  // a shadowing body without super: the base's does NOT run
  action Ashadow : A { exec body { message(NONE, "Ashadow"); } }

  // the base action runs in the base component
  action C : B { exec body { super; message(NONE, "C"); } }

  // nothing below declares a body: super does nothing
  action E { }
  action E1 : E { exec body { super; message(NONE, "E1"); } }

  // the base's locals are its own ...
  action L { exec body { int x = 1; message(NONE, "L x=%d", x); } }
  action L1 : L {
    exec body {
      bit[8] x = 100;
      super;
      message(NONE, "L1 x=%d", x);
    }
  }

  // ... and so is its return
  action R { exec body { message(NONE, "R1"); return; message(NONE, "R2"); } }
  action R1 : R { exec body { super; message(NONE, "R1 after"); } }
}
"""

_EXPECT = {
    "A1": ["A", "A1"],
    "A1r": ["A1r", "A"],
    "A2": ["A", "A1"],
    "A3": ["A", "A1", "A3"],
    "Ashadow": ["Ashadow"],
    "C": ["B", "C"],
    "E1": ["E1"],
    "L1": ["L x=1", "L1 x=100"],
    "R1": ["R1", "R1 after"],
}


def _compile(tmp_path, text, actions, await_style="sync"):
    p = tmp_path / "m.pss"
    p.write_text(text)
    out = tmp_path / f"out_{await_style}"
    driver.compile([str(p)], target="op-model-py", opts=argparse.Namespace(
        output_dir=str(out), progseq_root="pss_top",
        export_actions=list(actions), py_await=await_style))
    return out


def _load(out, *names):
    sys.path.insert(0, str(out))
    try:
        for name in ("pss_top",) + names:
            sys.modules.pop(name, None)
        return [importlib.import_module(n) for n in ("pss_top",) + names]
    finally:
        sys.path.remove(str(out))


def _messages(bus):
    got = [t for k, _, _, t in bus.log if k == "message"]
    bus.log.clear()
    return got


def test_sync(tmp_path):
    out = _compile(tmp_path, _MODEL, [f"pss_top::{a}" for a in _EXPECT])
    mod, rt = _load(out, "pssc_rt")
    bus = rt.MemoryBus()
    top = mod.PssTop(bus)
    got = {}
    for a in _EXPECT:
        getattr(top, a)()
        got[a] = _messages(bus)
    assert got == _EXPECT


def test_async(tmp_path):
    out = _compile(tmp_path, _MODEL, [f"pss_top::{a}" for a in _EXPECT],
                   "async")
    mod, _, rta = _load(out, "pssc_rt", "pssc_rt_async")
    bus = rta.AsyncMemoryBus()
    top = mod.PssTop(bus)
    got = {}
    for a in _EXPECT:
        asyncio.run(getattr(top, a)())
        got[a] = _messages(bus)
    assert got == _EXPECT


def test_only_reached_bases_are_rendered(tmp_path):
    out = _compile(tmp_path, _MODEL, ["pss_top::Ashadow", "pss_top::A3"])
    text = (out / "pss_top.py").read_text()
    assert "_pss_super_Ashadow" not in text
    assert "def _pss_super_A3_1(self)" in text
    assert "def _pss_super_A3_2(self)" in text
    assert "_pss_super_A3_3" not in text


def test_a_packaged_base_resolves(tmp_path):
    text = """
import std_pkg::*;
package p {
  component base_c { action B { exec body { message(NONE, "B"); } } }
}
component pss_top : p::base_c {
  action C : B { exec body { super; message(NONE, "C"); } }
  action D : p::base_c::B { exec body { super; message(NONE, "D"); } }
}
"""
    out = _compile(tmp_path, text, ["pss_top::C", "pss_top::D"])
    mod, rt = _load(out, "pssc_rt")
    bus = rt.MemoryBus()
    top = mod.PssTop(bus)
    top.C()
    assert _messages(bus) == ["B", "C"]
    top.D()
    assert _messages(bus) == ["B", "D"]


@pytest.mark.parametrize("base, msg", [
    ("action A { rand int k; exec body { } }", "rand attributes"),
    ("action A { exec pre_solve { int t = 0; } exec body { } }",
     "exec pre_solve"),
    ("action A { activity { } }", "has an activity"),
])
def test_what_a_base_declares_refuses_the_entry(tmp_path, base, msg):
    text = f"""
import std_pkg::*;
component pss_top {{
  {base}
  action A1 : A {{ exec body {{ message(NONE, "A1"); }} }}
}}
"""
    with pytest.raises(Exception) as ei:
        _compile(tmp_path, text, ["pss_top::A1"])
    assert msg in str(ei.value)
    assert "a base of 'pss_top::A1'" in str(ei.value)


def test_a_base_reading_its_attribute_is_refused(tmp_path):
    text = """
import std_pkg::*;
component pss_top {
  action A { int k; exec body { message(NONE, "%d", k); } }
  action A1 : A { exec body { super; } }
}
"""
    with pytest.raises(Exception) as ei:
        _compile(tmp_path, text, ["pss_top::A1"])
    assert "reads its attribute 'k'" in str(ei.value)


def test_a_base_in_an_unrelated_component_is_refused(tmp_path):
    text = """
import std_pkg::*;
component other_c { action B { exec body { message(NONE, "B"); } } }
component pss_top {
  other_c o;
  action C : other_c::B { exec body { super; } }
}
"""
    with pytest.raises(Exception) as ei:
        _compile(tmp_path, text, ["pss_top::C"])
    assert "neither is nor inherits from" in str(ei.value)
