"""``docs/annotations.rst`` says things that can stop being true (D3.2).

The page is a reference, so it restates values that live somewhere else: the
`@pssunit_pkg::test` attribute defaults, the diagnostic text for a non-constant
parameter, and the flags each attribute drives. Every one of those is a copy,
and a copy in prose is the kind that nothing fails on when it drifts -- the
reader is simply told something false, and finds out an hour later.

So each restated value is asserted against its source of truth rather than
against a second copy written here. What is *not* checked is the prose: the
argument for why annotations are carried uninterpreted is not a value and cannot
drift silently, only deliberately.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parents[2] / "docs"
_PAGE = _DOCS / "annotations.rst"


@pytest.fixture(scope="module")
def text() -> str:
    assert _PAGE.is_file(), f"{_PAGE} is missing"
    return _PAGE.read_text()


def test_the_page_is_in_the_toctree():
    """A page outside the toctree is not published, and Sphinx says so only as
    a warning -- which is to say, not at all in a build nobody reads."""
    index = (_DOCS / "index.rst").read_text()
    toctree = index.split(".. toctree::", 1)[1].split("\n\n", 2)[1]
    assert "annotations" in toctree.split()


# --- the attribute table ----------------------------------------------------

def _table_rows(text: str) -> dict:
    """The ``attribute -> (type, default)`` rows of the simple table."""
    rows = {}
    for line in text.splitlines():
        m = re.match(r"^``(\w+)``\s+(\w+)\s+(``.+?``)\s+\S", line)
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3).strip("`"))
    return rows


def test_the_table_lists_every_attribute_the_package_declares(text):
    """A table missing a row is how an attribute becomes undiscoverable."""
    from pssc.testing.sources import pssunit_sources

    body = pssunit_sources()[0].read_text().split(
        "annotation test {", 1)[1].split("}", 1)[0]
    declared = set(re.findall(r"\w+\s+(\w+)\s*=", body))

    assert set(_table_rows(text)) == declared


def test_the_documented_defaults_are_the_declared_defaults(text):
    """Against `TestMeta`, which is itself pinned to the PSS source by
    `test_the_dataclass_defaults_match_the_package_defaults`. Two links in a
    chain beat a third independent copy."""
    from pssc.testing.discover import TestMeta

    def rendered(v):
        """A Python default as PSS spells it.

        Dispatched on type, not by lookup: `{True: "true"}[1]` hits, because
        `bool` subclasses `int` -- the same trap `meta_of` guards against, and
        it would have quietly rendered `samples = 1` as ``true`` here.
        """
        if isinstance(v, bool):
            return "true" if v else "false"
        return f'"{v}"' if isinstance(v, str) else str(v)

    default = TestMeta()
    for name, (_, shown) in _table_rows(text).items():
        assert shown == rendered(getattr(default, name)), \
            f"the table says {name} defaults to {shown}"


def test_the_documented_types_are_the_declared_types(text):
    from pssc.testing.discover import TestMeta

    py = {"string": str, "bool": bool, "int": int}
    default = TestMeta()
    for name, (kind, _) in _table_rows(text).items():
        assert type(getattr(default, name)) is py[kind], \
            f"the table calls {name} a {kind}"


# --- everything else the page restates --------------------------------------

def test_the_flags_the_page_names_exist(text):
    """`--group`, `--noskip`, `--runxfail` are named as the effect of an
    attribute. A page that documents a flag the CLI dropped is worse than one
    that documents no flags at all."""
    import argparse

    from pssc.cli import build_parser

    named = set(re.findall(r"``(--[\w-]+)``", text))
    assert named, "the page names no flags; this test has stopped checking"

    known = set()
    for action in _test_subparser(build_parser())._actions:
        known.update(action.option_strings)
    assert named <= known, f"documented but absent: {sorted(named - known)}"


def _test_subparser(parser):
    import argparse
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["test"]
    raise AssertionError("no `test` subcommand")


def test_a_constant_expression_parameter_folds(text, tmp_path):
    """The page says the front end folds a parameter; checked against a real
    translation, read the way the runner reads it."""
    from pssc import annotations
    from pssc.driver import translate

    assert "1 + 1" in text
    src = tmp_path / "s.pss"
    src.write_text("""
package p {
    annotation test { int samples = 1; }
}
component c {
    @p::test{.samples = 1 + 1}
    action a { }
}
""")
    ctx = translate([str(src)])
    assert not ctx.errors
    assert annotations.of(ctx.type_map["c::a"], "p::test")["samples"] == 2


def test_the_page_records_that_samples_is_not_yet_consumed(text):
    """`.samples` is carried to `TestMeta` and nothing reads it.

    Documented as a gap, because a reader who sets it and sees one run has been
    told the tool is broken. This test is what makes the note come out when the
    runner starts honouring it.
    """
    from pssc.testing import runner

    unused = "samples" not in Path(runner.__file__).read_text()
    documented = "not yet consumed" in text
    assert unused == documented, (
        "`.samples` is now read by the runner -- update the table"
        if documented else
        "the table no longer says `.samples` is unconsumed, but nothing reads it")
