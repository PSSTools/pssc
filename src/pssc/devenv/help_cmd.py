"""`pssc help [COMMAND...] [--json]` (P1.18c).

``--json`` describes the whole CLI as one document (schema ``pssc.help/1``,
docs/schemas/pssc.help-1.json): every command and sub-command, its arguments,
and the schema of each machine-readable output it can print. It is built by
walking the real argparse tree, so it cannot drift from what the parser
accepts; agents and the generated CLI reference (D4) read it.

Without ``--json``, ``pssc help test`` is ``pssc test --help``. ``--rst``
prints the same document as the reStructuredText page
docs/cli-reference.rst (D4; T-D2 checks the checked-in page matches).
"""
from __future__ import annotations

import argparse
import json
import re

#: The ``pssc help --json`` schema; see docs/schemas/pssc.help-1.json.
SCHEMA = "pssc.help/1"

#: The machine-readable outputs of each command (by its path of names), and
#: the option that selects each. ``file`` is under docs/schemas/ when the
#: schema is published there.
OUTPUTS = {
    ("test",): [
        {"when": ["--json"], "schema": "pssunit.events/1", "format": "ndjson",
         "file": "docs/schemas/pssunit.events-1.json"},
        {"when": ["--collect-only", "--json"], "schema": "pssunit.catalog/1",
         "format": "json", "file": "docs/schemas/pssunit.catalog-1.json"},
        {"when": ["--report"], "schema": "ctrf", "format": "json-file"},
        {"when": ["--junit"], "schema": "junit-xml", "format": "xml-file"},
        {"when": ["--cov"], "schema": "pssunit.coverage/1", "format": "json-file"},
        {"when": ["--cov"], "schema": "ucis-ncdb", "format": "cdb-file"},
        {"when": ["--trace"], "schema": "zbc-trace/2", "format": "ndjson-file"},
    ],
    ("model", "api"): [{"when": ["--json"], "schema": "pssc.model/1",
                        "format": "json",
                        "file": "docs/schemas/pssc.model-1.json"}],
    ("model", "regs"): [{"when": ["--json"], "schema": "pssc.model/1",
                         "format": "json",
                         "file": "docs/schemas/pssc.model-1.json"}],
    ("model", "explain"): [{"when": ["--json"], "schema": "pssc.model/1",
                            "format": "json",
                            "file": "docs/schemas/pssc.model-1.json"}],
    ("model", "tests"): [{"when": ["--json"], "schema": "pssunit.catalog/1",
                          "format": "json",
                          "file": "docs/schemas/pssunit.catalog-1.json"}],
    **{("dbg", c): [{"when": ["--json"], "schema": "pssunit.stop/1",
                     "format": "json", "file": "docs/schemas/pssunit.stop-1.json"}]
       for c in ("start", "run", "continue", "step", "next", "out", "back",
                 "rc")},
    # Not versioned: {session, ...}; see docs/debugging.rst.
    **{("dbg",) + c: [{"when": ["--json"], "schema": None, "format": "json"}]
       for c in (("break", "add"), ("break", "list"), ("watch",), ("pause",),
                 ("bt",), ("locals",), ("regs",), ("bus",), ("eval",),
                 ("threads",), ("reload",), ("end",), ("list",))},
    ("cov", "gaps"): [{"when": ["--json"], "schema": "pssc.cov-gaps/1",
                       "format": "json",
                       "file": "docs/schemas/pssc.cov-gaps-1.json"}],
    ("cov", "report"): [{"when": ["--json"], "schema": "pssc.cov-report/1",
                         "format": "json",
                         "file": "docs/schemas/pssc.cov-report-1.json"}],
    ("cov", "diff"): [{"when": ["--json"], "schema": "pssc.cov-diff/1",
                       "format": "json",
                       "file": "docs/schemas/pssc.cov-diff-1.json"}],
    ("build",): [{"when": ["--image"], "schema": "zbc-debug", "format": "zbc-file"},
                 {"when": ["--json"], "schema": "pssc.image/1", "format": "json"}],
    ("trace", "show"): [{"when": ["--json"], "schema": "pssc.trace-show/1",
                         "format": "json",
                         "file": "docs/schemas/pssc.trace-show-1.json"}],
    ("trace", "why"): [{"when": ["--json"], "schema": "pssc.trace-why/1",
                        "format": "json",
                        "file": "docs/schemas/pssc.trace-why-1.json"}],
    ("trace", "slice"): [{"when": ["--json"], "schema": "pssc.trace-slice/1",
                          "format": "json",
                          "file": "docs/schemas/pssc.trace-slice-1.json"}],
    ("trace", "diff"): [{"when": ["--json"], "schema": "pssc.trace-diff/1",
                         "format": "json",
                         "file": "docs/schemas/pssc.trace-diff-1.json"}],
    ("cov", "merge"): [
        {"when": ["-o"], "schema": "pssunit.coverage/1", "format": "json-file"},
        {"when": ["-o"], "schema": "ucis-ncdb", "format": "cdb-file"}],
    ("cov", "lcov"): [{"when": ["-o"], "schema": "lcov", "format": "lcov-file"}],
    ("cov", "cobertura"): [{"when": ["-o"], "schema": "cobertura",
                            "format": "xml-file"}],
    # Not versioned: {root, running, ...}; see docs/cli.md `pssc daemon`.
    ("daemon",): [{"when": ["--json"], "schema": None, "format": "json"}],
    # Not versioned: {ok, sessions|session, ...}; see docs/simulation.rst.
    **{("sim", c): [{"when": ["--json"], "schema": None, "format": "json"}]
       for c in ("list", "run", "log", "reload", "end")},
    ("help",): [{"when": ["--json"], "schema": SCHEMA, "format": "json",
                 "file": "docs/schemas/pssc.help-1.json"}],
}


def add_parser(sub) -> None:
    """Register `pssc help` on the top-level subparsers *sub*."""
    h = sub.add_parser("help", help="describe the commands (--json: every "
                       "command, its arguments and output schemas)")
    h.add_argument("topic", nargs="*", metavar="COMMAND",
                   help="the command to describe (default: all)")
    h.add_argument("--json", action="store_true",
                   help="print one JSON document (schema pssc.help/1)")
    h.add_argument("--rst", action="store_true",
                   help="print the CLI reference page (docs/cli-reference.rst)")
    h.set_defaults(func=cmd_help)


def cmd_help(args: argparse.Namespace) -> int:
    from ..cli import build_parser
    parser = build_parser()
    target = parser
    for name in args.topic:
        subs = _subparsers(target)
        if name not in subs:
            print(f"pssc: error: unknown command: {' '.join(args.topic)}")
            return 2
        target = subs[name]
    if args.rst:
        print(render_rst(describe(parser)), end="")
        return 0
    if not args.json:
        target.print_help()
        return 0
    if args.topic:
        doc = _command(args.topic[-1], target, tuple(args.topic), None)
        doc = dict(schema=SCHEMA, commands=[doc])
    else:
        doc = describe(parser)
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


def describe(parser: argparse.ArgumentParser) -> dict:
    """The ``pssc.help/1`` document for the top-level *parser*."""
    from ..__version__ import version
    helps = _sub_helps(parser)
    return {
        "schema": SCHEMA,
        "prog": parser.prog,
        "version": str(version),
        "args": [_arg(a) for a in _plain(parser)],
        "commands": [_command(n, p, (n,), helps.get(n))
                     for n, p in _subparsers(parser).items()],
    }


def _command(name, parser, path, help_text) -> dict:
    helps = _sub_helps(parser)
    out = {
        "name": name,
        "path": list(path),
        "help": help_text if help_text is not None else parser.description,
        "args": [_arg(a) for a in _plain(parser)],
    }
    subs = _subparsers(parser)
    if subs:
        out["commands"] = [_command(n, p, path + (n,), helps.get(n))
                           for n, p in subs.items()]
    if path in OUTPUTS:
        out["outputs"] = OUTPUTS[path]
    return out


def _subparsers(parser) -> dict:
    """name -> parser, in registration order (aliases dropped)."""
    out = {}
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for n, p in a.choices.items():
                if p not in out.values():
                    out[n] = p
    return out


def _sub_helps(parser) -> dict:
    out = {}
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for c in a._choices_actions:
                out[c.dest] = c.help
    return out


def _plain(parser):
    return [a for a in parser._actions
            if not isinstance(a, (argparse._SubParsersAction,
                                  argparse._HelpAction))]


_KINDS = {
    argparse._StoreTrueAction: "flag", argparse._StoreFalseAction: "flag",
    argparse._StoreConstAction: "const", argparse._AppendAction: "append",
    argparse._CountAction: "count", argparse._VersionAction: "version",
}


def _arg(a: argparse.Action) -> dict:
    kind = _KINDS.get(type(a), "store")
    if kind == "flag":
        typ = "bool"
    elif a.type is None:
        typ = "str" if kind in ("store", "append") else None
    else:
        typ = getattr(a.type, "__name__", None)
        # `type=lambda s: int(s, 0)` and the like: an integer in any base.
        typ = "int" if typ == "<lambda>" else typ
    out = {
        "name": a.dest,
        "flags": list(a.option_strings),
        "positional": not a.option_strings,
        "kind": kind,
        "type": typ,
        # argparse marks a `*` positional required; it is not.
        "required": (bool(a.required) if a.option_strings
                     else a.nargs not in ("?", "*")),
        "default": _jsonable(a.default),
        "help": a.help,
    }
    if a.nargs is not None:
        out["nargs"] = a.nargs if isinstance(a.nargs, (int, str)) else str(a.nargs)
    if a.choices is not None:
        out["choices"] = [_jsonable(c) for c in a.choices]
    if a.metavar is not None:
        out["metavar"] = a.metavar if isinstance(a.metavar, str) else list(a.metavar)
    if a.const is not None:
        out["const"] = _jsonable(a.const)
    return out


def _jsonable(v):
    if v is argparse.SUPPRESS:
        return None
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return str(v)


#: The command that rewrites docs/cli-reference.rst (from the repo root).
REGENERATE = "PYTHONPATH=./src python -m pssc help --rst > docs/cli-reference.rst"

_UNDERLINE = "-~^"


def render_rst(doc: dict) -> str:
    """The CLI reference page (D4) for the ``pssc.help/1`` *doc*.

    The version is left out, so a release does not change the page.
    """
    out = [
        ".. Generated from ``pssc help --json``; do not edit. Regenerate with:",
        "   " + REGENERATE,
        "",
        "CLI reference",
        "=============",
        "",
        "Every ``%s`` command and option, generated from ``pssc help --json``"
        " (schema ``%s``, `docs/schemas/pssc.help-1.json`_), which reads the"
        " real argument parser. The guide with examples and exit codes is"
        " ``docs/cli.md``." % (doc["prog"], doc["schema"]),
        "",
        ".. _docs/schemas/pssc.help-1.json: schemas/pssc.help-1.json",
        "",
    ]
    if doc["args"]:
        out += ["Global options:", ""] + _rst_args(doc["args"])
    for c in doc["commands"]:
        _rst_command(out, doc["prog"], c, 0)
    return "\n".join(out).rstrip("\n") + "\n"


def _rst_command(out, prog, c, depth) -> None:
    title = "``%s %s``" % (prog, " ".join(c["path"]))
    out += [title, _UNDERLINE[depth] * len(title), ""]
    if c.get("help"):
        out += [_rst_text(c["help"]), ""]
    if c["args"]:
        out += _rst_args(c["args"])
    if c.get("outputs"):
        out += ["Machine-readable output:", ""]
        for o in c["outputs"]:
            line = "- ``%s``: %s (%s)" % (
                " ".join(o["when"]),
                "``%s``" % o["schema"] if o["schema"] else "unversioned",
                o["format"])
            if o.get("file"):
                line += ", schema ``%s``" % o["file"]
            out.append(line)
        out.append("")
    for s in c.get("commands", []):
        _rst_command(out, prog, s, depth + 1)


def _rst_args(args) -> list:
    out = []
    for a in args:
        if a["kind"] == "version":
            term = ", ".join(a["flags"])
        else:
            mv = a.get("metavar") or (a["name"].upper() if a["positional"] or
                                      a["kind"] in ("store", "append") else None)
            if isinstance(mv, list):
                mv = " ".join(mv)
            if a["positional"]:
                term = mv
            elif mv and a["kind"] in ("store", "append"):
                term = ", ".join("%s %s" % (f, mv) for f in a["flags"])
            else:
                term = ", ".join(a["flags"])
        out.append("``%s``" % term)
        notes = []
        if a.get("help"):
            notes.append(_rst_text(a["help"]))
        extra = []
        if a["positional"]:
            extra.append("required" if a["required"] else "optional")
        elif a["required"]:
            extra.append("required")
        said = (a.get("help") or "").lower()
        if a["kind"] == "append" and "repeat" not in said:
            extra.append("may repeat")
        if a.get("choices"):
            extra.append("one of " + ", ".join("``%s``" % c for c in a["choices"]))
        if (a["kind"] in ("store", "append") and "default" not in said
                and a["default"] not in (None, [], "")):
            extra.append("default ``%s``" % (a["default"],))
        if extra:
            notes.append("(%s)" % "; ".join(extra))
        out += ["   " + (" ".join(notes) or "(no description)"), ""]
    return out


def _rst_text(text: str) -> str:
    """argparse help as inline reST: `x` becomes ``x``; markup escaped."""
    text = " ".join(text.split())
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for i, p in enumerate(parts):
        if i % 2:
            out.append("``%s``" % p[1:-1])
        else:
            out.append(re.sub(r"([\\*|]|_(?!\w))", r"\\\1", p))
    return "".join(out)
