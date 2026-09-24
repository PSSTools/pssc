"""Locate pss-corpus and its checker (COMPLIANCE-DESIGN.md §11.4).

Search order: ``$PSS_CORPUS``, then ``packages/pss-corpus``, then a sibling
checkout. **A missing corpus fails the suite; it is never skipped** -- a gate
that skips when its input is absent reports success in exactly the case it
exists to catch.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))


def _find_corpus():
    env = os.environ.get("PSS_CORPUS")
    cands = [env] if env else []
    cands += [os.path.join(_REPO, "packages", "pss-corpus"),
              os.path.join(os.path.dirname(_REPO), "pss-corpus")]
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "compliance")):
            return c
    raise RuntimeError(
        "pss-corpus not found (looked at $PSS_CORPUS, packages/pss-corpus and a "
        "sibling checkout); the compliance suite cannot run without it")


CORPUS = _find_corpus()
_CHECKER = os.path.join(CORPUS, "checker", "src")
if _CHECKER not in sys.path:
    sys.path.insert(0, _CHECKER)
os.environ.setdefault("PSS_CORPUS", CORPUS)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
