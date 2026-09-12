"""``cpp-progseq`` target: generate a reusable C++ programming API from a PSS
component tree.

Emits a single header: register value unions (shared with the C backend),
``pssc::reg<T,ACC>`` register-group classes, the pure-virtual export/import APIs,
and the component class + factory. The user subclasses ``pssc::mem_if`` (virtual
dispatch) -- no redirect trick.

Design: docs/design/pss-c-cpp-progseq-gen-design.md (§4)
Plan:   docs/design/pss-c-cpp-progseq-gen-impl-plan.md
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .c_progseq_tgt import CProgSeqTarget
from .op_model import OpModelTarget


class CppProgSeqTarget(OpModelTarget):
    name = "op-model-cpp"
    description = "C++ operation-model API generated from a component tree"
    language = "C++"

    # Same reasoning as op-model-c: plain functions, no coroutine runtime, no
    # solver in the image.
    target_cfg = {
        "HAVE_EVENT_WAIT": False,
        "HAVE_RUNTIME_SOLVER": False,
    }

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        # `--root`, `--ctor-name` and `--no-core-copy` come from
        # `OpModelTarget`; only the C++-specific options are here.
        super().add_args(parser)
        parser.add_argument(
            "--namespace", dest="cpp_namespace", metavar="NAME",
            help="cpp-progseq: namespace + class prefix (default: root sans _c)",
        )
        parser.add_argument(
            "--dispatch", dest="cpp_dispatch",
            choices=("virtual", "template"), default="virtual",
            help="cpp-progseq: dispatch model (default: virtual)",
        )
        parser.add_argument(
            "--no-single-header", dest="cpp_single_header", action="store_false",
            default=True,
            help="cpp-progseq: (reserved) split declarations and bodies",
        )

    # -- runtime source -----------------------------------------------------

    core_lang = "cpp"

    def core_file_names(self, model, opts) -> List[str]:
        from .cpp.cpp_progseq_gen import core_header_names
        return core_header_names(
            model,
            match_default=getattr(opts, "c_match_default", "message"),
            message_style=getattr(opts, "c_message_style", "import"))

    def emit(self, model, opts: argparse.Namespace) -> List[Path]:
        root_name = getattr(opts, "progseq_root", None)
        ns = getattr(opts, "cpp_namespace", None) or CProgSeqTarget.default_prefix(
            root_name.split("::")[-1])

        from .cpp.cpp_progseq_gen import generate
        # `--yield`, `--match-default` and `--message-style` are declared by
        # the C target on the shared `compile` parser and mean exactly the
        # same thing here, so they are consumed rather than redeclared -- the
        # dedup proxy would drop a second declaration silently anyway.
        # Core headers LAST, as for C: they are included by name, so the
        # generated header stays first in the list a build system reads.
        return generate(model, ns,
                        dispatch=getattr(opts, "cpp_dispatch", "virtual"),
                        yield_mode=getattr(opts, "c_yield", "none"),
                        match_default=getattr(opts, "c_match_default",
                                              "message"),
                        message_style=getattr(opts, "c_message_style",
                                              "import"),
                        class_map=getattr(opts, "c_prefix_map", None)
                        ) + self.install_core(model, opts)
