"""``pssc`` command-line entry point.

Subcommands:
  * ``compile`` — sources -> selected target
  * ``parse``   — front-end only; optionally dump canonical IR as YAML
  * ``targets`` — list available targets

Exit codes: ``0`` success, ``1`` user error (bad target, parse/translate
failure), ``2`` internal error.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Dict, Optional

from .__version__ import version
from . import diag
from . import driver
from . import targets as _targets
from .ir import dump_ir


class OptionPolicyError(Exception):
    """A target contributed an option it is not allowed to contribute."""


class _DedupArgGroup:
    """Proxy over a parser whose ``add_argument`` applies the per-target option
    policy.

    All targets contribute their options to the single ``compile`` parser, so
    the namespace is shared and something has to arbitrate it. The answer is
    different for the two kinds of contributor, because their failure modes are:

    *Built-ins* -- first wins, silently. Target *variants* legitimately share
    inherited options (``sv-pure`` extends ``sv-native`` and calls
    ``super().add_args()``, so both declare ``--no-rt-pkg``), and the whole
    op-model family shares ``--root``. A second declaration is the same option,
    by construction: they are one codebase, and `test_cli.py` pins the
    behaviour.

    *Plugins* -- a collision is an error, and the option must be namespaced
    ``--<target-name>-...`` in the first place. The old silent skip means a
    plugin's ``--prefix`` quietly becomes the C backend's: the plugin's
    ``add_args`` returns, its ``run`` reads ``opts.c_prefix``-shaped state it
    never set, and the generated output is wrong with nothing on stderr. A
    plugin that wants an un-namespaced knob has ``-X`` (see ``Target.opt``).

    *A plugin's INHERITED options* -- built-in policy, because they are a
    built-in's options. A target with ``derives_from`` delegates to its
    ancestor's ``add_args``, so ``--root`` arriving from
    ``op-model-acme-c`` is not the plugin claiming a shared name: it is the
    ancestor's own declaration, reaching the same ``dest`` the derived target
    reads. ``inherited`` is computed by asking the ancestor what it
    contributes, so the exemption covers exactly those and not one option
    more -- a derived plugin adding a NEW ``--prefix`` is still refused.
    """

    def __init__(self, parser: argparse.ArgumentParser):
        self._parser = parser
        #: option string -> name of the target that first declared it
        self._owner: Dict[str, str] = {}
        self._target: Optional[str] = None
        self._is_plugin = False
        self._inherited: set = set()

    def for_target(self, name: str, is_plugin: bool,
                   inherited=()) -> "_DedupArgGroup":
        """Attribute subsequent ``add_argument`` calls to target ``name``."""
        self._target = name
        self._is_plugin = is_plugin
        self._inherited = set(inherited)
        return self

    # -- policy -------------------------------------------------------------

    @staticmethod
    def _namespace_ok(opt: str, target: str) -> bool:
        return opt.startswith(f"--{target}-")

    def add_argument(self, *args, **kwargs):
        opts = [a for a in args if isinstance(a, str) and a.startswith("-")]
        existing = self._parser._option_string_actions

        if self._is_plugin:
            for opt in opts:
                if opt in self._inherited:
                    continue
                if not self._namespace_ok(opt, self._target):
                    raise OptionPolicyError(
                        f"target '{self._target}' contributes option '{opt}', "
                        f"which is not namespaced; a plugin's options must "
                        f"begin '--{self._target}-'. Use -X "
                        f"{opt.lstrip('-')}=VALUE for an option you do not "
                        f"want to spell out")
                if opt in existing:
                    owner = self._owner.get(opt, "another target")
                    raise OptionPolicyError(
                        f"target '{self._target}' contributes option '{opt}', "
                        f"which target '{owner}' already declared. Two targets "
                        f"cannot share one option on the compile parser")

        if any(o in existing for o in opts):
            return None                       # built-in family: first wins
        action = self._parser.add_argument(*args, **kwargs)
        for opt in opts:
            self._owner.setdefault(opt, self._target or "(unknown)")
        return action

    def __getattr__(self, name):
        return getattr(self._parser, name)


def _inherited_options(tgt) -> set:
    """The option strings ``tgt`` gets from the target it derives from.

    Asked of the ancestor rather than guessed from a prefix, so the plugin
    option policy exempts exactly the inherited options and not one more. Empty
    for everything that does not derive, which is every built-in.
    """
    anc = getattr(tgt, "ancestor", None)
    anc = anc() if callable(anc) else None
    if anc is None:
        return set()
    probe = argparse.ArgumentParser(add_help=False)
    anc.add_args(probe)
    return {s for a in probe._actions for s in a.option_strings}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pssc", description="The PSS compiler")
    p.add_argument("--version", action="version", version=f"pssc {version}")
    sub = p.add_subparsers(dest="command")

    # --- compile -----------------------------------------------------------
    c = sub.add_parser("compile", help="compile PSS sources to a target")
    c.add_argument("sources", nargs="+", help="PSS source file(s)")
    c.add_argument(
        "-t", "--target", default="python",
        help="output target (see `pssc targets`); default: python",
    )
    c.add_argument(
        "-o", "--output-dir", dest="output_dir", default=".",
        help="directory for generated files (created if absent); default: .",
    )
    c.add_argument(
        "--dump-ir", dest="dump_ir", metavar="FILE",
        help="also write the canonical IR Context as YAML to FILE",
    )
    c.add_argument("-q", "--quiet", action="store_true",
                   help="do not print written file paths")
    # How far to lower the PSS 3.1 §21.14.1 masked register writes. The
    # field-wise forms always reduce to write_val_masked; this decides whether
    # that survives to the backend or is spelled out as the read/modify/write
    # the LRM defines it to be. Behaviour is identical either way.
    c.add_argument(
        "--reg-rmw", dest="reg_rmw", choices=("native", "expand"),
        default="native",
        help="lowering for masked register writes: 'native' emits "
        "write_val_masked; 'expand' emits read_val + write_val, for a backend "
        "with no read-modify-write primitive; default: native",
    )
    # The generated operation model is a close transcription of its PSS
    # source, so by default it carries the source's prose across. This turns
    # that off -- and is the regression gate for the feature: with it, the
    # output must match the pre-comment output byte for byte.
    c.add_argument(
        "--no-comments", dest="no_comments", action="store_true",
        help="do not carry PSS comments into the generated code",
    )
    # Shared across the SV export targets (sv-native oo_api, sv-dpi-bridge):
    # which actions to expose as entry points. Added once here so multiple
    # targets can consume it without colliding in the single compile parser.
    c.add_argument(
        "--export-action", dest="export_actions", action="append",
        metavar="NAME", default=None,
        help="action to expose as an export entry point (repeatable; "
        "default: the auto-detected single root action)",
    )
    # Override what the target publishes about itself in `target_cfg_pkg`, the
    # source unit injected ahead of the sources (see pssc/targets/target_cfg.py).
    # A target's own answer is the default and is usually right; this exists for
    # the case where the surrounding project changes it -- firmware that
    # supplies its own scheduler under a generated C API, say.
    c.add_argument(
        "--target-cfg", dest="target_cfg", action="append",
        metavar="NAME=VALUE", default=None,
        help="override a target_cfg_pkg constant, e.g. HAVE_EVENT_WAIT=false "
        "(repeatable; see `pssc targets`)",
    )
    # The escape hatch from the shared option namespace. A target reads these
    # through `Target.opt`, so a plugin can take options without claiming a
    # `--flag` that a built-in might want later -- and without every plugin on
    # the system enlarging `pssc compile --help`.
    c.add_argument(
        "-X", "--target-opt", dest="target_opts", action="append",
        metavar="NAME=VALUE", default=None,
        help="pass a target-specific option (repeatable). NAME is defined by "
        "the target; a bare NAME means NAME=true",
    )
    # per-target options. Shared options inherited across target variants (e.g.
    # the SV family) are registered once via the dedup proxy; a plugin's option
    # must be namespaced and may not collide -- see _DedupArgGroup.
    _targets.discover()
    _c_dedup = _DedupArgGroup(c)
    for name in _targets.list_targets():
        tgt = _targets.get(name)
        tgt.add_args(_c_dedup.for_target(name, not _targets.is_builtin(name),
                                         _inherited_options(tgt)))
    c.set_defaults(func=_cmd_compile)

    # --- parse -------------------------------------------------------------
    pa = sub.add_parser("parse", help="run the front end only; optionally dump IR")
    pa.add_argument("sources", nargs="+", help="PSS source file(s)")
    pa.add_argument(
        "--dump-ir", dest="dump_ir", metavar="FILE",
        help="write the canonical IR Context as YAML to FILE",
    )
    pa.add_argument("-q", "--quiet", action="store_true")
    pa.set_defaults(func=_cmd_parse)

    # --- targets -----------------------------------------------------------
    t = sub.add_parser("targets", help="list available targets")
    t.add_argument(
        "--overrides", metavar="TARGET", nargs="?", const="",
        help="print TARGET's published override surface -- the methods a "
             "mid-weight extension may subclass, and what each promises. With "
             "no TARGET, every target that publishes one",
    )
    t.set_defaults(func=_cmd_targets)

    # --- sv-core-path ------------------------------------------------------
    scp = sub.add_parser(
        "sv-core-path",
        help="print the path to the bundled SV core package (pssc_reg_pkg)",
    )
    scp.add_argument(
        "--file", action="store_true",
        help="print the path to pssc_reg_pkg.sv instead of its directory",
    )
    scp.set_defaults(func=_cmd_sv_core_path)

    # --- c-core-path / cpp-core-path --------------------------------------
    ccp = sub.add_parser(
        "c-core-path",
        help="print the directory of the bundled C core seam headers (pssc_mem*.h)",
    )
    ccp.add_argument(
        "--file", metavar="NAME", nargs="?", const=C_CORE_HEADER,
        help="print the path to a specific header (default: pssc_mem.h)",
    )
    ccp.set_defaults(func=_cmd_c_core_path)

    xcp = sub.add_parser(
        "cpp-core-path",
        help="print the directory of the bundled C++ core header (pssc_reg.hpp)",
    )
    xcp.add_argument(
        "--file", metavar="NAME", nargs="?", const=CPP_CORE_HEADER,
        help="print the path to a specific header (default: pssc_reg.hpp)",
    )
    xcp.set_defaults(func=_cmd_cpp_core_path)

    return p


def _cmd_compile(args: argparse.Namespace) -> int:
    res = driver.compile(args.sources, target=args.target, opts=args,
                         raise_on_error=True)
    if args.dump_ir:
        Path(args.dump_ir).write_text(dump_ir(res.context))
    if not args.quiet:
        for path in res.outputs:
            print(path)
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    ctx = driver.translate(args.sources)
    if ctx.errors:
        raise driver.CompileError(
            f"PSS translation failed with {len(ctx.errors)} error(s)", ctx.errors
        )
    if args.dump_ir:
        Path(args.dump_ir).write_text(dump_ir(ctx.ir_context))
        if not args.quiet:
            print(f"wrote {args.dump_ir}")
    elif not args.quiet:
        print(f"parsed OK: {len(ctx.type_map)} types")
    return 0


def _cmd_targets(args: argparse.Namespace) -> int:
    from .targets import style as _styles
    _targets.discover()
    _styles.discover()
    if getattr(args, "overrides", None) is not None:
        return _cmd_overrides(args.overrides)
    for name in _targets.list_targets():
        tgt = _targets.get(name)
        print(f"{name:24} {tgt.description}")
        # The styles available for this target. Listed here rather than behind
        # a separate command because `--style` is a target option: the answer
        # to "what can I pass" belongs beside the target it applies to. Omitted
        # when `default` is the only one, so the common listing does not grow a
        # line per target saying nothing.
        # A derived target's styles include its ancestor's, which is the only
        # reason this is not a plain `list_styles(name)`.
        avail = (tgt.available_styles() if hasattr(tgt, "available_styles")
                 else _styles.list_styles(name))
        if avail and avail != ["default"]:
            print(f"{'':24} styles: {'  '.join(avail)}")
        # What the target publishes as `target_cfg_pkg`. Printed because it is
        # otherwise invisible: it changes which parts of a model elaborate,
        # without appearing in any file the user wrote.
        cfg = tgt.resolved_target_cfg()
        if cfg:
            flags = "  ".join(f"{k}={'true' if v else 'false'}"
                              for k, v in sorted(cfg.items()))
            print(f"{'':24} target_cfg: {flags}")
    # Trailing, on stderr, and not an exit code: the listing above is still
    # correct and still pipeable, but "my target isn't here" now has an answer
    # on screen instead of requiring a debugger.
    report = _targets.plugin_error_report()
    errs = _styles.style_errors()
    if errs:
        n = len(errs)
        report = report + [
            f"{n} style{'' if n == 1 else 's'} failed to load "
            f"(they cannot be selected with --style):"]
        report += [f"  {e}" for e in errs]
    if report:
        print("", file=sys.stderr)
        for line in report:
            print(f"pssc: {line}", file=sys.stderr)
    return 0


def _cmd_overrides(target: str) -> int:
    """`pssc targets --overrides [TARGET]`.

    The manifest, printed. An extension author's first question is "what am I
    allowed to override, and what happens to it next release"; without this the
    answer is to read pssc's source and guess, which is how an unmarked method
    becomes somebody's API.
    """
    from .targets.overridable import report, surface

    names = [target] if target else _targets.list_targets()
    printed = 0
    for name in names:
        tgt = _targets.get(name)
        cls = getattr(tgt, "backend_class", None)
        cls = cls() if callable(cls) else None
        if cls is None or not surface(cls):
            if target:
                print(f"target '{name}' publishes no override surface",
                      file=sys.stderr)
                return 1
            continue
        printed += 1
        print(f"{name}:")
        for line in report(cls):
            print(f"  {line}")
    if not printed:
        print("no target publishes an override surface", file=sys.stderr)
        return 1
    return 0


#: Filename of the bundled SV core package under ``pssc/share/sv``.
SV_CORE_PKG = "pssc_reg_pkg.sv"


def sv_core_dir() -> Path:
    """Return the directory of the bundled SV core package (``pssc/share/sv``)."""
    from .resources import core_dir
    return core_dir("pssc", "sv")


def _cmd_sv_core_path(args: argparse.Namespace) -> int:
    d = sv_core_dir()
    print(d / SV_CORE_PKG if args.file else d)
    return 0


#: Default header names under ``pssc/share/c`` and ``pssc/share/cpp``.
C_CORE_HEADER = "pssc_mem.h"
CPP_CORE_HEADER = "pssc_reg.hpp"


def c_core_dir() -> Path:
    """Return the directory of the bundled C core seam headers (``pssc/share/c``)."""
    from .resources import core_dir
    return core_dir("pssc", "c")


def cpp_core_dir() -> Path:
    """Return the directory of the bundled C++ core header (``pssc/share/cpp``)."""
    from .resources import core_dir
    return core_dir("pssc", "cpp")


def _cmd_c_core_path(args: argparse.Namespace) -> int:
    d = c_core_dir()
    print(d / args.file if args.file else d)
    return 0


def _cmd_cpp_core_path(args: argparse.Namespace) -> int:
    d = cpp_core_dir()
    print(d / args.file if args.file else d)
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0

    try:
        return func(args)
    except driver.CompileError as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        for err in e.errors:
            # EVERY line indented, not just the first: a diagnostic carries a
            # source snippet and a caret, and indenting only the head shifts
            # the caret out from under the token it points at.
            print(diag.indent_block(str(err)), file=sys.stderr)
        return 1
    except Exception:  # pragma: no cover - internal error path
        traceback.print_exc()
        print("pssc: internal error (see traceback above)", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
