"""``c-progseq`` target: generate a reusable C programming API from a PSS
component tree.

Transforms the subtree rooted at ``--root`` into a C header (+ optional ``.c``):
register value unions, baked inline register accessors, per-component export
functions, and a component struct + factory. The memory-access seam is selected
by ``--link-style`` (vtable / direct / mmio); the register accessors and every
operation body are emitted identically across the three styles.

Design: docs/design/pss-c-cpp-progseq-gen-design.md (§3)
Plan:   docs/design/pss-c-cpp-progseq-gen-impl-plan.md
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

from .op_model import OpModelTarget


class CProgSeqTarget(OpModelTarget):
    name = "op-model-c"
    description = "C operation-model API generated from a component tree"
    language = "C"

    # This backend emits plain C functions. It generates no coroutine runtime
    # and no scheduler -- that is what the `c-host` / `c-embedded` targets are
    # for -- so there is no way for a caller here to suspend until another
    # party posts an event, and there is no solver in the image. Both false.
    #
    # HAVE_EVENT_WAIT=false does NOT mean this target cannot wait. It means it
    # cannot wait on an EVENT. A polling wait needs no runtime support at all
    # and is available here: a model gets its whole operation surface, with
    # `yield` (which lowers to nothing by default) as the wait primitive in
    # place of a channel get. See target_cfg.py's module docstring for why the
    # v1 flag conflated these.
    #
    # Override with `--target-cfg HAVE_EVENT_WAIT=true` if a project supplies
    # its own scheduler and event plumbing under the generated API.
    target_cfg = {
        "HAVE_EVENT_WAIT": False,
        "HAVE_RUNTIME_SOLVER": False,
    }

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        # `--root`, `--ctor-name` and `--no-core-copy` come from
        # `OpModelTarget`; only the C-specific options are here.
        super().add_args(parser)
        parser.add_argument(
            "--prefix", dest="c_prefix", metavar="NAME",
            help="c-progseq: symbol/file prefix (default: root name sans _c)",
        )
        parser.add_argument(
            "--prefix-map", dest="c_prefix_map", metavar="TYPE=PREFIX",
            action="append", default=[],
            help="c-progseq: override one component type's symbol prefix. The "
                 "escape hatch for two component types that collide after "
                 "'_c'-stripping, which is otherwise a generation error",
        )
        parser.add_argument(
            "--link-style", dest="c_link_style",
            choices=("vtable", "direct", "mmio"), default="vtable",
            help="c-progseq: memory-access seam (default: vtable)",
        )
        parser.add_argument(
            "--mem-access", dest="c_mem_access",
            choices=("pointer", "functions", "selectable"), default=None,
            help="op-model-c: the memory-access MECHANISM, superseding the "
                 "direct/mmio spelling of --link-style. 'pointer' dereferences "
                 "the address; 'functions' calls extern symbols; 'selectable' "
                 "defers the choice to the C compile line "
                 "(-DPSSC_MEM_ACCESS_FUNCTIONS). Omitted: --link-style decides, "
                 "which is what every existing build gets",
        )
        parser.add_argument(
            "--reg-style", dest="c_reg_style",
            choices=("bitfields", "accessors"), default="bitfields",
            help="c-progseq: register value layout (default: bitfields, LE)",
        )
        parser.add_argument(
            "--lifecycle", dest="c_lifecycle",
            choices=("malloc", "static"), default="malloc",
            help="op-model-c: how the root object is obtained. 'malloc' "
                 "(default) also emits <prefix>_create()/_destroy(); 'static' "
                 "emits only <prefix>_init() and links no allocator, for a "
                 "target with no heap",
        )
        parser.add_argument(
            "--emit-stubs", dest="c_emit_stubs", action="store_true",
            default=False,
            help="op-model-c: also write <prefix>_stubs.c with a WEAK empty "
                 "pssc_message(), so a link succeeds before the platform side "
                 "exists. No memory primitive and no model-declared import is "
                 "ever stubbed -- see the file's own banner for why",
        )
        parser.add_argument(
            "--addr-bits", dest="c_addr_bits", type=int, choices=(32, 64),
            default=64,
            help="op-model-c: width of pssc_addr_t (default 64, matching PSS "
                 "addr_handle_t). 32 saves 4 bytes per address held in a "
                 "component and is required by nothing -- narrow it only when "
                 "the device map fits, which this generator cannot check",
        )
        parser.add_argument(
            "--include", dest="c_include", metavar="HEADER",
            action="append", default=[],
            help="op-model-c: extra #include in the generated header, placed "
                 "BEFORE the seam includes so it can define PSSC_MEM_BARRIER, "
                 "PSSC_CHAN_ENTER/EXIT or PSSC_UNREACHABLE. Repeatable; order "
                 "preserved. Bare names are quoted, <angled> is passed through",
        )
        parser.add_argument(
            "--include-impl", dest="c_include_impl", metavar="HEADER",
            action="append", default=[],
            help="op-model-c: extra #include in the generated .c only, for "
                 "something the bodies need but no caller should see. Ignored "
                 "under --header-only, where there is no separate .c",
        )
        parser.add_argument(
            "--omit-stdint", dest="c_omit_stdint", action="store_true",
            default=False,
            help="op-model-c: do not emit #include <stdint.h>/<stdbool.h>. For "
                 "a platform that supplies the fixed-width types itself; pair "
                 "with --include so something still declares uint32_t",
        )
        parser.add_argument(
            "--header-only", dest="c_header_only", action="store_true",
            default=False,
            help="c-progseq: emit a single self-contained .h -- every body, "
                 "register layout and accessor `static inline`, and no .c. "
                 "The default emits both, with the implementation in the .c",
        )
        parser.add_argument(
            "--yield", dest="c_yield", choices=("none", "import"),
            default="none",
            help="op-model-c: how `yield` lowers. 'none' (default) emits "
                 "nothing -- a bare-metal target has no scheduler to yield to, "
                 "so the surrounding loop becomes a tight poll. 'import' calls "
                 "yield_(), for a platform that wants to charge for a spin "
                 "(WFI, watchdog kick, delay)",
        )
        parser.add_argument(
            "--match-default", dest="c_match_default",
            choices=("none", "message", "unreachable"), default="message",
            help="op-model-c: what an unmatched `match` subject does when the "
                 "model states no default. PSS makes it an error (3.1 "
                 "§22.7.9) and a C `switch` with no default silently does "
                 "nothing, so 'none' is available but is not the default",
        )
        parser.add_argument(
            "--message-style", dest="c_message_style",
            choices=("import", "none"), default="import",
            help="op-model-c: 'import' calls pssc_message(); 'none' drops the "
                 "call AND its format string (.rodata is a real budget on "
                 "this class of part)",
        )

        parser.add_argument(
            # No argparse default, so that "not given" is distinguishable from
            # "--style default" -- which is what lets a derived backend's
            # `style_cls` supply the default policy while an explicit --style
            # still wins. Resolves to `default` for every other target, which
            # is what every existing command line already got.
            "--style", dest="c_style", metavar="NAME", default=None,
            help="op-model-c: the naming/layout policy to generate under, "
                 "from the pssc.styles entry-point group. 'default' is pssc's "
                 "own conventions; a house style is one class and one entry "
                 "point, and tracks upstream C semantics automatically",
        )

    # -- helpers ------------------------------------------------------------

    def style_for(self, opts):
        """Resolve `--style` to a policy instance.

        An unknown name is fatal rather than a warning-and-default: generating
        a whole API under pssc's conventions when a house style was asked for
        is the failure this flag exists to prevent, and it is one a reviewer
        cannot see in a diff of the generated files alone.

        With no `--style`, the BACKEND's `style_cls` decides. That is how a
        derived target ships a house policy without also having to publish it
        as an entry point -- and an explicit `--style` still wins over it,
        because the flag is the user's answer and a backend subclass must not
        overrule it.
        """
        name = getattr(opts, "c_style", None)
        if name:
            return self.resolve_style(name)
        return self.backend_class().style_cls()

    @staticmethod
    def _check_style_link_style(style, link_style: str) -> None:
        """Refuse a bus-overriding style under `--link-style vtable`.

        A style that supplies its own seam headers -- `seam_headers()` returning
        an empty sequence -- is choosing the memory MECHANISM. `vtable` is also
        a mechanism: the handle carries a struct of function pointers and every
        access goes through it. Combining them generates a struct member and a
        `_create(bus, ...)` parameter that the bodies never read, so a caller
        hands over a vtable that is silently never called -- which looks exactly
        like a working integration until the first access goes somewhere else.

        Rejected at start-up rather than at the first access, and in the shape
        of the existing `--mem-access` + `vtable` refusal, because both are the
        same class of mistake: two answers to one question.
        """
        if link_style == "vtable" and style.overrides_bus(link_style):
            raise ValueError(
                f"--style {style.name} supplies its own memory mechanism "
                f"(seam_headers() is empty), which --link-style vtable also "
                f"does: vtable reaches the bus through a per-instance struct of "
                f"function pointers, and a style that renders its own accesses "
                f"never calls it. Drop one -- --link-style direct or mmio pairs "
                f"with a bus-overriding style")

    @staticmethod
    def default_prefix(root_name: str) -> str:
        """Default symbol prefix: the root name with a trailing ``_c`` stripped."""
        return root_name[:-2] if root_name.endswith("_c") else root_name

    # -- runtime source -----------------------------------------------------

    core_lang = "c"

    def core_file_names(self, model, opts) -> List[str]:
        """The seam headers to copy -- the style's answer where it gives one.

        A policy that renders its own memory access has chosen the MECHANISM,
        not just the spelling, and copying pssc's seam headers beside it would
        ship a second mechanism that nothing calls: a directory whose contents
        contradict its generated code.
        """
        from .c.c_progseq_gen import seam_headers
        link_style = getattr(opts, "c_link_style", "vtable")
        override = self.style_for(opts).seam_headers(link_style)
        return seam_headers(link_style) if override is None else list(override)

    # -- entry point --------------------------------------------------------

    #: The backend class that assembles the files. `None` means pssc's own
    #: (`COpModelBackend`), resolved late so importing this module does not drag
    #: the whole lowering in. THE seam a tier-B extension uses: a derived target
    #: sets this and inherits every option, every legality entry and every
    #: capability from `op-model-c` via `derives_from`.
    backend_cls = None

    def prefix_for(self, opts: argparse.Namespace) -> str:
        root_name = getattr(opts, "progseq_root", None)
        return getattr(opts, "c_prefix", None) or self.default_prefix(
            root_name.split("::")[-1])

    def flags_for(self, opts: argparse.Namespace) -> dict:
        """The command line, as `settings_for`'s keywords.

        One reading of the options, shared by `emit` and `sections`: a second
        copy would let the differential test describe a build the generator
        does not actually produce.
        """
        link_style = getattr(opts, "c_link_style", "vtable")
        return dict(
            link_style=link_style,
            reg_style=getattr(opts, "c_reg_style", "bitfields"),
            # ASKED FOR, never inferred. `--link-style mmio` used to force this
            # -- with no bus handle to carry there is nothing a body needs from
            # a translation unit, so everything COULD be `static inline` -- but
            # "could" was the whole argument, and it cost every mmio build the
            # .c that its register layouts and accessors belong in. A caller
            # that wants one file says so.
            header_only=getattr(opts, "c_header_only", False),
            yield_mode=getattr(opts, "c_yield", "none"),
            match_default=getattr(opts, "c_match_default", "message"),
            message_style=getattr(opts, "c_message_style", "import"),
            prefix_map=getattr(opts, "c_prefix_map", None),
            lifecycle=getattr(opts, "c_lifecycle", "malloc"),
            emit_stubs=getattr(opts, "c_emit_stubs", False),
            mem_access=getattr(opts, "c_mem_access", None),
            includes=getattr(opts, "c_include", None),
            includes_impl=getattr(opts, "c_include_impl", None),
            omit_stdint=getattr(opts, "c_omit_stdint", False),
            addr_bits=getattr(opts, "c_addr_bits", 64),
        )

    def abi_settings(self, opts: argparse.Namespace) -> dict:
        """The C options that move a symbol, an offset or a signature.

        `flags_for` exactly: every one of them changes what a caller links
        against -- the seam a `_create` takes, whether `_create` exists at all,
        how a register value is spelled. Reading the same method the generation
        reads is the point; a hand-kept second list would let the manifest
        describe a build that was not produced.
        """
        return dict(self.flags_for(opts), prefix=self.prefix_for(opts))

    def check_overrides(self) -> None:
        """Refuse a backend subclass that took one half of a paired override.

        Called by `targets.register`, so the answer arrives before any model is
        read rather than in a C compiler's complaint about a generated file.
        """
        from .overridable import check_pairs
        check_pairs(self.backend_class())

    @classmethod
    def backend_class(cls):
        """The backend class this target generates through -- pssc's own
        unless a subclass names another. The published override surface
        (`pssc targets --overrides`) is this class's."""
        from .c.backend import COpModelBackend
        return cls.backend_cls or COpModelBackend

    def backend_for(self, opts: argparse.Namespace):
        """The backend instance this run generates through."""
        style = self.style_for(opts)
        self._check_style_link_style(
            style, getattr(opts, "c_link_style", "vtable"))
        return self.backend_class()(style)

    def sections(self, model, opts: argparse.Namespace) -> Dict[str, str]:
        """`{"<file>:<section>": text}` -- what this run WOULD generate,
        attributed. Nothing is written.

        The differential test helper (`pssc.testing`) compares these between an
        extension and its baseline, so that "the header changed" becomes "the
        `impl` section changed", which is a claim an extension author can
        declare in advance and a reviewer can check.
        """
        from .c.c_progseq_gen import settings_for
        be = self.backend_for(opts)
        s = settings_for(model, self.prefix_for(opts), **self.flags_for(opts))
        be.prepare(model, s)
        out: Dict[str, str] = {}
        files = [(be.style.header_name(s.prefix), be.header_sections(model, s))]
        if not s.header_only:
            files.append((be.style.impl_name(s.prefix),
                          be.impl_sections(model, s)))
        for filename, sections in files:
            for name, emit in sections:
                out[f"{filename}:{name}"] = "\n".join(emit(model, s))
        for filename, text in be.emit_extra_files(model, s).items():
            out[f"{filename}:extra_files"] = text
        return out

    def emit(self, model, opts: argparse.Namespace) -> List[Path]:
        from .c.c_progseq_gen import settings_for
        # Core headers LAST: a C header is included by name, so unlike the SV
        # package there is no compilation order to honour, and the generated
        # header stays first in the list a build system reads.
        be = self.backend_for(opts)
        s = settings_for(model, self.prefix_for(opts), **self.flags_for(opts))
        return be.generate(model, s) + self.install_core(model, opts)
