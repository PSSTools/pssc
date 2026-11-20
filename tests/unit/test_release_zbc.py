"""T-G1: release bytecode is unchanged by the debug work.

Every compliance and construct-matrix model, lowered in release, must give the
same ``.zbc`` bytes as ``data/release_zbc_sha256.json`` (see ``release_zbc``).
The snapshot was taken with zuspec-be-bc and zuspec-ir-core at their HEAD; the
only difference from those images is the ENTRY section (kind 16) every image
now carries, the COMP section (kind 21) of each model with components, and the
MSG section (kind 22) of each model with messages or strings, and the IMPORT
section (kind 26) of each model with imports, and the ACT section (kind 27) of
each model with activations (P4.10a). Regenerate only
for an intentional change to release bytecode::

    PYTHONPATH=./src python tests/unit/release_zbc.py --write
"""
import json

import pytest

from . import release_zbc


@pytest.mark.slow
@pytest.mark.golden
def test_release_zbc_unchanged():
    with open(release_zbc.SNAPSHOT) as fp:
        want = json.load(fp)
    got = release_zbc.collect()
    assert sorted(got) == sorted(want), "models added or removed: regenerate the snapshot"
    moved = sorted(k for k in want if got[k] != want[k])
    assert not moved, f"release .zbc changed for {len(moved)} models: {moved[:10]}"
