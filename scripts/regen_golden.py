#!/usr/bin/env python3
"""Rewrite the operation-model golden snapshots (tests/progseq/golden/).

The snapshots are what makes "byte-identical" mean something in the phases of
docs/design/generator-style-extensions-plan.md that claim to preserve output. That
guarantee survives exactly as long as regenerating stays a deliberate act, so
this script refuses to run unless ``PSSC_GOLDEN_REGEN=1`` is set.

    PSSC_GOLDEN_REGEN=1 python scripts/regen_golden.py            # all configs
    PSSC_GOLDEN_REGEN=1 python scripts/regen_golden.py c-vtable   # one config

**When regenerating is legitimate:** only alongside an intentional change to
generated output, in the same commit as that change, with the snapshot diff
reviewed as part of it. Never to make a failing test pass during a refactor
that was supposed to preserve output -- that is the one failure mode this whole
mechanism exists to catch, and silencing it poisons every later snapshot.

The summary printed at the end is per-config added/removed line counts, so the
regeneration itself is reviewable at a glance before the diff is read.
"""
from __future__ import annotations

import argparse
import difflib
import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from tests.progseq import golden_util as gu  # noqa: E402


def _snapshot_text(cfg_dir: Path) -> dict:
    """Every stored file of a config as {relpath: text}, for the diff stat."""
    out = {}
    if not cfg_dir.exists():
        return out
    for p in sorted(cfg_dir.rglob("*")):
        if p.is_file():
            out[p.relative_to(cfg_dir).as_posix()] = p.read_text()
    return out


def _diff_stat(before: dict, after: dict) -> str:
    added = removed = 0
    for rel in sorted(set(before) | set(after)):
        d = difflib.unified_diff(before.get(rel, "").splitlines(),
                                 after.get(rel, "").splitlines(), n=0)
        for ln in d:
            if ln.startswith("+") and not ln.startswith("+++"):
                added += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                removed += 1
    files = sorted(set(before) ^ set(after))
    note = f", {len(files)} file(s) added/removed" if files else ""
    if not added and not removed and not files:
        return "unchanged"
    return f"+{added} -{removed}{note}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("configs", nargs="*", metavar="CONFIG",
                    help="config name(s) to regenerate; default: all")
    ap.add_argument("--list", action="store_true",
                    help="print the known configs and what each covers")
    args = ap.parse_args(argv)

    if args.list:
        for cfg in gu.CONFIGS:
            print(f"{cfg.name:<14} {cfg.target:<13} {cfg.rationale}")
            print(f"{'':<14} {' '.join(cfg.argv) or '(defaults)'}")
        return 0

    if not gu.regen_allowed():
        print("refusing to regenerate: set PSSC_GOLDEN_REGEN=1 to confirm.\n"
              "Regenerate only alongside an intentional output change -- see "
              "this script's docstring.", file=sys.stderr)
        return 2

    names = args.configs or [c.name for c in gu.CONFIGS]
    unknown = [n for n in names if n not in gu.CONFIG_BY_NAME]
    if unknown:
        print(f"unknown config(s): {', '.join(unknown)}\n"
              f"known: {', '.join(c.name for c in gu.CONFIGS)}", file=sys.stderr)
        return 2

    gu.GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
    stats = []
    for name in names:
        cfg = gu.CONFIG_BY_NAME[name]
        before = _snapshot_text(gu.GOLDEN_ROOT / name)
        tmp = Path(tempfile.mkdtemp(prefix=f"golden-{name}-"))
        try:
            paths = gu.run_config(cfg, tmp)
            gu.write_golden(cfg, paths, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        after = _snapshot_text(gu.GOLDEN_ROOT / name)
        stats.append((name, _diff_stat(before, after)))
        print(f"  {name}: {len(paths)} file(s)")

    print("\nsummary:")
    for name, stat in stats:
        print(f"  {name:<14} {stat}")
    print("\nReview the snapshot diff as part of the change that caused it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
