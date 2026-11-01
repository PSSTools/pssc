"""The child process a ``pssc`` build task compiles in.

``python -m pssc.dvflow.compile_child REQUEST.json RESULT.json``

:func:`pssc.dvflow.common.run_build` runs every compile here rather than in the
DFM process. A compile is then independent of whatever else that process is
doing: its prints and log handlers go to the child's stdout (the task's
``pssc_compile.log``), not to a ``sys.stdout`` other tasks share; the
generators it imports load in a process of their own, so two tasks importing
the same backend at once cannot deadlock on Python's import lock; and a crash
or leak in a target ends with the child.

REQUEST is JSON: ``sources``, ``target``, ``output_dir``, ``export_actions``,
``args`` (raw ``pssc compile`` options) and ``overrides`` (option-namespace
keywords). The options are built as ``pssc compile`` builds them -- the CLI's
defaults, then ``args`` -- and ``overrides`` are applied on top, so a task
behaves as the same command line would.

RESULT is JSON: ``outputs`` (the written paths) and ``errors`` (PSS
diagnostics). The exit status is 0 on success, 1 on a PSS error, 2 on anything
else (the traceback is in the log).
"""
from __future__ import annotations

import json
import sys
import traceback


def _options(req):
    from pssc.cli import build_parser

    argv = ["compile", *req["sources"], "-t", req["target"],
            "-o", req["output_dir"], *req.get("args", [])]
    return build_parser().parse_args(argv)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m pssc.dvflow.compile_child REQUEST.json RESULT.json",
              file=sys.stderr)
        return 2
    req_path, res_path = argv
    with open(req_path) as f:
        req = json.load(f)

    result = {"outputs": [], "errors": []}
    status = 0
    try:
        from pssc.driver import CompileError, compile as pssc_compile
        overrides = dict(req.get("overrides") or {})
        if req.get("export_actions"):
            overrides["export_actions"] = list(req["export_actions"])
        try:
            res = pssc_compile(req["sources"], target=req["target"],
                               output_dir=req["output_dir"],
                               opts=_options(req), **overrides)
            result["outputs"] = [str(p) for p in res.outputs]
        except CompileError as e:
            result["errors"] = list(e.errors) or [str(e)]
            status = 1
    except SystemExit:
        # argparse rejected `args`; its message is already on stderr.
        result["errors"] = ["invalid pssc options %s (see pssc_compile.log)"
                            % req.get("args", [])]
        status = 2
    except BaseException as e:  # noqa: BLE001 -- reported through RESULT
        traceback.print_exc()
        result["errors"] = ["%s: %s" % (type(e).__name__, e)]
        status = 2

    with open(res_path, "w") as f:
        json.dump(result, f)
    sys.stdout.flush()
    return status


if __name__ == "__main__":
    sys.exit(main())
