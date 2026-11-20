"""``docs/test-internals.rst`` says things that can stop being true (D1.3).

This page describes structure rather than values, so most of it cannot be
asserted -- "isolation is a property of the architecture" is an argument, and an
argument only stops being true deliberately. What *is* asserted is the part a
reader would act on and be wrong: the module each stage lives in, the enum
members, the import paths, and the four extension seams.

The import warning gets the most attention here. ``pssc.testing``'s own
``__init__`` is the plugin-author kit, and the pssunit runner is a set of
submodules that are deliberately not re-exported from it -- so the page tells
the reader that ``from pssc.testing import run`` does not work. If that ever
becomes false, the warning is actively misleading, which is worse than absent.
It is pinned in both directions for that reason.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parents[2] / "docs"
_PAGE = _DOCS / "test-internals.rst"


@pytest.fixture(scope="module")
def text() -> str:
    assert _PAGE.is_file(), f"{_PAGE} is missing"
    return _PAGE.read_text()


def test_the_page_is_in_the_toctree():
    index = (_DOCS / "index.rst").read_text()
    toctree = index.split(".. toctree::", 1)[1].split("\n\n", 2)[1]
    assert "test-internals" in toctree.split()


# --- the pipeline table ------------------------------------------------------

def _stage_table(text: str) -> dict:
    """``stage -> module`` from the pipeline table."""
    block = text.split("The pipeline", 1)[1].split("The split is what", 1)[0]
    return dict(re.findall(r"^(\w+)\s+``([\w.]+)``\s+\S", block, re.M))


def test_every_module_the_pipeline_table_names_is_importable(text):
    """The table is a map from a stage to where its code is. A stale entry
    sends a contributor to a module that does not exist."""
    stages = _stage_table(text)
    assert len(stages) == 6, f"expected six stages, table has {sorted(stages)}"

    for stage, mod in stages.items():
        name = mod if mod.startswith("pssc.") else f"pssc.{mod}"
        try:
            importlib.import_module(name)
        except ImportError as e:  # pragma: no cover - the failure this guards
            pytest.fail(f"stage '{stage}' names {name}, which does not import: {e}")


def test_the_pipeline_arrow_names_the_same_six_stages(text):
    """The page draws the pipeline twice -- as an arrow line and as a table.
    Two copies of one fact, so they are checked against each other."""
    arrow = re.search(r"src-spec -> (.+)", text).group(1)
    drawn = [s.strip() for s in arrow.split("->")]
    assert drawn == list(_stage_table(text)), (
        f"the arrow says {drawn}, the table says {list(_stage_table(text))}")


# --- the import warning ------------------------------------------------------

def test_the_runner_is_still_not_reexported_from_pssc_testing(text):
    """The page's warning, pinned in both directions.

    ``pssc.testing.__init__`` is the plugin kit; the runner is submodules. If
    the runner is ever re-exported the warning becomes a lie, and a reader who
    believes it writes a longer import than they need -- harmless. The other
    direction is not: dropping the warning while the collision persists sends
    them to ``from pssc.testing import run``, which raises.
    """
    import pssc.testing as kit

    # Against ``__all__``, not ``hasattr``: importing ``pssc.testing.discover``
    # binds `discover` as an attribute of the parent package, so once any other
    # test has imported a submodule, `hasattr` reports a re-export that is not
    # one. That is the same confusion the page's warning exists to clear up.
    names = ("run", "discover", "resolve_sources", "write_ctrf")
    exported = set(getattr(kit, "__all__", ()))
    reexported = [n for n in names if n in exported]
    warns = "does not work" in text

    assert bool(reexported) != warns, (
        f"{reexported} are now re-exported -- drop the warning"
        if reexported else
        "the page dropped the warning, but the runner is still not re-exported")


def test_the_import_lines_the_page_shows_actually_run(text):
    """Four ``from ... import ...`` lines in the warning block, executed as
    written -- they are what a reader will copy."""
    block = text.split(".. warning::", 1)[1].split("The pipeline", 1)[0]
    lines = re.findall(r"^\s+(from pssc\.testing\.[\w.]+ import \w+)$",
                       block, re.M)
    assert len(lines) == 4, f"expected four import lines, found {lines}"
    for line in lines:
        exec(line, {})


def test_the_plugin_kit_the_warning_names_is_really_the_kit(text):
    """The warning distinguishes the two by naming three of the kit's own
    exports. If those move, the distinction the page draws is gone."""
    import pssc.testing as kit

    for name in ("compile_op_model", "assert_common_tier",
                 "assert_deterministic"):
        assert hasattr(kit, name), f"pssc.testing no longer exports {name}"
        assert f"``{name}``" in text


# --- the data ----------------------------------------------------------------

def test_the_kinds_the_page_lists_are_the_kinds_that_exist(text):
    """Listed inline as the reasons a test can go wrong. A missing one is a
    failure mode a contributor does not know they can produce."""
    from pssc.testing.runner import Kind

    block = text.split("``Kind`` is *why*", 1)[1].split("kept separate", 1)[0]
    listed = set(re.findall(r"``(\w+)``", block))
    assert listed == {k.name for k in Kind} - {"NONE"}


def test_the_ok_rule_is_the_one_the_page_states(text):
    """Stated as the definition of a green test, and it is what the exit code
    is computed from."""
    from pssc.testing.discover import TestItem
    from pssc.testing.runner import Result, Status

    assert "``status in (PASS, SKIP, XFAIL)``" in text
    green = {s for s in Status
             if Result(item=TestItem(qname="c::a", name="a", comp="c"),
                       status=s).ok}
    assert green == {Status.PASS, Status.SKIP, Status.XFAIL}


def test_the_discovery_rule_names_a_base_that_is_actually_configured(text):
    """The page makes a point of `TEST_BASES` being a tuple that already names
    an undeclared base. That is only worth saying while it is true."""
    from pssc.testing.discover import TEST_BASES

    assert "pssunit_pkg::test_a" in TEST_BASES
    assert "``pssunit_pkg::property_test_a``" in text
    assert "pssunit_pkg::property_test_a" in TEST_BASES


def test_the_dataclass_fields_the_page_lists_exist(text):
    """`TestItem` and `TestMeta` are described field-by-field."""
    import dataclasses as dc

    from pssc.testing.discover import TestItem, TestMeta

    for cls, heading in ((TestMeta, "``TestMeta``"), (TestItem, "``TestItem``")):
        # [0], not [1]: the definition-list body is what directly follows the
        # term, and [1] would be the *next* entry's body.
        block = text.split(heading, 1)[1].split("\n\n", 2)[0]
        listed = set(re.findall(r"``(\w+)``", block))
        real = {f.name for f in dc.fields(cls)}
        assert real <= listed, (
            f"{heading} omits {sorted(real - listed)}")

        # The blocks also name things in prose (`meta_of`, `None`, a CTRF
        # field). Those are allowed, but only if they are real -- so anything
        # that is not a field of the class must at least be a symbol of the
        # module the page is describing.
        import pssc.testing.discover as d
        stray = {n for n in listed - real
                 if not hasattr(d, n) and n not in {"None", "filePath", "line"}}
        assert not stray, f"{heading} names {sorted(stray)}, which do not exist"


# --- the seams ---------------------------------------------------------------

def test_every_extension_seam_the_page_names_is_real(text):
    """The seams section is the page's actionable half: it tells a contributor
    where to change something. A name that has moved sends them nowhere."""
    from pssc.testing import cmd, discover, runner

    block = text.split("Extension seams", 1)[1].split(
        "What is deliberately absent", 1)[0]
    # Term lines, which may name more than one seam (``A`` / ``B``).
    seams = set()
    for line in block.splitlines():
        if line.startswith("``"):
            seams.update(re.findall(r"``(\w+)``", line))
    assert len(seams) >= 6, f"only found {sorted(seams)}"

    where = {
        "TEST_BASES": discover, "TEST_ANNOTATION": discover,
        "TestMeta": discover, "Kind": runner, "run_one": runner,
        "filter_items": cmd,
    }
    for name, mod in where.items():
        assert hasattr(mod, name), f"{mod.__name__}.{name} is gone"
        assert name in seams, f"the page dropped the {name} seam"


def test_samples_is_still_documented_as_unconsumed(text):
    """Listed under "deliberately absent". The same note lives on the
    annotations page with its own guard; this one keeps the two in step."""
    import inspect

    from pssc.testing import runner

    consumed = "samples" in inspect.getsource(runner)
    documented_absent = "**``samples``.**" in text
    assert consumed != documented_absent, (
        "the runner reads `samples` now -- drop it from the absent list"
        if consumed else
        "the page stopped saying `samples` is unconsumed, but it still is")
