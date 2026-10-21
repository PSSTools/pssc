#!/usr/bin/env python3
"""Rewrite docs/override-surface.json from the `@overridable` marks.

The manifest is checked in so that growing pssc's published override surface is
a diff in TWO files -- the mark and the manifest -- rather than a thing that
happens when somebody adds a decorator. `tests/unit/test_override_surface.py`
fails when the two disagree.

Running this is legitimate when the marks changed ON PURPOSE, and the diff it
produces belongs in the same commit and the same review as that change. The
admission rule for a NEW entry is in docs/extension-stability.md: a method
becomes overridable because a real extension needed it, not because it looked
useful.

    python scripts/regen_override_surface.py            # rewrite
    python scripts/regen_override_surface.py --check    # exit 1 if stale
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from pssc.targets.overridable import manifest_for  # noqa: E402

#: The classes whose surface is published, by dotted name. A class not listed
#: here has no published surface, however many marks it carries.
SURFACES = ["pssc.targets.c.backend.COpModelBackend",
            "pssc.targets.c.lower_progseq.CBodyEmitter"]

MANIFEST = _ROOT / "docs" / "override-surface.json"

COMMENT = (
    "The published override surface. Generated from the @overridable marks "
    "and CHECKED IN: growing the surface is then a diff in two files, which "
    "is a decision somebody makes rather than a thing that happens. "
    "tests/unit/test_override_surface.py fails when this file and the code "
    "disagree. Regenerate with scripts/regen_override_surface.py."
)


def _resolve(dotted: str):
    import importlib
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


def build() -> dict:
    return {
        "version": 1,
        "comment": COMMENT,
        "surfaces": {name: manifest_for(_resolve(name)) for name in SURFACES},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if the manifest is stale")
    args = ap.parse_args(argv)

    text = json.dumps(build(), indent=2) + "\n"
    if args.check:
        current = MANIFEST.read_text() if MANIFEST.is_file() else ""
        if current != text:
            print(f"{MANIFEST} is stale; run scripts/regen_override_surface.py",
                  file=sys.stderr)
            return 1
        print(f"{MANIFEST} is up to date")
        return 0

    MANIFEST.write_text(text)
    n = sum(len(v) for v in build()["surfaces"].values())
    print(f"wrote {MANIFEST} ({n} marked member(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
