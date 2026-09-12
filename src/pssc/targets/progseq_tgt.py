"""``sv-progseq`` target: generate a reusable SystemVerilog programming API from
a PSS component tree.

Transforms the subtree rooted at ``--root`` into an SV package: register value
structs + register-model classes, per-component export-API interface classes, an
import-API interface (extends the core ``pss_mem_if``), implementation
classes, a parameterized import adapter, and a factory.

Design: docs/design/pss-programming-seq-gen-design.md
Plan:   docs/design/pss-programming-seq-gen-impl-plan.md
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .op_model import OpModelTarget


class ProgSeqTarget(OpModelTarget):
    name = "op-model-sv"
    description = "SystemVerilog operation-model API generated from a component tree"
    language = "SystemVerilog"

    # A generated SV operation-model API exposes its operations as `task`s, so
    # a caller CAN suspend until another process posts an event -- that is what
    # a `channel_c` get lowers to; and SystemVerilog carries a constraint solver
    # the caller randomizes through. Both true.
    target_cfg = {
        "HAVE_EVENT_WAIT": True,
        "HAVE_RUNTIME_SOLVER": True,
    }

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        # `--root`, `--ctor-name` and `--no-core-copy` come from
        # `OpModelTarget`: they belong to the FAMILY, not to this backend, and
        # restating them per target is how they drifted apart before.
        super().add_args(parser)
        parser.add_argument(
            "--package", dest="progseq_package", metavar="NAME",
            help="sv-progseq: generated package name (default: <root>_pkg)",
        )
        # Spelling only -- see progseq_gen.generate(). Both settings produce
        # the same bus traffic; `folded` is the collapsed (mask, value) form the
        # C target consumes, kept reachable for diffing the two backends.
        parser.add_argument(
            "--sv-reg-fields", dest="progseq_reg_fields",
            choices=("named", "folded"), default="named",
            help="sv-progseq: spell a folded masked write as "
                 "write_field(<FIELD_CONST>, v) ('named', default) or as "
                 "write_val_masked(<mask>, <val>) ('folded')",
        )

    # -- entry point --------------------------------------------------------

    core_lang = "sv"

    def core_file_names(self, model, opts) -> List[str]:
        from .progseq_gen import SV_CORE_PKG
        return [SV_CORE_PKG]

    def emit(self, model, opts: argparse.Namespace) -> List[Path]:
        root_name = getattr(opts, "progseq_root", None)
        pkg_name = getattr(opts, "progseq_package", None) or f"{root_name}_pkg"
        from .progseq_gen import generate
        # COMPILATION ORDER, not creation order. The returned list is what a
        # build system hands the compiler (dv-flow's `classify_outputs`
        # preserves it), and the generated package uses `addr_handle_t` and
        # `pss_mem_if` from the core package -- so the core comes first.
        return (self.install_core(model, opts)
                + generate(model, pkg_name,
                           reg_fields=getattr(opts, "progseq_reg_fields",
                                              "named")))
