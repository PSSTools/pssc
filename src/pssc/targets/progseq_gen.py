"""Emission entry point for the ``sv-progseq`` target.

Assembles the generated SV package for the root component's subtree and writes
it to the output directory (copying the core ``pssc_reg_pkg.sv`` alongside).

The package itself is assembled by `sv/backend.py` (`SvOpModelBackend`).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from . import op_model as om
from .progseq_model import CompKind

_log = logging.getLogger("pssc.progseq")


#: The core SV package the generated package imports. `ProgSeqTarget` copies it
#: (via `OpModelTarget.install_core`); this module only names it.
SV_CORE_PKG = "pssc_reg_pkg.sv"


def generate(model, pkg_name: str, reg_fields: str = "named") -> List[Path]:
    """Emit the SV programming-sequence package for an elaborated ``model``.

    ``reg_fields`` controls how a folded masked write is SPELLED, not what it
    does. ``named`` restores the field name the mask came from
    (``csr.write_field(WB_DMA_CH_CSR_ars, ..)``); ``folded`` emits the literal
    (mask, value) pair the reduction produced, which is what every other backend
    consumes and what this target emitted before the naming existed. The two
    generate identical bus traffic -- ``folded`` exists so the collapsed form
    stays reachable for diffing against the C target and for debugging a
    suspected naming bug.

    The package is assembled by `SvOpModelBackend`; this is the functional
    entry point kept for callers that predate it.

    Returns the list of written file paths.
    """
    from .sv.backend import SvOpModelBackend
    tree = model.tree
    _log.info("progseq: root=%s package=%s components: %d regular, %d reg-group",
              tree.name, pkg_name, om.count(tree, CompKind.REGULAR),
              om.count(tree, CompKind.REG_GROUP))
    written = SvOpModelBackend(pkg_name, reg_fields).generate(model)
    _log.info("progseq: wrote %s", written[0].name)
    return written
