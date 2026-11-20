"""`TestMeta` from the IR, and how the runner settles it (M4.3).

Two pieces, both unit-tier because the end-to-end suite proves they work
together and this proves each one alone:

* :func:`pssc.testing.discover.meta_of` -- reading `@pssunit_pkg::test` off an
  IR node, including what it does with an attribute it does not like.
* the runner's `skip` / `xfail` settling -- the step that turns an annotation
  plus a raw outcome into one verdict, so nothing downstream re-derives it.
"""
from __future__ import annotations

import zuspec.ir.core as ir

from pssc.testing.discover import TEST_ANNOTATION, TestItem, TestMeta, meta_of
from pssc.testing.runner import Result, Status, _settle_xfail, _skipped


def _node(**params) -> ir.DataTypeClass:
    dt = ir.DataTypeClass(name="a", super=None)
    if params:
        dt.annotations.append(
            ir.Annotation(type_qname=TEST_ANNOTATION, values=dict(params)))
    return dt


def _item(**meta) -> TestItem:
    return TestItem(qname="c::a", name="a", comp="c", meta=TestMeta(**meta))


# --- meta_of ----------------------------------------------------------------

def test_an_unannotated_node_gets_the_defaults():
    assert meta_of(_node()) == TestMeta()


def test_a_bare_annotation_is_the_same_as_no_annotation():
    """What folding the declaration's defaults in the front end buys.

    If they differed, every consumer would need its own answer to "the author
    wrote `@test` but set nothing", and two consumers would answer differently.
    """
    assert meta_of(_node(**{})) == meta_of(_node())


def test_every_attribute_is_read():
    got = meta_of(_node(group="io", skip=True, xfail=True,
                        reason="because", samples=3))
    assert got == TestMeta(group="io", skip=True, xfail=True,
                           reason="because", samples=3)


def test_an_unrelated_annotation_is_ignored():
    """§7.13 says other annotations must survive; this says they must also not
    be mistaken for this one."""
    dt = _node()
    dt.annotations.append(ir.Annotation(type_qname="other::hint",
                                        values={"skip": True}))
    assert meta_of(dt).skip is False


def test_an_attribute_of_the_wrong_type_keeps_the_default():
    """`skip = 1` must not coerce to True.

    Nothing can currently produce it -- an annotation parameter's type is fixed
    by its declaration and the front end rejects a non-constant value -- but the
    failure it would cause is the one discovery must never have: a test silently
    deleted from the run. The guard is cheap and the failure is not.
    """
    assert meta_of(_node(skip=1)).skip is False
    assert meta_of(_node(group=7)).group == ""
    assert meta_of(_node(samples="lots")).samples == 1


def test_a_bool_is_not_accepted_where_an_int_is_expected():
    """`bool` is a subclass of `int` in Python, so `samples = true` would pass a
    naive isinstance check and silently mean 1."""
    assert meta_of(_node(samples=True)).samples == 1


def test_the_dataclass_defaults_match_the_package_defaults():
    """`TestMeta`'s defaults and `pssunit_pkg.pss`'s are the same values written
    down twice, so they can drift. Asserted against the shipped package text
    rather than restated here, which would just be a third copy.
    """
    import re
    from pssc.testing.sources import pssunit_sources

    text = pssunit_sources()[0].read_text()
    body = text.split("annotation test {", 1)[1].split("}", 1)[0]
    declared = dict(re.findall(r"\w+\s+(\w+)\s*=\s*([^;]+);", body))

    default = TestMeta()
    assert declared["group"].strip() == '""' and default.group == ""
    assert declared["skip"].strip() == "false" and default.skip is False
    assert declared["xfail"].strip() == "false" and default.xfail is False
    assert declared["reason"].strip() == '""' and default.reason == ""
    assert declared["samples"].strip() == "1" and default.samples == 1


# --- skip -------------------------------------------------------------------

def test_a_skipped_result_carries_the_reason_as_its_detail():
    r = _skipped(_item(skip=True, reason="waiting on the model"), seed=7)
    assert r.status is Status.SKIP
    assert r.detail == "waiting on the model"
    assert r.seed == 7


def test_a_skip_with_no_reason_stays_a_skip():
    """Unhelpful, but the annotation permits it -- and inventing text here would
    read as though the author had written it."""
    assert _skipped(_item(skip=True), seed=1).detail == ""


def test_a_skip_is_ok():
    assert _skipped(_item(skip=True), seed=1).ok


# --- xfail ------------------------------------------------------------------

def _settled(status, **meta) -> Result:
    return _settle_xfail(Result(item=_item(**meta), status=status))


def test_an_unannotated_result_is_left_alone():
    r = _settled(Status.FAIL)
    assert r.status is Status.FAIL and r.raw_status is None


def test_an_xfail_that_fails_becomes_xfail_and_is_ok():
    r = _settled(Status.FAIL, xfail=True)
    assert r.status is Status.XFAIL and r.ok


def test_an_xfail_that_errors_also_becomes_xfail():
    """An error is still "it did not work", which is what the mark predicted."""
    assert _settled(Status.ERROR, xfail=True).status is Status.XFAIL


def test_an_xfail_that_passes_becomes_xpass_and_is_not_ok():
    r = _settled(Status.PASS, xfail=True)
    assert r.status is Status.XPASS and not r.ok


def test_an_xpass_explains_itself():
    r = _settled(Status.PASS, xfail=True, reason="stale")
    assert "expected to fail, but passed" in r.detail
    assert "stale" in r.detail


def test_settling_keeps_the_raw_status():
    """So a report can still say *how* an xfail failed."""
    assert _settled(Status.ERROR, xfail=True).raw_status is Status.ERROR


def test_a_skip_is_not_rewritten_by_xfail():
    """A test that is both skipped and xfail was never run, so there is no
    outcome for the mark to be right or wrong about."""
    r = _settle_xfail(_skipped(_item(skip=True, xfail=True), seed=1))
    assert r.status is Status.SKIP
