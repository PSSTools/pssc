"""``sv-progseq`` target: generate a reusable SystemVerilog programming API from
a PSS component tree.

Transforms the subtree rooted at ``--root`` into an SV package: register value
structs + register-model classes, the import API (extends the core
``pss_mem_if``), the context API the platform calls (the root's exported
functions and the exported actions), one class per component type -- derived
from its base's -- and a factory. See docs/design/sv-op-model-inheritance.md.

Design: docs/design/pss-programming-seq-gen-design.md
Plan:   docs/design/pss-programming-seq-gen-impl-plan.md
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

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

    # `exec init_down`/`init_up` are the component classes' construction hooks,
    # run by `pss_do_init()` after the root's constructor (design D3, D4).
    supports_init_blocks = True

    # An exported action (`--export-action`) is a task of the component it
    # runs in, called by the factory's method of the action's name.
    supports_entries = True

    # One class per component type, derived from its base's: `extends`,
    # `super.f(...)`, and a field declared again hides its base's (LRM 17.1).
    native_inheritance = True

    # Package-scope functions are package tasks and functions: `target` and
    # unqualified ones tasks, `solve` ones SV functions (`sv.lower_progseq
    # .is_task`).
    supports_package_functions = True

    @staticmethod
    def assert_api_is_not_empty(model) -> None:
        """The context API is the exported functions and the exported
        actions, and nothing else (design D11): a model with neither has no
        way in."""
        if not (model.exports or model.entries):
            raise ValueError(
                f"op-model-sv: '{model.root.name}' exports nothing, so the "
                f"generated model would have no API. Export the root's "
                f"operations (`export target function f;` in its body) or "
                f"an action (--export-action)")

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

    #: The backend class that assembles the package. Resolved late so importing
    #: this module does not drag the whole lowering in.
    backend_cls = None

    @classmethod
    def backend_class(cls):
        from .sv.backend import SvOpModelBackend
        return cls.backend_cls or SvOpModelBackend

    def backend_for(self, opts: argparse.Namespace):
        """The backend instance this run generates through."""
        root_name = getattr(opts, "progseq_root", None)
        pkg_name = (getattr(opts, "progseq_package", None)
                    or (f"{root_name}_pkg" if root_name else ""))
        return self.backend_class()(
            pkg_name, getattr(opts, "progseq_reg_fields", "named"))

    def sections(self, model, opts: argparse.Namespace) -> Dict[str, str]:
        return self.backend_for(opts).sections(model)

    def emit(self, model, opts: argparse.Namespace) -> List[Path]:
        # COMPILATION ORDER, not creation order. The returned list is what a
        # build system hands the compiler (dv-flow's `classify_outputs`
        # preserves it), and the generated package uses `addr_handle_t` and
        # `pss_mem_if` from the core package -- so the core comes first.
        return (self.install_core(model, opts)
                + self.backend_for(opts).generate(model))
