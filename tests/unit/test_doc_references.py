"""Every ``docs/...`` path named in the tree must be a file that exists.

Nineteen design notes and implementation plans were cited by roughly forty
docstrings, comments and doc pages, and none of them was in the repository. The
``docs/progseq.rst`` quick-start told a user to compile two ``.pss`` files from a
directory that had never been committed. Nothing failed, because nothing checked
-- a citation is prose, and prose does not get imported.

That is what makes this worth a test rather than a cleanup. A stale pointer in a
shipped docstring costs a reader the time it takes to conclude the docs are
wrong, and then it costs them the parts that were still true. The failure is
silent and the decay is one-way, so the only thing that holds it is a check.

Scope is deliberately narrow, to stay a check rather than a style opinion:

* Only ``docs/``-rooted paths, which are the ones that name a location in THIS
  repository. A bare ``foo.md`` might be anything.
* ``docs/design/`` is exempt as a source of references. Those are archived
  working notes, each banner-marked as written against the tree of its own
  date; rewriting their internal citations would make them say something their
  authors did not. They are still checked as TARGETS.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: Files that can carry a reference. Restricted to text we author.
_SUFFIXES = (".py", ".rst", ".md", ".sv", ".h", ".hpp", ".c", ".yaml", ".cfg")

#: A doc path rooted at THIS repository. Stops at the extension so trailing
#: prose -- a possessive or a sentence-ending period right after the suffix --
#: is not swallowed into the filename.
#:
#: The lookbehind is what keeps a SIBLING project's doc out of scope:
#: `packages/dv-flow-libpss/docs/file-order-probes.md` is a perfectly good
#: citation and not a path in this tree, so matching its `docs/...` tail would
#: demand a file that has no business existing here.
_REF = re.compile(r"(?<![\w/])docs/[\w./-]*[\w-]+\.(?:md|rst)")

#: Roots that are searched for references.
_SCAN = ("docs", "src", "tests", "scripts")

#: Not ours: vendored trees, build output, the dependency checkouts.
_SKIP_DIRS = {"__pycache__", "_build", "build", "packages", ".git", "golden"}


def _candidates():
    for top in _SCAN:
        base = _ROOT / top
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in _SUFFIXES:
                continue
            if _SKIP_DIRS & set(p.relative_to(_ROOT).parts):
                continue
            # Archived notes may cite their own era -- see the module docstring.
            if p.is_relative_to(_ROOT / "docs" / "design"):
                continue
            yield p


_FILES = sorted(_candidates())
assert _FILES, "found no files to scan; the roots or suffixes are wrong"


@pytest.mark.parametrize(
    "path", _FILES, ids=[str(p.relative_to(_ROOT)) for p in _FILES])
def test_every_doc_reference_resolves(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = sorted({r for r in _REF.findall(text)
                      if not (_ROOT / r).is_file()})
    assert not missing, (
        "%s names %d doc path(s) that do not exist: %s.\n"
        "Either restore the file (docs/design/ is where the archived design "
        "notes and impl plans live), point at where it actually went, or drop "
        "the citation -- but do not leave a reader chasing it."
        % (path.relative_to(_ROOT), len(missing), ", ".join(missing)))


def test_the_archived_notes_say_they_are_archived():
    """Every file under ``docs/design/`` carries the banner, or is a live doc
    that belongs in ``docs/`` instead.

    The banner is the whole reason the directory is exempt above. A note added
    there without one is exempt from the reference check while reading like a
    current document, which is the worst of both.
    """
    design = _ROOT / "docs" / "design"
    if not design.is_dir():
        pytest.skip("no docs/design/")
    unmarked = [p.name for p in sorted(design.glob("*.md"))
                if "Archived working note" not in p.read_text(encoding="utf-8")]
    # The two notes written in place, as current documents, are not archives.
    allowed = {"dynamic-multi-actor-executors.md",
               "generic-constraints-system-tests.md"}
    assert set(unmarked) <= allowed, (
        "docs/design/%s has no archived-note banner. Add one, or move the file "
        "to docs/ if it is a current guide -- and add it to the allow-list here "
        "only if it is a design note being written NOW."
        % ", ".join(sorted(set(unmarked) - allowed)))
