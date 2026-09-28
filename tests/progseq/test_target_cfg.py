"""`target_cfg_pkg` -- the PSS prelude a target injects ahead of the sources.

Three layers, tested separately because they fail differently:

  * the renderer (does the emitted package say what the target claims),
  * `Target.prelude` (does the right target inject, and does `--target-cfg`
    override it),
  * end to end (does a `compile if` in a model actually see it, and does the
    generated output change).

The end-to-end pair is the load-bearing one. The others would all still pass if
the prelude were appended after the sources instead of prepended, which is the
one mistake that is silent in PSS: `compile if` reads constants only from
previously-processed source units (3.1 §19.1.2), so a late prelude does not
error -- the model simply takes its default branch.
"""
import argparse
import textwrap

import pytest

from pssc import driver
from pssc import targets as _targets
from pssc.targets import target_cfg as _tc


# --- the renderer -----------------------------------------------------------

def test_render_emits_every_contract_constant():
    text = _tc.render("op-model-sv",
                      {"HAVE_EVENT_WAIT": True, "HAVE_RUNTIME_SOLVER": False})
    assert "package target_cfg_pkg {" in text
    assert f"TARGET_CFG_VERSION = {_tc.TARGET_CFG_VERSION};" in text.replace("  ", " ")
    assert "HAVE_EVENT_WAIT" in text and "= true;" in text
    assert "HAVE_RUNTIME_SOLVER" in text and "= false;" in text
    # The target is named in the output: a reader who hits a diagnostic in this
    # synthetic source unit needs to know who produced it.
    assert "op-model-sv" in text


def test_render_rejects_a_partial_capability_set():
    """The completeness obligation, enforced.

    A model that has seen TARGET_CFG_VERSION references every contract flag
    without guarding it. Emitting a package with a hole in it would turn that
    into an unresolved reference in the user's model, far from the cause.
    """
    with pytest.raises(_tc.TargetCfgError, match="HAVE_RUNTIME_SOLVER"):
        _tc.render("some-target", {"HAVE_EVENT_WAIT": True})


def test_render_rejects_a_provider_that_omits_the_wait_capability():
    """The other half of the obligation, and the one the rename can break.

    A provider ported from v1 might drop the wait flag entirely rather than
    rename it -- the version marker would still be published, and every model
    that reads `HAVE_EVENT_WAIT` would fail at its own reference site instead
    of here. Naming the missing constant is the whole value of the check.
    """
    with pytest.raises(_tc.TargetCfgError, match="HAVE_EVENT_WAIT"):
        _tc.render("some-target", {"HAVE_RUNTIME_SOLVER": True})


def test_render_rejects_unknown_constants():
    with pytest.raises(_tc.TargetCfgError, match="HAVE_TELEPATHY"):
        _tc.render("some-target", {"HAVE_EVENT_WAIT": True,
                                   "HAVE_RUNTIME_SOLVER": True,
                                   "HAVE_TELEPATHY": True})


@pytest.mark.parametrize("raw,expect", [
    ("HAVE_EVENT_WAIT=true", True), ("HAVE_EVENT_WAIT=1", True),
    ("HAVE_EVENT_WAIT=yes", True), ("HAVE_EVENT_WAIT=false", False),
    ("HAVE_EVENT_WAIT=0", False), ("HAVE_EVENT_WAIT=NO", False),
])
def test_parse_overrides_booleans(raw, expect):
    assert _tc.parse_overrides([raw]) == {"HAVE_EVENT_WAIT": expect}


@pytest.mark.parametrize("raw,match", [
    ("HAVE_EVENT_WAIT", "NAME=VALUE"),
    ("HAVE_EVENT_WAIR=true", "unknown target_cfg constant"),
    ("HAVE_EVENT_WAIT=maybe", "expected a boolean"),
])
def test_parse_overrides_rejects_bad_input(raw, match):
    # A typo'd constant name must not reach the emitted package, where it would
    # be a silent no-op for every model that reads the contract.
    with pytest.raises(_tc.TargetCfgError, match=match):
        _tc.parse_overrides([raw])


# --- the v1 -> v2 rename ----------------------------------------------------
#
# `HAVE_BLOCKING` was not renamed for tidiness; it was answering a broader
# question than any model needed (see target_cfg.py). Silently accepting it as
# a synonym would carry every v1 provider's over-broad `false` into a contract
# where it means something narrower -- and the visible symptom would be an
# operation surface that quietly shrank. So it is an error, with a hint.

def test_stale_v1_constant_is_rejected_with_a_migration_hint():
    with pytest.raises(_tc.TargetCfgError) as exc:
        _tc.parse_overrides(["HAVE_BLOCKING=false"])
    msg = str(exc.value)
    assert "HAVE_BLOCKING" in msg and "HAVE_EVENT_WAIT" in msg
    # Not merely "unknown": a reader must be told what to write instead, and
    # that it is a re-decision rather than a substitution.
    assert "unknown target_cfg constant" not in msg
    assert "not synonyms" in msg


def test_a_provider_publishing_the_v1_constant_is_rejected():
    with pytest.raises(_tc.TargetCfgError, match="HAVE_EVENT_WAIT"):
        _tc.render("legacy-target", {"HAVE_BLOCKING": False,
                                     "HAVE_RUNTIME_SOLVER": False})


def test_contract_version_was_bumped_for_the_rename():
    """The bump is what makes the rename loud rather than silent.

    Without it a model could keep testing `compile has(TARGET_CFG_VERSION)`,
    see version 1, and reference a constant that no longer exists.
    """
    assert _tc.TARGET_CFG_VERSION >= 2
    assert "HAVE_EVENT_WAIT" in _tc.CONTRACT
    assert "HAVE_BLOCKING" not in _tc.CONTRACT


# --- Target.prelude ---------------------------------------------------------

def _ns(**kw):
    return argparse.Namespace(**kw)


def test_op_model_targets_publish_their_capabilities():
    sv = _targets.get("op-model-sv")
    c = _targets.get("op-model-c")
    cpp = _targets.get("op-model-cpp")

    # SV generates `task`s and carries a solver; the C/C++ APIs are plain
    # functions with no coroutine runtime and no solver in the image.
    assert sv.target_cfg == {"HAVE_EVENT_WAIT": True, "HAVE_RUNTIME_SOLVER": True}
    assert c.target_cfg == {"HAVE_EVENT_WAIT": False, "HAVE_RUNTIME_SOLVER": False}
    assert cpp.target_cfg == c.target_cfg


def test_prelude_is_a_single_named_source_unit():
    prelude = _targets.get("op-model-sv").prelude(_ns())
    assert len(prelude) == 1
    name, text = prelude[0]
    # Not a real path, and visibly so: nothing on disk corresponds to it.
    assert name.startswith("<pssc:op-model-sv>")
    assert "package target_cfg_pkg" in text


def test_target_without_published_capabilities_injects_nothing():
    """`target_cfg = None` means "not established", and must stay silent.

    Publishing a guess is worse than publishing nothing: a model that sees the
    version marker trusts every flag beside it.
    """
    tgt = _targets.get("python")
    assert tgt.target_cfg is None
    assert tgt.prelude(_ns()) == []


def test_target_cfg_override_replaces_the_targets_answer():
    prelude = _targets.get("op-model-sv").prelude(
        _ns(target_cfg=["HAVE_EVENT_WAIT=false"]))
    _, text = prelude[0]
    assert "HAVE_EVENT_WAIT" in text
    wait_line = next(ln for ln in text.splitlines() if "HAVE_EVENT_WAIT " in ln)
    assert "= false;" in wait_line
    # The un-overridden flag keeps the target's own answer.
    solver_line = next(ln for ln in text.splitlines() if "HAVE_RUNTIME_SOLVER" in ln)
    assert "= true;" in solver_line


# --- end to end -------------------------------------------------------------

#: A model in the shape the contract prescribes: an adapter that answers both
#: questions once (is it configured, and what does it say), and a component
#: whose content is gated on the result. Deliberately NOT the single-expression
#: `compile has(V) && V` form -- pssparser evaluates both operands eagerly, so
#: that shape is a hard error.
_MODEL = textwrap.dedent("""
    package target_cfg_pkg { }

    package m_cfg_pkg {
        compile if (compile has(target_cfg_pkg::TARGET_CFG_VERSION)) {
            static const bool HAS_EVENT_WAIT = target_cfg_pkg::HAVE_EVENT_WAIT;
        } else {
            static const bool HAS_EVENT_WAIT = true;
        }
    }

    component widget_c {
        function void arm() { }
        export target function arm;

        compile if (m_cfg_pkg::HAS_EVENT_WAIT) {
            function void wait_done() { }
        }
    }
""")


def _generate_sv(tmp_path, target_cfg=None):
    src = tmp_path / "model.pss"
    src.write_text(_MODEL)
    out = tmp_path / "out"
    opts = argparse.Namespace(
        output_dir=str(out), progseq_root="widget_c",
        progseq_package="widget_pkg", progseq_core_copy=False,
        target_cfg=target_cfg)
    driver.compile([str(src)], target="op-model-sv", opts=opts)
    return (out / "widget_pkg.sv").read_text()


def test_injected_prelude_reaches_a_compile_if_in_the_model(tmp_path):
    """op-model-sv publishes HAVE_EVENT_WAIT=true, so the gated function exists.

    This is what proves the prelude is processed BEFORE the sources. Appended
    instead, the adapter would take its `else` branch -- which here happens to
    also be `true`, so the negative case below is the one that pins the order.
    """
    text = _generate_sv(tmp_path)
    assert "arm" in text
    assert "wait_done" in text


def test_target_cfg_override_changes_what_elaborates(tmp_path):
    """Flip the flag and the gated function must disappear.

    Same target, same sources -- only the injected package differs. If the
    prelude were appended, the model would take its default (`true`) branch and
    `wait_done` would still be here.
    """
    text = _generate_sv(tmp_path, target_cfg=["HAVE_EVENT_WAIT=false"])
    assert "arm" in text
    assert "wait_done" not in text


def test_bad_target_cfg_fails_the_compile_with_a_clear_message(tmp_path):
    src = tmp_path / "model.pss"
    src.write_text(_MODEL)
    with pytest.raises(driver.CompileError, match="unknown target_cfg constant"):
        driver.compile([str(src)], target="op-model-sv",
                       opts=argparse.Namespace(
                           output_dir=str(tmp_path / "out"),
                           progseq_root="widget_c",
                           target_cfg=["HAVE_EVENT_WAIR=false"]))
