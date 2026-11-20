"""Finding the tests in a translated model.

    A test is a non-abstract action type whose `super` chain reaches
    `pssunit_pkg::test_a`.

That is the whole rule. Two things about the IR shape make it less obvious than
it reads, and both were verified against real output rather than inferred from
the class names:

* **PSS actions are built as `ir.DataTypeClass`, not `ir.DataTypeAction`.**
  `DataTypeAction` belongs to the `@zdc` authoring path. What distinguishes an
  action in the PSS type map is its presence in ``ctx.parent_comp_names`` --
  the same table that answers "which component declares it", so one lookup does
  both jobs.
* **`super` is an unresolved `ir.DataTypeRef`, not a type.** Walking the chain
  means re-resolving ``ref_name`` through ``type_map`` at every hop, and
  ``ref_name`` arrives as written in the source, so it may be unqualified.

WHY THIS MODULE SHOUTS. A name that cannot be resolved is an error here, never
a quiet "not a test". Discovery is the one stage where failing soft produces a
green summary for a suite that never ran: a typo in a base type would delete a
test from the run and report success. Both failure paths below -- unresolvable
and ambiguous -- raise and name what they could not resolve.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: Base types that make an action a test. ``property_test_a`` is not declared
#: yet (it arrives with property tests); naming it here costs nothing and
#: records that the rule was never meant to be single-valued.
TEST_BASES: Tuple[str, ...] = (
    "pssunit_pkg::test_a",
    "pssunit_pkg::property_test_a",
)


class DiscoveryError(Exception):
    """A model could not be interrogated for tests.

    A tool error, not a test failure: it exits 2 and produces no report.
    """


#: The annotation type that carries test metadata, as ``pssunit_pkg`` declares
#: it. Qualified, as `pssc.annotations` requires: the front end resolves a bare
#: ``@test`` written under ``import pssunit_pkg::*`` to this same name.
TEST_ANNOTATION = "pssunit_pkg::test"


@dataclass(frozen=True)
class TestMeta:
    """Metadata from ``@pssunit_pkg::test``.

    Every field carries the annotation's own default, so a test with no
    annotation and a test with a bare ``@pssunit_pkg::test`` are the same thing
    to everything downstream.

    Read off the IR node through `pssc.annotations`, and the front end has
    already folded the declaration's defaults into the values -- so :func:`meta_of` does
    not need to know what any attribute defaults to, and there is exactly one
    place (``pssunit_pkg.pss``) where a default is written down.
    """

    # pytest collects any class named Test*; these are data, not tests.
    __test__ = False

    group: str = ""
    skip: bool = False
    xfail: bool = False
    reason: str = ""
    samples: int = 1
    tags: str = ""


def meta_of(dt) -> TestMeta:
    """The ``@pssunit_pkg::test`` metadata on an action's IR node.

    An attribute the annotation does not carry keeps this dataclass's default.
    That is the same value ``pssunit_pkg.pss`` declares, and the two agreeing is
    asserted rather than assumed -- see the conformance tier.

    An attribute of the wrong type is *ignored*, not coerced: ``skip = 1`` would
    coerce to True and silently delete a test from the run, which is the one
    failure mode discovery must not have. Nothing here can produce that, because
    an annotation parameter's type is fixed by its declaration and the front end
    rejects a non-constant value; the isinstance guards are what make that
    still true if either of those changes.
    """
    from .. import annotations

    p = annotations.of(dt, TEST_ANNOTATION)
    if p is None:
        return TestMeta()

    default = TestMeta()

    def _get(name, kind, fallback):
        v = p.get(name, fallback)
        return v if isinstance(v, kind) and not (
            kind is int and isinstance(v, bool)) else fallback

    return TestMeta(
        group=_get("group", str, default.group),
        skip=_get("skip", bool, default.skip),
        xfail=_get("xfail", bool, default.xfail),
        reason=_get("reason", str, default.reason),
        samples=_get("samples", int, default.samples),
        tags=_get("tags", str, default.tags),
    )


@dataclass(frozen=True)
class TestItem:
    """One discovered test.

    ``comp`` is the root component the test runs under: the component that
    declares it, or a fixture that inherits it (``qname`` is then
    ``<fixture>::<test>`` while ``decl`` names the declaring type). ``loc`` is
    the declaration site, carried for reporting and for the CTRF ``filePath`` /
    ``line`` fields; it is ``None`` when the front end recorded none. The
    fields after ``meta`` are the catalog's (:mod:`.catalog`).
    """

    __test__ = False

    qname: str
    name: str
    comp: str
    loc: Optional[Any] = None
    meta: TestMeta = field(default_factory=TestMeta)
    kind: str = "action"            # or "function"
    decl: str = ""                  # declaring type (or ``<comp>::<fn>``)
    end_line: Optional[int] = None  # last line of the body, for FILE:LINE
    tags: Tuple[str, ...] = ()
    property_runs: Optional[int] = None
    expect_error: Optional[str] = None
    platform_min: Optional[str] = None
    bases: Tuple[str, ...] = ()     # super chain, nearest first
    is_fixture: bool = False        # root derives from pssunit_pkg::fixture_c

    @property
    def type_name(self) -> str:
        """The ``type_map`` key of the declaring action."""
        return self.decl or self.qname

    def __str__(self) -> str:  # what the reports print
        return self.qname


def resolve_type_name(ctx, ref_name: str) -> str:
    """Resolve a raw type reference to its ``type_map`` key.

    The same suffix match ``pssc._resolve_qualified_name`` implements -- but not
    its failure behaviour. That function is best-effort by design and returns
    its input unchanged when nothing matches, which is right for a caller that
    may already hold a final name and wrong for this one.

    An ambiguous match raises for the same reason an unresolved one does:
    picking the first of several candidates decides which of two types a test
    derives from by dictionary order.
    """
    if ref_name in ctx.type_map:
        return ref_name

    matches = [k for k in ctx.type_map if k.endswith(f"::{ref_name}")]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise DiscoveryError(
            f"cannot resolve type '{ref_name}': no such type in the model. "
            f"If this is the base type of a test, that test would silently "
            f"disappear from the run -- check the spelling, and that the "
            f"declaring package is among the sources")
    raise DiscoveryError(
        f"type reference '{ref_name}' is ambiguous: it matches "
        + ", ".join(f"'{m}'" for m in sorted(matches))
        + ". Qualify the reference")


def derives_from(ctx, dt, bases: Tuple[str, ...] = TEST_BASES) -> bool:
    """Whether ``dt``'s super chain reaches any of ``bases``.

    Cycle-guarded. A model with ``a : b`` and ``b : a`` is a bug somewhere
    upstream, but it must not be one that hangs the test runner.
    """
    seen = set()
    while dt is not None:
        ref = getattr(dt, "super", None)
        if ref is None:
            return False
        ref_name = getattr(ref, "ref_name", None)
        if ref_name is None:
            return False
        name = resolve_type_name(ctx, ref_name)
        if name in bases:
            return True
        if name in seen:
            return False
        seen.add(name)
        dt = ctx.type_map.get(name)
    return False


def discover(ctx) -> List[TestItem]:
    """Every test in ``ctx``; see :func:`pssc.testing.catalog.build`.

    Declaration order rather than sorted: it is the order the user wrote, which
    is the order they will look for a failure in. Filtering and grouping happen
    downstream.
    """
    from .catalog import build
    return build(ctx)
