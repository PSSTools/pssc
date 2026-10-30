"""The call-legality registry: the tier contract, and the manifest's honesty.

Two tests here carry the design rather than a behaviour:

  * `test_every_target_renders_the_common_set` is the Tier 1 contract. Without
    it "COMMON" is an aspiration each backend independently fails to meet --
    which was the state that produced four disagreeing copies of the builtin
    list (docs §3.0).

  * `test_manifest_matches_the_front_end_stdlib` is the check that keeps the
    Python manifest from drifting from the language. Tier 0 SHOULD be derived
    from the front end's declarations rather than restated here; it is not,
    because the declarations do not survive into the IR -- `type_map` carries
    `addr_reg_pkg::reg_group_c` with an EMPTY `functions` list, and package-scope
    functions like `print`/`read32` are not represented at all. Until ast2ir
    retains them (docs §10, item 8), a cross-check against the stdlib SOURCE is
    what makes the restatement safe: drift fails here instead of shipping.
"""
import inspect
import os
import re

import pytest

from pssc.targets.call_legality import (COMMON, Ctx, Disposition, Entry,
                                        LegalityError, Outcome, TIER0,
                                        classify, entries_for, extensions_for,
                                        register_extension, registered_targets,
                                        renderable, subset_of_common)
from .sv_text import sv_package_text

_STDLIB = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "pssparser", "src", "stdlib"))


# --- the tier contract ------------------------------------------------------

def test_every_target_renders_the_common_set():
    """A target may EXTEND the common set. It shrinks it only as a declared
    subset target (`register_extension(..., subset=True)`)."""
    for target in registered_targets():
        missing = set(COMMON) - renderable(target) - subset_of_common(target)
        assert not missing, (
            f"{target} does not render common calls: {sorted(missing)}")


def test_extensions_do_not_contradict_common():
    """A Tier 2 entry must not re-declare a Tier 1 name as unsupported --
    that would be shrinking the common set through the back door."""
    for target in registered_targets():
        ext = extensions_for(target)
        for name, entry in ext.items():
            if name in COMMON and name not in subset_of_common(target):
                assert not entry.unsupported, (
                    f"{target} marks common call '{name}' unsupported")


def test_extensions_keyed_by_canonical_name():
    """A Tier 2 set reachable only through an alias is a Tier 2 set nobody
    reaches.

    `_EXTENSIONS` was keyed `c-progseq`/`cpp-progseq` after the family was
    renamed to `op-model-<kind>`, so `entries_for("op-model-c")` returned Tier 0
    + COMMON and nothing else: every C extension -- `print`, `try_get`,
    `try_put` -- classified as UNKNOWN. Nothing noticed, because the validation
    pass ran for SV only. Turning that pass on for C (P1.T1) would have turned
    every legal `print` in a model into a compile error pointing at the model.
    """
    import pssc.targets as _t

    for key in registered_targets():
        assert key in _t.list_targets(), (
            f"extension key '{key}' is not a registered canonical target name")


def test_entries_for_resolves_alias():
    """The old names stay usable: they are registered aliases of the same
    target, and a stale flow or command line must not silently lose Tier 2."""
    for alias, canonical in (("c-progseq", "op-model-c"),
                             ("cpp-progseq", "op-model-cpp"),
                             ("sv-progseq", "op-model-sv")):
        assert entries_for(alias) == entries_for(canonical), alias


def test_c_target_has_tier2_entries_under_its_canonical_name():
    """The concrete regression: this returned zero Tier 2 entries."""
    for target in ("op-model-c", "op-model-cpp"):
        extra = set(entries_for(target)) - set(COMMON) - set(TIER0)
        assert extra, f"{target} has no Tier 2 entries"
        assert classify("print", context=Ctx.TARGET, target=target).ok


def test_an_unknown_target_name_falls_back_rather_than_raising():
    """Legality is not where a bad target name is diagnosed -- that happens
    where targets are resolved, with the list of available ones."""
    assert entries_for("no-such-target") == entries_for("not-a-target-either")


def test_sv_extends_beyond_common():
    """The SV target is the reference backend; if it added nothing, the tiering
    would be describing a distinction that does not exist."""
    assert renderable("op-model-sv") > set(COMMON)


def test_c_cannot_do_BLOCKING_channels_and_says_why():
    """The C backend's real capability gap, and the reason the tiers exist at
    all rather than one flat list.

    Updated 2026-08-13 (C3). This test used to assert that `try_put` was
    unsupported. That stopped being true when the C target grew a depth-1
    `channel_c` runtime, and the registry said otherwise for long enough to
    matter -- its reason string still pointed at a `_reject_channels` function
    that had been deleted. The gap is now the BLOCKING pair only, and the
    distinction is the point: `try_*` cannot block, so it needs no scheduler.
    """
    for blocking in ("get", "put"):
        r = classify(blocking, context=Ctx.TARGET, target="c-progseq")
        assert r.outcome is Outcome.UNSUPPORTED_HERE, blocking
        assert "suspend" in r.message, r.message

    for nonblocking in ("try_get", "try_put"):
        assert classify(nonblocking, context=Ctx.TARGET, target="c-progseq").ok, \
            nonblocking

    assert classify("put", context=Ctx.TARGET, target="op-model-sv").ok


def test_the_registry_agrees_with_what_the_c_emitter_actually_renders():
    """DOC-4's point, as a test rather than a promise.

    The registry is documentation that the build consults. When it drifts from
    the emitter, the two disagree silently: nothing consults the registry on the
    C path yet (call validation is wired for SV only), so a wrong entry sits
    inert until the day it is wired up and then refuses a call the emitter has
    been lowering correctly for months. That is exactly what had happened here.

    So: assert the two agree on the channel four, against the emitter's own
    dispatch rather than against a second copy of the list.
    """
    from pssc.targets.c.lower_progseq import CBodyEmitter

    src = inspect.getsource(CBodyEmitter._chan_call)
    for nonblocking in ("try_get", "try_put"):
        assert f'"{nonblocking}"' in src, (
            f"registry says c-progseq renders {nonblocking}, but the emitter's "
            f"channel dispatch does not mention it")
        assert classify(nonblocking, context=Ctx.TARGET, target="c-progseq").ok

    # ...and the blocking pair is refused in BOTH places.
    assert '("get", "put")' in src or '"get"' in src
    for blocking in ("get", "put"):
        assert classify(blocking, context=Ctx.TARGET,
                        target="c-progseq").outcome is Outcome.UNSUPPORTED_HERE


# --- the four outcomes ------------------------------------------------------

def test_supported():
    r = classify("message", context=Ctx.SOLVE, target="op-model-sv")
    assert r.ok and r.entry.disposition is Disposition.UTILITY


def test_unsupported_here_names_the_prerequisite():
    """Math is registered, not omitted: the diagnostic must say it is a real
    core-library function blocked on float64, not 'unknown call'."""
    r = classify("sqrt", context=Ctx.TARGET, target="op-model-sv")
    assert r.outcome is Outcome.UNSUPPORTED_HERE
    assert "float64" in r.message and "21.5.2" in r.message


def test_wrong_context_for_a_bus_access_in_a_constructor():
    """addr_reg_pkg declares read32 as a plain `function`, so the FRONT END
    permits this. The lowering cannot: there is no bus while the model is being
    constructed. The message has to explain itself, not just cite a rule."""
    r = classify("read32", context=Ctx.SOLVE, target="op-model-sv")
    assert r.outcome is Outcome.WRONG_CONTEXT
    assert "target context" in r.message


def test_unknown_points_at_the_import_route():
    r = classify("my_helper", context=Ctx.TARGET, target="op-model-sv")
    assert r.outcome is Outcome.UNKNOWN
    assert "import" in r.message


def test_model_names_are_not_in_any_tier():
    """An operation of the component being lowered, an import function and a
    sub-component constructor are all model names, resolved from the model."""
    assert classify("transfer_single", context=Ctx.TARGET, target="op-model-sv",
                    model_ops=frozenset({"transfer_single"})).ok
    assert classify("dma_bfm", context=Ctx.TARGET, target="op-model-sv",
                    imports=frozenset({"dma_bfm"})).ok
    assert classify("initialize", context=Ctx.SOLVE, target="op-model-sv",
                    subcomps=frozenset({"initialize"})).ok


# --- keeping the manifest honest -------------------------------------------

def _declared_in_stdlib():
    """Free-function names declared by the front end's stdlib SOURCE."""
    names = set()
    pat = re.compile(
        r"^\s*(?:(?:solve|target|pure|import)\s+)*function\s+[\w:\[\]<>, ]+?"
        r"(\w+)\s*\(", re.M)
    for fn in ("std_pkg.pss", "addr_reg_pkg.pss", "sync_pkg.pss"):
        path = os.path.join(_STDLIB, fn)
        if not os.path.exists(path):
            continue
        src = re.sub(r"//[^\n]*", "", open(path).read())
        names |= set(pat.findall(src))
    return names


@pytest.mark.skipif(not os.path.isdir(_STDLIB), reason="stdlib source not present")
def test_manifest_matches_the_front_end_stdlib():
    """Every stdlib-declared function must be in SOME tier.

    A name the front end resolves but no tier knows falls straight through to
    the verbatim fallthrough -- which is how `urandom()` would have reached
    generated SystemVerilog as a call to a function that does not exist.
    """
    known = set(COMMON) | set(TIER0)
    for target in registered_targets():
        known |= set(extensions_for(target))
    # Declared on components (reg_c, channel_c) rather than at package scope,
    # and already covered by the REG/CHANNEL dispositions.
    known |= {"get_handle", "add_addr_space"}
    missing = _declared_in_stdlib() - known
    assert not missing, (
        f"declared by the stdlib but in no tier, so they would fall through to "
        f"verbatim emission: {sorted(missing)}")


def test_error_and_fatal_are_now_in_the_stdlib():
    """PSS 3.1 §21.3 puts error() and fatal() in std_pkg; Phase 1a (docs §9)
    is done and the front end now declares them, matching what COMMON always
    stated about the language. (Previously this asserted the inverse -- that
    they were still missing -- as a reminder that the front end had not
    caught up.)"""
    declared = _declared_in_stdlib()
    if not declared:
        pytest.skip("stdlib source not readable")
    assert {"error", "fatal"} <= declared


def test_tier0_entries_all_carry_a_reason():
    """An entry with no reason is indistinguishable from a supported one, and
    would produce a diagnostic that says nothing."""
    for name, entry in TIER0.items():
        assert entry.unsupported, f"Tier 0 entry '{name}' has no reason"


def test_entries_for_merges_in_precedence_order():
    """COMMON must not be shadowed by Tier 0, and a target extension must be
    able to make a Tier 0 name renderable."""
    sv = entries_for("op-model-sv")
    assert not sv["make_handle_from_handle"].unsupported
    assert not sv["print"].unsupported
    assert entries_for("c-progseq")["format"].unsupported


# --- the gate, end to end ---------------------------------------------------

# `read_bytes` is DECLARED by addr_reg_pkg, so the front end resolves it -- which
# is what makes this a test of the backend gate rather than of name resolution.
# (`sqrt` would not do: the stdlib does not declare the math functions, so the
# front end rejects it first. That is the Tier 0 distinction in action.)
_BAD_MODEL = """
import addr_reg_pkg::*;

component gate_c {
    target function void go(addr_handle_t h) {
        write_bytes(h, null);
    }
}
"""


def test_unlowerable_call_fails_the_build_and_writes_nothing(tmp_path):
    """The whole point: a call with no lowering must stop the compile, and must
    not leave a partial artifact behind for a later build to treat as
    up-to-date.
    """
    import argparse
    from pssc import driver

    src = tmp_path / "gate.pss"
    src.write_text(_BAD_MODEL)
    out = tmp_path / "out"
    out.mkdir()
    ns = argparse.Namespace(progseq_root="gate_c", progseq_package="gate_pkg",
                            output_dir=str(out))
    with pytest.raises(driver.CompileError) as ei:
        driver.compile([str(src)], target="op-model-sv", opts=ns)

    joined = "\n".join(ei.value.errors)
    assert "write_bytes" in joined and "list" in joined
    assert "gate_c::go" in joined, "the diagnostic must locate the call"
    assert not list(out.glob("gate_pkg.sv")), "a rejected compile wrote output"


def test_a_lowerable_model_still_generates(tmp_path):
    """The gate must not be so eager that it rejects the models that work --
    the risk any newly-added gate carries."""
    import argparse
    from pssc import driver
    model = os.path.join(os.path.dirname(__file__), "data", "offset_fold.pss")

    out = tmp_path / "ok"
    out.mkdir()
    ns = argparse.Namespace(progseq_root="fold_top_c", progseq_package="fold_top_pkg",
                            output_dir=str(out))
    res = driver.compile([model], target="op-model-sv", opts=ns)
    assert res.outputs


# --- the gate runs for every op-model backend, not just SV (P1.T1) ----------
#
# Until P1.T1 the gate ran for `op-model-sv` alone -- the backend with the
# WIDEST lowering. The two with the narrowest, C and C++, emitted whatever they
# met. `urandom()` is the case that reaches them: PSS declares it (std_pkg
# 21.4), the SV target renders it, so it passes the front end and arrives at a
# backend that has no PRNG in the image.

_URANDOM_MODEL = """
import std_pkg::*;
component gate_c {
    target function int roll() {
        int v;
        v = urandom();
        return v;
    }
    export target function roll;
}
"""


@pytest.mark.parametrize("target,language,artifact", [
    ("op-model-c", "C", "gate.h"),
    ("op-model-cpp", "C++", "gate.hpp"),
])
def test_the_c_and_cpp_targets_gate_an_unlowerable_call(
        tmp_path, target, language, artifact):
    """The gap P1.T1 closed: these two used to emit the call and exit 0."""
    import argparse
    from pssc import driver

    src = tmp_path / "gate.pss"
    src.write_text(_URANDOM_MODEL)
    out = tmp_path / target
    out.mkdir()
    ns = argparse.Namespace(progseq_root="gate_c", progseq_package="gate_pkg",
                            c_prefix="gate", cpp_namespace="gate",
                            output_dir=str(out))
    with pytest.raises(driver.CompileError) as ei:
        driver.compile([str(src)], target=target, opts=ns)

    assert language in str(ei.value)
    joined = "\n".join(ei.value.errors)
    assert "urandom" in joined
    assert "gate_c::roll" in joined, "the diagnostic must locate the call"
    assert not (out / artifact).exists(), (
        "a rejected compile left an artifact a later build would treat as "
        "up-to-date")


def test_the_c_gate_does_not_reject_what_the_c_backend_renders(tmp_path):
    """The other half of P1.T1, and the reason P1.T2 had to land first.

    With `_EXTENSIONS` still keyed by the old alias, `entries_for("op-model-c")`
    returned no Tier 2 entries at all -- so turning this gate on would have
    rejected every legal `print` and `try_get` in every model, pointing the
    user at their model for a compiler bug.
    """
    for name in ("print", "try_get", "try_put"):
        assert classify(name, context=Ctx.TARGET, target="op-model-c").ok, name


def test_the_same_model_generates_for_sv(tmp_path):
    """The point of the tiers, and the check that the gate is not simply
    strict: `urandom` is renderable by the SV backend, so the identical model
    that the two tests above refuse must still generate here."""
    import argparse
    from pssc import driver

    src = tmp_path / "gate.pss"
    src.write_text(_URANDOM_MODEL)
    out = tmp_path / "sv"
    out.mkdir()
    ns = argparse.Namespace(progseq_root="gate_c", progseq_package="gate_pkg",
                            output_dir=str(out))
    res = driver.compile([str(src)], target="op-model-sv", opts=ns)
    assert res.outputs
    assert "urandom" in sv_package_text(out / "gate_pkg.sv")


# --- Tier 2 registration (P3.T4) --------------------------------------------
#
# The tier contract used to be a test over a literal dict in call_legality.py.
# That holds the built-ins and nothing else: a plugin declaring its own Tier 2
# was never checked, and the first thing a plugin author reaches for is
# "COMMON is too strict for my target, I'll mark that one unsupported" -- which
# is precisely the move the tiers exist to prevent.

@pytest.fixture
def legality_sandbox():
    """Register extensions under throwaway names and take them back out."""
    import pssc.targets.call_legality as cl
    saved = {k: dict(v) for k, v in cl._EXTENSIONS.items()}
    saved_subsets = dict(cl._SUBSETS)
    yield cl
    cl._EXTENSIONS.clear()
    cl._EXTENSIONS.update(saved)
    cl._SUBSETS.clear()
    cl._SUBSETS.update(saved_subsets)


def test_extension_cannot_shrink_common(legality_sandbox):
    common_name = next(iter(COMMON))
    with pytest.raises(LegalityError) as exc:
        register_extension("x-shrinker", [
            Entry(common_name, Disposition.UTILITY, frozenset({Ctx.TARGET}),
                  unsupported="my target is special"),
        ])
    msg = str(exc.value)
    assert common_name in msg and "my target is special" in msg
    assert "subset=True" in msg
    assert "x-shrinker" not in registered_targets(), "and nothing registered"


# --- subset targets (PC-18b) ------------------------------------------------

def _narrow(name="message", reason="no console on this HAL"):
    return Entry(name, Disposition.UTILITY, frozenset({Ctx.TARGET}),
                 unsupported=reason)


def test_a_subset_target_may_narrow_common(legality_sandbox):
    register_extension("x-subset", [_narrow()], subset=True)
    assert subset_of_common("x-subset") == frozenset({"message"})
    assert "message" not in renderable("x-subset")
    r = classify("message", context=Ctx.TARGET, target="x-subset")
    assert r.outcome is Outcome.UNSUPPORTED_HERE
    assert "no console on this HAL" in r.message


def test_a_subset_needs_a_reason(legality_sandbox):
    with pytest.raises(LegalityError, match="blank reason"):
        register_extension("x-subset", [_narrow(reason="   ")], subset=True)
    assert "x-subset" not in registered_targets()


def test_a_conforming_target_has_no_subset(legality_sandbox):
    register_extension("x-adder", [], subset=True)
    assert subset_of_common("x-adder") == frozenset()
    assert subset_of_common("op-model-c") == frozenset()


def test_inheriting_a_subset_carries_its_narrowing(legality_sandbox):
    register_extension("x-subset", [_narrow()], subset=True)
    register_extension("x-derived", [], inherit="x-subset")
    assert subset_of_common("x-derived") == frozenset({"message"})
    # supporting the name again lifts it
    register_extension("x-derived", [
        Entry("message", Disposition.UTILITY, frozenset({Ctx.TARGET}))],
        inherit="x-subset", replace=True)
    assert subset_of_common("x-derived") == frozenset()
    # a further narrowing still needs subset=True
    other = next(n for n in sorted(COMMON) if n != "message")
    with pytest.raises(LegalityError):
        register_extension("x-derived2", [_narrow(other)],
                           inherit="x-subset")


def test_assert_common_tier_accepts_a_declared_subset(legality_sandbox):
    from pssc.testing import assert_common_tier
    register_extension("x-subset", [_narrow()], subset=True)
    assert_common_tier("x-subset")


def test_an_extension_may_add_to_common(legality_sandbox):
    register_extension("x-adder", [
        Entry("print", Disposition.UTILITY, frozenset({Ctx.TARGET})),
    ])
    assert "print" in extensions_for("x-adder")
    # ...and the Tier 1 contract still holds for it
    assert not (set(COMMON) - renderable("x-adder"))


def test_an_extension_may_make_a_tier0_name_renderable(legality_sandbox):
    tier0_name = next(n for n, e in TIER0.items() if e.unsupported)
    register_extension("x-riser", [
        Entry(tier0_name, Disposition.UTILITY, frozenset({Ctx.TARGET})),
    ])
    assert tier0_name in renderable("x-riser")


def test_inherit_chains(legality_sandbox):
    register_extension("x-base", [
        Entry("print", Disposition.UTILITY, frozenset({Ctx.TARGET})),
        Entry("urandom", Disposition.UTILITY, frozenset({Ctx.TARGET}),
              unsupported="no PRNG in the base"),
    ])
    register_extension("x-derived", [
        Entry("urandom", Disposition.UTILITY, frozenset({Ctx.TARGET})),
    ], inherit="x-base")

    derived = extensions_for("x-derived")
    assert "print" in derived                       # inherited
    assert not derived["urandom"].unsupported       # overridden
    assert extensions_for("x-base")["urandom"].unsupported, "base untouched"


def test_inherit_is_a_snapshot_not_a_live_link(legality_sandbox):
    """The reason op-model-cpp is spelled out rather than dict()-copied from C.

    If inheritance were live, a call the C backend learns to render tomorrow
    would silently become legal for every derived target -- including one whose
    author never saw the change and whose emitter has no case for it. That is a
    wrong-code bug, reported as an unresolved symbol in somebody else's build.
    """
    register_extension("x-snap-base", [
        Entry("print", Disposition.UTILITY, frozenset({Ctx.TARGET})),
    ])
    register_extension("x-snap-derived", [], inherit="x-snap-base")
    register_extension("x-snap-base", [
        Entry("print", Disposition.UTILITY, frozenset({Ctx.TARGET})),
        Entry("format_string", Disposition.UTILITY, frozenset({Ctx.TARGET})),
    ], replace=True)
    assert "format_string" not in extensions_for("x-snap-derived")


def test_inherit_from_an_unregistered_target_is_an_error(legality_sandbox):
    with pytest.raises(LegalityError) as exc:
        register_extension("x-orphan", [], inherit="x-nope")
    assert "x-nope" in str(exc.value)


def test_re_registering_needs_replace(legality_sandbox):
    register_extension("x-twice", [])
    with pytest.raises(LegalityError) as exc:
        register_extension("x-twice", [])
    assert "replace=True" in str(exc.value)
    register_extension("x-twice", [], replace=True)      # deliberate: fine


def test_the_builtins_registered_through_the_public_call():
    assert set(registered_targets()) == {"op-model-sv", "op-model-c",
                                         "op-model-cpp", "op-model-py"}


# --- the registry as the dispatch table (P7.T4) -----------------------------
#
# Before this, the registry was a list checked by one pass and re-stated as an
# ordered chain of `if` in each emitter. The chain decided; the table only got
# to say whether the compile should proceed. These tests are on the other
# arrangement: the table decides, and the emitter's hooks are what it decides
# BETWEEN.

def _c_dispositions():
    """Every disposition an `op-model-c` entry can actually produce, plus the
    three the model's own names carry (MODEL_OP / IMPORT / SUBCOMP_CTOR, which
    are in no tier -- see `classify`)."""
    from pssc.targets.call_legality import Disposition as D

    out = {e.disposition for e in entries_for("op-model-c").values()
           if not e.unsupported}
    return out | {D.MODEL_OP, D.IMPORT, D.SUBCOMP_CTOR}


def test_dispatch_matches_registry():
    """Every disposition the C registry can hand the emitter has a hook.

    This is the loud error the arrangement buys: a Tier-2 entry added with a
    disposition nothing renders used to fall out of the emitter's chain as
    "no lowering for this call", naming the call rather than the gap.
    """
    from pssc.targets.c.lower_progseq import _CtorEmitter

    hooks = _CtorEmitter.call_hooks()
    missing = sorted(d.value for d in _c_dispositions() if d.value not in hooks)
    assert not missing, (
        f"op-model-c can classify a call as {missing} and has no "
        f"call_<disposition> to render it")


def test_every_dispatch_hook_is_reachable():
    """The other direction: a hook for a disposition no entry produces is dead
    code that reads as coverage."""
    from pssc.targets.c.lower_progseq import _CtorEmitter

    reachable = {d.value for d in _c_dispositions()}
    extra = sorted(set(_CtorEmitter.call_hooks()) - reachable)
    assert not extra, (
        f"op-model-c renders {extra}, which no entry visible to it produces")


def test_an_operation_body_renders_fewer_dispositions_than_a_constructor():
    """The ctor-only forms are ctor-only. A body emitter that could render
    `set_handle` would silently accept a structural call in an operation."""
    from pssc.targets.c.lower_progseq import CBodyEmitter, _CtorEmitter

    body = set(CBodyEmitter.call_hooks())
    assert "structural" not in body and "subcomp_ctor" not in body
    assert body < set(_CtorEmitter.call_hooks())


def test_a_tier2_entry_with_no_hook_is_a_loud_error(legality_sandbox):
    """Registered against a throwaway target so the failure is the DISPATCH's,
    not a missing entry's."""
    from pssc.targets.body_walker import CallDispatch

    register_extension("x-dispatch", [
        Entry("frob", Disposition.STRUCTURAL, frozenset({Ctx.TARGET})),
    ])

    class _Emitter(CallDispatch):
        legality_target = "x-dispatch"
        call_context = Ctx.TARGET

    class _Call:
        func = type("F", (), {"attr": "frob"})()

    with pytest.raises(ValueError) as exc:
        _Emitter().expr_call(_Call())
    msg = str(exc.value)
    assert "classified STRUCTURAL" in msg
    assert "call_structural()" in msg


def test_a_call_the_gate_would_have_refused_says_so(legality_sandbox):
    """The emitter is downstream of `validate_calls`. If an unclassifiable call
    reaches it anyway, that is a compiler bug and the message says which one --
    rather than emitting text naming a function nothing declares."""
    from pssc.targets.body_walker import CallDispatch

    register_extension("x-gate", [])

    class _Emitter(CallDispatch):
        legality_target = "x-gate"
        call_context = Ctx.TARGET

    class _Call:
        func = type("F", (), {"attr": "no_such_function"})()

    with pytest.raises(ValueError) as exc:
        _Emitter().expr_call(_Call())
    assert "reached the emitter unclassified (unknown)" in str(exc.value)
    assert "before any file was opened" in str(exc.value)
