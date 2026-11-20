"""pssc's `std_libs/pssunit_pkg.pss` against the normative copy in the pssunit repo.

The pssunit repo owns the package (`src/pss/pssunit_pkg/*.pss`, one type per
file); pssc ships a flat copy so `pssc test` needs nothing installed. During
Phase 1 the copy is a declared superset: every declaration the normative
package makes inside `package pssunit_pkg` must appear in pssc's copy with the
same text once comments and whitespace are dropped, and pssc may add more
(`rt_c`, the checks, `import std_pkg::*`) ahead of the normative copy. pssc may
also add members to a declaration (a new attribute of `annotation test`): a
declaration with the same header covers the normative one if it has every
member the normative one has.

Skipped where the pssunit repo is not checked out; set PSSUNIT_ROOT to point
at it.
"""

import os
import re
from pathlib import Path

import pytest

OURS = (Path(__file__).resolve().parents[2]
        / "src" / "pssc" / "std_libs" / "pssunit_pkg.pss")
ROOT = Path(os.environ.get("PSSUNIT_ROOT",
                           Path.home() / "projects" / "psstools" / "pssunit"))
NORMATIVE = ROOT / "src" / "pss" / "pssunit_pkg"


def _tokens(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.findall(r'"(?:\\.|[^"\\])*"|\w+|[^\s\w]', text)


def _package_decls(text, pkg="pssunit_pkg"):
    """Each top-level declaration inside every `package <pkg> { ... }`, as a
    normalized token string."""
    toks = _tokens(text)
    decls = []
    i = 0
    while i < len(toks):
        if toks[i] == "package" and toks[i + 1:i + 3] == [pkg, "{"]:
            i += 3
            depth, start = 0, i
            while depth >= 0:
                t = toks[i]
                if t == "{":
                    depth += 1
                elif t == "}":
                    depth -= 1
                    if depth == 0:
                        decls.append(" ".join(toks[start:i + 1]))
                        start = i + 1
                elif t == ";" and depth == 0:
                    decls.append(" ".join(toks[start:i + 1]))
                    start = i + 1
                i += 1
        else:
            i += 1
    return decls


def test_the_extractor_reads_a_package_body():
    src = """
    package pssunit_pkg {
        /** doc */ abstract action test_a { // x
        }
        import std_pkg::*;
    }
    component outside_c { }
    """
    assert _package_decls(src) == ["abstract action test_a { }",
                                   "import std_pkg : : * ;"]


def test_pssc_copy_declares_the_framework_types():
    decls = _package_decls(OURS.read_text())
    assert "abstract action test_a { }" in decls
    assert any(d.startswith("annotation test {") for d in decls)


def _split(decl):
    """A declaration's header and its top-level members, or None if it has
    no body."""
    if "{" not in decl or not decl.endswith("}"):
        return None
    head, body = decl.split("{", 1)
    toks = body[:-1].split()
    members, cur, depth = set(), [], 0
    for t in toks:
        cur.append(t)
        depth += (t == "{") - (t == "}")
        if depth == 0 and t in (";", "}"):
            members.add(" ".join(cur))
            cur = []
    return head.strip(), members


def _covered(norm, ours):
    if norm in ours:
        return True
    n = _split(norm)
    return n is not None and any(
        o is not None and o[0] == n[0] and n[1] <= o[1]
        for o in map(_split, ours))


def test_an_added_member_still_covers():
    norm = 'annotation test { int samples = 1 ; }'
    assert _covered(norm, {'annotation test { int samples = 1 ; string tags = "" ; }'})
    assert not _covered(norm, {'annotation test { int samples = 2 ; }'})
    assert not _covered(norm, {'annotation other { int samples = 1 ; }'})


@pytest.mark.skipif(not NORMATIVE.is_dir(),
                    reason="pssunit repo not present (set PSSUNIT_ROOT)")
def test_pssc_copy_is_a_superset_of_the_normative_package():
    ours = set(_package_decls(OURS.read_text()))
    normative = [d for f in sorted(NORMATIVE.glob("*.pss"))
                 for d in _package_decls(f.read_text())]
    assert normative, f"no pssunit_pkg declarations under {NORMATIVE}"
    missing = [d for d in normative if not _covered(d, ours)]
    assert not missing, ("in the normative pssunit_pkg but not in "
                         f"{OURS.name}: {missing}")
