"""What it means for an operation-model target to be correct, as a runnable suite.

`assert_common_tier` and `assert_deterministic` check properties of the TARGET.
This checks properties of its OUTPUT, and it is the part a plugin author cannot
easily write for themselves: each of these was a real defect in one of pssc's
own backends, found late, and none of them fails a build.

The five checks, and the failure each one is:

* **Every operation appears.** An export API with operations missing -- or with
  none at all -- is what every front-end defect in this generator's history
  looked like: the model translated, the file was written, the run exited 0,
  and the API was empty. Nobody reads a warning printed by a successful build.
* **Every register address matches the offset fold.** The one class of bug a
  golden snapshot can never catch: a wrong address frozen into a snapshot stays
  green forever. The addresses are recomputed from the model here and looked
  for in the output.
* **Nothing undeclared is emitted.** A call the backend does not recognise used
  to be rendered verbatim, naming a function the target language does not have.
  It exits 0 and surfaces much later as an unresolved symbol.
* **Regeneration is byte-stable.** Nondeterminism defeats every incremental
  build downstream and is almost always an unordered set or dict.
* **The path list is a compilation order.** The list a target returns is what a
  build system hands the compiler, in order. A target returning its files in
  creation order instead breaks only when something compiles them together.

`run()` returns a report rather than raising, so a caller can see every failure
at once instead of the first. `assert_conforms()` is the assertion form.
"""
from __future__ import annotations

import dataclasses as dc
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import compile_op_model, op_model_root, op_model_sources

__all__ = ["Check", "ConformanceReport", "run", "assert_conforms"]


@dc.dataclass(frozen=True)
class Check:
    """One check's outcome."""
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"[{mark}] {self.name}" + (f": {self.detail}" if self.detail else "")


@dc.dataclass(frozen=True)
class ConformanceReport:
    target: str
    checks: List[Check]

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if not c.passed]

    def __str__(self) -> str:
        head = (f"conformance: {self.target} -- "
                f"{len(self.checks) - len(self.failures)}/{len(self.checks)} passed")
        return "\n".join([head] + [f"  {c}" for c in self.checks])


# -- the model side ----------------------------------------------------------
#
# Recomputed from the IR rather than read from the output, which is the whole
# point: an expectation derived from the artifact it is checking proves nothing.

def _elaborate(sources: Sequence[str], root_name: str):
    from .. import driver
    from ..targets import op_model as om

    ctx = driver.translate(list(sources))
    if ctx.errors:
        raise ValueError(f"the conformance model failed to translate: "
                         f"{ctx.errors[0]}")
    tm = getattr(ctx, "type_map", {}) or {}
    root = tm.get(root_name)
    if root is None:
        cands = [n for n in tm if n.split("::")[-1] == root_name]
        if len(cands) != 1:
            raise ValueError(f"cannot resolve root '{root_name}' in the model")
        root = tm[cands[0]]
    return om.elaborate(ctx, root, Path("."))


def _expected_operations(model) -> List[str]:
    return [getattr(fn, "name", "") for comp in model.components
            for fn in model.operations(comp)]


def _group_bases(model) -> Dict[int, List[int]]:
    """Every byte base a register group can sit at, by ``id(group)``.

    A group nested inside another can legitimately be addressed two ways, and
    both are correct:

    * relative -- the C++ backend constructs the inner group object at its own
      base, so its registers carry offsets WITHIN the group (`base + 0x1c`);
    * absolute -- the C backend folds the whole path into one flat accessor, so
      the same register reads `base + 0x3c` (0x20 for the bank + 0x1c).

    A check that expected only one of those spellings would report a conforming
    backend as emitting a wrong address. It found exactly that: the first
    version of this file failed `op-model-c` on a register the C backend
    addresses perfectly well.
    """
    from ..targets import progseq_model as pm
    from ..targets.progseq_model import OffsetFoldError

    bases: Dict[int, List[int]] = {id(g): [0] for g in model.reg_groups}
    # `reg_groups` is nested-first, so a parent is visited after its children
    # have their relative-0 entry; two passes settle a two-level nesting and
    # the loop below generalises it without recursing into a cycle.
    for _ in range(len(model.reg_groups)):
        changed = False
        for group in model.reg_groups:
            for field in (getattr(group, "fields", None) or []):
                child = None
                if pm.field_is_reg_group(field):
                    child = field.datatype
                elif pm.field_is_array(field):
                    elem = pm.array_element_type(field)
                    child = elem if pm.is_reg_group(elem) else None
                if child is None or id(child) not in bases:
                    continue
                try:
                    if pm.field_is_array(field):
                        off = model.base_stride_of(group, field.name)[0]
                    else:
                        off = model.offset_of(group, field.name)
                except OffsetFoldError:
                    continue
                for parent_base in list(bases[id(group)]):
                    value = parent_base + off
                    if value not in bases[id(child)]:
                        bases[id(child)].append(value)
                        changed = True
        if not changed:
            break
    return bases


def _expected_addresses(model) -> Dict[str, List[int]]:
    """Register instance name -> the addresses a conforming backend may emit.

    Scalar registers only. An array of registers has an address that depends on
    a runtime index, so there is no single constant to look for; its base and
    stride are checked by `test_op_model_offset_fold.py` in pssc's own suite,
    which can reach into the generated expression rather than grep for a number.
    """
    from ..targets.progseq_model import OffsetFoldError
    from ..targets.reg_layout import is_reserved_name

    bases = _group_bases(model)
    out: Dict[str, List[int]] = {}
    for group in model.reg_groups:
        for field in (getattr(group, "fields", None) or []):
            name = getattr(field, "name", "")
            if not name or is_reserved_name(name):
                continue          # reserved placeholders are never surfaced
            try:
                off = model.offset_of(group, name)
            except OffsetFoldError:
                continue          # an array, or a body this cannot fold
            out.setdefault(name, [])
            for base in bases.get(id(group), [0]):
                if base + off not in out[name]:
                    out[name].append(base + off)
    return out


# -- the checks --------------------------------------------------------------

def _name_appears(text: str, name: str) -> bool:
    """Is ``name`` present as an operation name, allowing a symbol prefix?

    The C backend emits `configure_channel` as `dma_engine_configure_channel`,
    so a plain `\\b` on the left finds nothing -- `_` is a word character. The
    lookbehind rejects an alphanumeric on the left (so `copy` does not match
    inside `fastcopy`) while allowing `_`, which is exactly where a generated
    prefix joins. That does leave `copy` matching `mem_to_mem_copy`; a target
    whose operation name is a suffix of another's, after an underscore, gets a
    pass it did not quite earn. Erring permissive is right for a check whose
    failure mode should be "the operation really is missing".
    """
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}\b", text) is not None


def _check_operations(model, text: str) -> Check:
    expected = _expected_operations(model)
    if not expected:
        return Check("operations-present", False,
                     "the model declares no operations, so this proves nothing")
    missing = [n for n in expected if not _name_appears(text, n)]
    return Check(
        "operations-present", not missing,
        "" if not missing else
        f"{len(missing)} of {len(expected)} model operations do not appear in "
        f"the output: {', '.join(sorted(missing)[:8])}")


def _value_appears(text: str, value: int) -> bool:
    """Does ``value`` appear as a numeric literal, in any language's spelling?

    Strict about the VALUE, permissive about the FORM -- the question is "is the
    number the model computes the number in the file", not "does this backend
    format hex the way pssc does". C writes `0x1c`; SystemVerilog writes
    `64'h1c`; either may pad. A `0x1c` list would have reported the SV backend
    as emitting a wrong address, which is the worst possible outcome for a
    check whose entire purpose is to be believed.

    Offset 0 matches almost any file. Stated rather than special-cased: the
    check is worth having for every other register in the group, and dropping
    the zero offset would hide a backend that emitted no address at all.
    """
    digits = f"{value:x}"
    # The trailing guard is "no more hex digits", not `\b`: C writes `0x3cu`
    # and `0x20UL`, and a word boundary after `3c` never matches the `u`.
    return bool(
        # IGNORECASE: `0x1C` and `0x1c` are the same address.
        re.search(rf"(?:0x|'[hd]|\bh)0*{digits}(?![0-9a-f])", text,
                  re.IGNORECASE)
        or re.search(rf"(?<![\w.])0*{value}(?![0-9])", text))


def _check_addresses(model, text: str) -> Check:
    expected = _expected_addresses(model)
    if not expected:
        return Check("addresses-match-fold", True, "no scalar registers")
    missing = [f"{name}={'/'.join(hex(o) for o in offs)}"
               for name, offs in sorted(expected.items())
               if not any(_value_appears(text, o) for o in offs)]
    return Check(
        "addresses-match-fold", not missing,
        "" if not missing else
        f"offsets recomputed from the model do not appear in the output: "
        f"{', '.join(missing[:8])}")


def _check_no_undeclared_calls(target: str, model) -> Check:
    """Run the legality gate the built-ins run, over the same model.

    Cheap here, and it is the check that separates "this target refuses what it
    cannot lower" from "this target emits it and lets the C compiler find out".
    """
    from ..targets.validate_calls import gate
    try:
        gate(model.root, model.ctx, target, target, model.ctor_names)
    except Exception as e:
        return Check("calls-are-declared", False,
                     f"the model this suite ships is legal for a conforming "
                     f"target, but the gate refused it: {e}")
    return Check("calls-are-declared", True)


def _check_deterministic(target: str, opts: Dict[str, Any]) -> Check:
    from . import assert_deterministic
    try:
        assert_deterministic(target, **opts)
    except AssertionError as e:
        return Check("regeneration-is-byte-stable", False, str(e).strip())
    return Check("regeneration-is-byte-stable", True)


#: Extensions whose files must precede anything that consumes them. A generated
#: SV package importing the core package is the case that motivated it.
_CORE_FIRST_SUFFIXES = (".sv", ".svh")


def _check_path_order(outcome) -> Check:
    """The returned list must be usable as a compilation order.

    Only checkable in general for languages where order is load-bearing. For
    SV, the core package pssc ships has to precede the generated package that
    imports it; for C and C++ headers are included by name and any order works,
    so the check passes vacuously rather than inventing a rule.
    """
    names = outcome.names
    if not names:
        return Check("path-list-is-a-compilation-order", False,
                     "the target returned no paths, so nothing downstream sees "
                     "its output")
    sv = [n for n in names if n.endswith(_CORE_FIRST_SUFFIXES)]
    if len(sv) < 2:
        return Check("path-list-is-a-compilation-order", True,
                     "no order constraint applies to these outputs")
    core = [i for i, n in enumerate(sv) if n.startswith("pssc_")]
    gen = [i for i, n in enumerate(sv) if not n.startswith("pssc_")]
    if core and gen and min(core) > min(gen):
        return Check(
            "path-list-is-a-compilation-order", False,
            f"a generated file precedes the core package it depends on: "
            f"{sv}. The returned order is what a build system compiles in")
    return Check("path-list-is-a-compilation-order", True)


# -- entry points ------------------------------------------------------------

def run(target: str, *, sources: Optional[Sequence[str]] = None,
        root: Optional[str] = None, **opts: Any) -> ConformanceReport:
    """Run every conformance check for ``target`` and report.

    Does not raise on a failed CHECK -- that is what the report is for. It does
    raise if the target cannot compile the model at all, because there is
    nothing to report on then.
    """
    srcs = list(sources or op_model_sources())
    root_name = root or op_model_root
    model = _elaborate(srcs, root_name)

    checks: List[Check] = [_check_no_undeclared_calls(target, model)]

    with compile_op_model(target, sources=srcs, root=root_name,
                          **opts) as outcome:
        text = "\n".join(p.read_text(errors="replace")
                         for p in outcome.outputs if p.is_file())
        checks.append(_check_operations(model, text))
        checks.append(_check_addresses(model, text))
        checks.append(_check_path_order(outcome))

    checks.append(_check_deterministic(
        target, dict(opts, sources=srcs, root=root_name)))

    return ConformanceReport(target=target, checks=checks)


def assert_conforms(target: str, **kwargs: Any) -> ConformanceReport:
    """:func:`run`, as an assertion. Returns the report on success."""
    report = run(target, **kwargs)
    assert report.ok, str(report)
    return report
