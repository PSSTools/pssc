"""``pssc`` command-line entry point.

Subcommands:
  * ``compile`` — sources -> selected target
  * ``parse``   — front-end only; optionally dump canonical IR as YAML
  * ``targets`` — list available targets
  * ``test``    — discover and run pssunit tests

Exit codes: ``0`` success, ``1`` user error (bad target, parse/translate
failure), ``2`` internal error. ``pssc test`` has its own (see
``pssc.testing.cmd``): ``0`` pass, ``1`` a test failed, ``2`` compile error,
``3`` internal error, ``5`` nothing selected.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import Dict, Optional

from .__version__ import version
from . import diag
from . import driver
from . import preludes as _preludes
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


def _mutate_kinds(text: str) -> tuple:
    """``--mutate-kinds operator,field``: a subset of ``mutate.KINDS``."""
    from .testing.mutate import KINDS
    kinds = tuple(k.strip() for k in text.split(",") if k.strip())
    bad = [k for k in kinds if k not in KINDS]
    if bad or not kinds:
        raise argparse.ArgumentTypeError(
            f"unknown kind {', '.join(bad) or repr(text)}; "
            f"choose from {', '.join(KINDS)}")
    return kinds


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
    # Detached (orphan) comments in a procedural body: a block of comment a
    # blank line cuts off from the statement below it, or one after a
    # block's last statement. Dropped by default, which is how an author
    # suppresses a comment. Unset (None) leaves the choice to the target
    # (`keep_detached_comments` on its class).
    c.add_argument(
        "--keep-detached-comments", dest="keep_detached_comments",
        action="store_true", default=None,
        help="also carry the comments a blank line detaches from a statement "
        "in a function or exec body; file-scope and declaration comments "
        "stay dropped",
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

    # --- test --------------------------------------------------------------
    # `sources` is nargs="*", unlike compile/parse: a src-spec is a directory,
    # a glob or a filelist as well as a file, and running `pssc test` inside a
    # suite should mean the suite. See pssc/testing/sources.py.
    te = sub.add_parser("test", help="discover and run pssunit tests")
    te.add_argument(
        "sources", nargs="*", metavar="SRC-SPEC",
        help="PSS file, directory (searched for *.pss), glob, or @filelist; "
        "FILE:LINE (the tests spanning that line); or a test-ID glob "
        "SUITE::TEST (contains '::'); default: the current directory",
    )
    te.add_argument(
        "-k", dest="k", metavar="EXPR",
        help="run only tests whose name matches EXPR (glob or substring)",
    )
    te.add_argument(
        "-m", dest="tags", metavar="TAG", action="append", default=None,
        help="run only tests tagged TAG (.tags or @pssunit_pkg::tag); "
        "repeat for any of several",
    )
    te.add_argument(
        "--lf", "--last-failed", dest="lf", action="store_true",
        help="run only the tests that failed last time "
        "(.pssunit/lastfailed); all of them if none did",
    )
    te.add_argument(
        "--changed", dest="changed", action="store_true",
        help="skip the tests that last passed with the same slice (their "
        "source and what it reaches by name), options and --seed "
        "(.pssunit/results.json, kept by every run once it exists)",
    )
    te.add_argument(
        "--base", dest="base", metavar="REV",
        help="with --changed: run the tests whose slice differs from their "
        "slice in the sources at git REV (a test new since REV runs); the "
        "result record is not consulted",
    )
    te.add_argument(
        "--watch", dest="watch", action="store_true",
        help="run, then run again with --changed each time a source is "
        "saved, until Ctrl-C; each round goes to the daemon",
    )
    te.add_argument(
        "--image", dest="image", metavar="FILE",
        help="run the tests of image FILE (pssc build --image) instead of "
        "compiling sources; positionals are test IDs only",
    )
    te.add_argument(
        "--fixture", dest="fixture", metavar="NAME",
        help="run only tests rooted in component NAME",
    )
    te.add_argument(
        "--group", dest="group", metavar="NAME",
        help="run only tests annotated @pssunit_pkg::test{.group=NAME}",
    )
    # The two escape hatches. An annotation that cannot be turned off from the
    # command line is a place for a real failure to hide: `--noskip` answers "is
    # this still broken?" and `--runxfail` answers "what is it actually doing?"
    # without editing the source to find out.
    te.add_argument(
        "--noskip", dest="noskip", action="store_true",
        help="run tests annotated .skip=true anyway",
    )
    te.add_argument(
        "--runxfail", dest="runxfail", action="store_true",
        help="report tests annotated .xfail=true by what they actually did",
    )
    # First-class from day one rather than a later convenience: during bring-up
    # it is the fastest way to see whether discovery found what you meant, and
    # it answers "which tests exist, under which root" without solving anything.
    te.add_argument(
        "--collect-only", dest="collect_only", action="store_true",
        help="list the tests that would run; do not run them",
    )
    te.add_argument(
        "--json", dest="json", action="store_true",
        help="print NDJSON events, one per line (schema pssunit.events/1) "
        "instead of the human report; with --collect-only: the catalog as "
        "JSON (schema pssunit.catalog/1)",
    )
    te.add_argument(
        "--seed", dest="seed", type=int, default=None,
        help="solver seed; default: random, and always reported",
    )
    te.add_argument(
        "--seeds", dest="seeds", type=int, metavar="N", default=None,
        help="run every test N times with N seeds (the first is --seed); "
        "a failure reports the seed of the run that failed",
    )
    te.add_argument(
        "-j", "--jobs", dest="jobs", type=int, metavar="N", default=1,
        help="run the tests in N processes; the results equal -j1's",
    )
    te.add_argument(
        "-x", "--exitfirst", dest="exitfirst", action="store_true",
        help="stop after the first failure",
    )
    te.add_argument(
        "--strict", dest="strict", action="store_true",
        help="fail a test that records no checks (it asserted nothing)",
    )
    te.add_argument(
        "--targets", dest="targets", metavar="T1,T2", default=None,
        help="op-model targets whose gate the code under test must pass "
        "before tests run; default: [tool.pssunit].targets, else op-model-c; "
        "'none' gates nothing",
    )
    te.add_argument(
        "--engine", dest="engine", choices=("py", "c", "gen-py", "gen-sv"),
        default=None,
        help="the engine the tests run on: py (the Python VM), c (the C "
        "engine; a test it cannot run yet is an ERROR that says why) or "
        "gen-py (the op-model-py generated code, on the fake, checked "
        "against a py run: a different bus access FAILs; a test that "
        "cannot be exported is SKIPped with the reason) or gen-sv (the "
        "op-model-sv generated code under Verilator, served the py run's "
        "reads and checked the same way); "
        "default: $PSSC_ENGINE, else py",
    )
    te.add_argument(
        "--platform", dest="platform", metavar="fake|py:MOD[:CLASS]|sim:S",
        default=None,
        help="the platform for every test: fake (a register fake per test), "
        "or MOD's @pv.* doubles and a new CLASS() per test, or sim:S to "
        "build, hot reload and run the tests in server-mode simulation S; "
        "default: [tool.pssunit].platform",
    )
    te.add_argument(
        "--reg-semantics", dest="reg_semantics", metavar="FILE", default=None,
        help="register field kinds (clear on read, write 1 to clear or set, "
        "read-only, ...) from an IP-XACT .xml, a SystemRDL .rdl (needs "
        "systemrdl-compiler) or a JSON {register: {field: kind}} table, for the fake and the register checks; a field_access "
        "annotation wins over it",
    )
    te.add_argument(
        "--no-debug", dest="debug", action="store_false",
        help="lower the release image (no statement markers, calls inlined)",
    )
    te.add_argument(
        "--cov", dest="cov", metavar="PATH", default=None,
        help="write statement, branch and register coverage to PATH: a "
        "covsight database (UCIS, with per-test history) if PATH ends in "
        ".cdb, else JSON; needs the debug image",
    )
    te.add_argument(
        "--trace", dest="trace", choices=("off", "fail", "all"), default="off",
        help="write each test's events (statements, calls, register "
        "accesses), with source lines, as NDJSON: 'all' for every test, "
        "'fail' for failing ones; needs the debug image; default: off",
    )
    te.add_argument(
        "--trace-dir", dest="trace_dir", metavar="DIR", default=None,
        help="where --trace writes <test-id>.ndjson; default: .pssunit/traces",
    )
    te.add_argument(
        "--cov-scope", dest="cov_scope", metavar="COMP", action="append",
        default=None,
        help="count coverage only in component type COMP, its sub-components "
        "and its base and derived types (repeatable); default: the tests' "
        "@pssunit_pkg::under_test, else all but tests, mocks and libraries",
    )
    te.add_argument(
        "--ctrf", dest="ctrf", metavar="PATH", default="pssunit-ctrf.json",
        help="write a CTRF JSON report to PATH; default: pssunit-ctrf.json",
    )
    te.add_argument(
        "--report", dest="ctrf", metavar="PATH",
        help="same as --ctrf PATH",
    )
    te.add_argument(
        "--junit", dest="junit", metavar="PATH", default=None,
        help="also write a JUnit XML report to PATH",
    )
    te.add_argument(
        "--no-ctrf", dest="ctrf", action="store_const", const=None,
        help="do not write a CTRF report",
    )
    te.add_argument(
        "--no-doctest", dest="doctest", action="store_false",
        help="do not run the pss-example blocks of doc comments as tests",
    )
    te.add_argument(
        "--mutate", action="store_true",
        help="after a green run, run each mutant of the model (a flipped "
        "operator, a constant off by one, a moved offset, a swapped field) on "
        "the tests that reach it and list the ones no test kills; -j N runs N "
        "mutants at once; exit 1 if any survive",
    )
    te.add_argument(
        "--mutate-kinds", metavar="K,K", type=_mutate_kinds, default=None,
        help="with --mutate, only these kinds of mutant: operator, constant, "
        "offset, field (default: all)",
    )
    te.add_argument(
        "--mutate-max", metavar="N", type=int, default=None,
        help="with --mutate, run at most N mutants, a sample picked by --seed",
    )
    te.add_argument("-v", "--verbose", action="store_true",
                    help="per-test detail (qualified name, seed, timing)")
    te.add_argument("-q", "--quiet", action="store_true",
                    help="summary line only")
    te.set_defaults(func=_cmd_test)

    # --- model -------------------------------------------------------------
    from .devenv.model_cmd import add_parser as _add_model_parser
    _add_model_parser(sub)
    from .devenv.build_cmd import add_parser as _add_build_parser
    _add_build_parser(sub)
    from .devenv.dbg_cmd import add_parser as _add_dbg_parser
    _add_dbg_parser(sub)
    from .devenv.sim_cmd import add_parser as _add_sim_parser
    _add_sim_parser(sub)
    from .devenv.dap_cmd import add_parser as _add_dap_parser
    _add_dap_parser(sub)
    from .devenv.cov_cmd import add_parser as _add_cov_parser
    _add_cov_parser(sub)
    from .devenv.trace_cmd import add_parser as _add_trace_parser
    _add_trace_parser(sub)
    from .devenv.client import add_parser as _add_daemon_parser
    _add_daemon_parser(sub)
    from .devenv.help_cmd import add_parser as _add_help_parser
    _add_help_parser(sub)

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

    spp = sub.add_parser(
        "sv-pssvm-path",
        help="print the directory of the bundled SV engine package (pssvm_pkg), "
             "which runs a `pssc build --image` image in a simulation",
    )
    spp.add_argument(
        "--file", action="store_true",
        help="print the path to pssvm_pkg.sv instead of its directory",
    )
    spp.add_argument(
        "--py", action="store_true",
        help="print the PYTHONPATH the simulation needs for the pyvm engine "
             "(pssvm_hif_pkg over pyhdl-if: pssc and pssvm_dpi_shim)",
    )
    spp.add_argument(
        "--lib", action="store_true",
        help="print the path to the C engine's shared library (built on first "
             "use) that a simulation links for the native engine "
             "(+define+PSSVM_DPI, class pssvm_engine_dpi)",
    )
    spp.set_defaults(func=_cmd_sv_pssvm_path)

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
    try:
        res = driver.compile(args.sources, target=args.target, opts=args,
                             raise_on_error=True)
    finally:
        # A warning, not an error, and printed whether or not the compile
        # succeeded: a missing contribution matters only to a model that uses
        # it, and that model's `unknown type` should have its cause beside it.
        for line in _preludes.prelude_error_report():
            print(f"pssc: warning: {line}", file=sys.stderr)
    if args.dump_ir:
        Path(args.dump_ir).write_text(dump_ir(res.context))
    if not args.quiet:
        for path in res.outputs:
            print(path)
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    # No target, but the installed `pssc.preludes` contributions: a model
    # that compiles must also parse.
    ctx = driver.translate(args.sources,
                           prelude=driver.prelude_units(None, args))
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


def _cmd_test(args: argparse.Namespace) -> int:
    from .testing.cmd import cmd_test
    return cmd_test(args)


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
    # Contributors that failed to LOAD. Asked with no compile, so a failure
    # inside a contributor's call shows only on `pssc compile`.
    _preludes.discover()
    report = report + _preludes.prelude_error_report()
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
        em = getattr(cls, "body_emitter_class", None)
        em = em() if callable(em) else None
        if em is not None and surface(em):
            for line in report(em):
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


#: The SV engine package under ``pssc/share/sv/pssvm``.
SV_PSSVM_PKG = "pssvm_pkg.sv"


def sv_pssvm_dir() -> Path:
    """Return the directory of the bundled SV engine package (``pssc/share/sv/pssvm``)."""
    return sv_core_dir() / "pssvm"


def sv_pssvm_pythonpath() -> str:
    """PYTHONPATH for pyvm in a simulation: pssc's parent and the shim's directory."""
    pkg = Path(__file__).resolve().parent
    return os.pathsep.join([str(pkg.parent), str(pkg / "share" / "py")])


def _cmd_sv_pssvm_path(args: argparse.Namespace) -> int:
    if args.py:
        print(sv_pssvm_pythonpath())
        return 0
    if getattr(args, "lib", False):
        from .testing.native import engine_lib
        print(engine_lib())
        return 0
    d = sv_pssvm_dir()
    print(d / SV_PSSVM_PKG if args.file else d)
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


def parse_argv(parser: argparse.ArgumentParser, argv) -> argparse.Namespace:
    """``parser.parse_args(argv)``, except that `pssc test` takes SRC-SPECs
    after options too (``pssc test @m.f --platform fake S::T``): argparse
    fills a ``nargs="*"`` positional once, so the later ones come back as
    extras. (``parse_intermixed_args`` refuses parsers with subcommands.)"""
    args, extra = parser.parse_known_args(argv)
    if extra and getattr(args, "command", None) == "test" \
            and not any(e.startswith("-") for e in extra):
        args.sources = list(args.sources or []) + extra
    elif extra:
        parser.error("unrecognized arguments: %s" % " ".join(extra))
    return args


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parse_argv(parser, argv)

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
