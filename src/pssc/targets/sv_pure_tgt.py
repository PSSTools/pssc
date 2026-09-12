"""Pure-SystemVerilog target (``sv-pure``).

This is the incremental-traversal pure-SV lowering path (see
``docs/design/pure-sv-incremental-design.md`` and
``docs/design/pure-sv-implementation-plan.md``). It lowers a subset of PSS to SV that is
solved entirely by the native SV constraint solver -- no DPI, no external
``dv-solve`` on the default path.

M0 status: this is a **stub** that delegates to the existing ``sv-native``
lowering so the target id exists and is wired through the CLI/driver/dvflow. The
incremental pipeline is filled in across milestones M1-M8 (plan task ``F1``).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .sv_tgt import SvTarget


class SvPureTarget(SvTarget):
    name = "sv-pure"
    description = (
        "Pure SystemVerilog via incremental-traversal lowering, solved by the "
        "native SV solver (no DPI; M0 stub delegates to sv-native)"
    )

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        super().add_args(parser)
        parser.add_argument(
            "--allow-dpi",
            dest="sv_pure_allow_dpi",
            action="store_true",
            default=False,
            help="sv-pure target: permit the DPI/dv-solve fallback for "
            "over-capacity problems instead of refusing them (default: off)",
        )

    def run(self, ctx, opts: argparse.Namespace) -> List[Path]:
        # Use the structured pure-SV path for the supported subset (atomic
        # actions); fall back to sv-native for everything else (additive until
        # later milestones extend the subset). See docs/pure-sv-*-plan.md.
        from .sv.lower_pure import generate_pure_sv, is_pure_supported

        export_actions = getattr(opts, "export_actions", None)
        if is_pure_supported(ctx, export_actions):
            return generate_pure_sv(
                ctx,
                str(getattr(opts, "output_dir", ".") or "."),
                export_actions=export_actions,
                package_name=getattr(opts, "sv_package_name", "zsp_gen_pkg"),
            )
        return super().run(ctx, opts)
