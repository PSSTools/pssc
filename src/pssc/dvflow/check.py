"""``pssc.Check`` -- parse and elaborate PSS source, generating nothing.

A cheap "does this still link" node for CI, plus the one check that a PSS
front end's *exit status* cannot give you.

Why ``order_check`` compares counts, not status
-----------------------------------------------

PSS has no declare-before-use rule, but front ends do not all implement that.
``docs/design/pssparser-defects-2026-08-02.md`` D3 records a front end that, handed
the same model in a different file order, leaves most cross-file references
unresolved -- and still reports ``0 errors`` and exits 0. A regression test
that asserts on the exit status passes identically before and after the fix,
which is why the defect survived.

So ``order_check`` links the model **twice** -- in the order given, and
reversed, which turns every declaration-first reference into a use-first one --
and compares what actually landed in the IR. Two orders of one model must
produce the same IR content; when they do not, the difference is the measure of
what the front end dropped, and it is reported as node and reference counts
rather than as a pass/fail bit with no evidence in it.

Declaration *order* within the IR legitimately follows file order, so the
comparison is order-insensitive by construction (see :func:`_ir_signature`).
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from typing import Any, Dict, List, Tuple

from .common import gather_pss_sources, run_child

#: Object identities in a serialized back-reference vary run to run.
_REF_ID_RE = re.compile(r"^(\s*_ref: )[0-9]+$", re.M)


def _normalized_dump(context) -> List[str]:
    """The IR serialization, with run-varying ids masked, as sorted lines.

    Sorting is what makes the comparison insensitive to *declaration* order:
    two file orders of one model place the same types in ``type_m`` in
    different positions, which is a presentation difference, not a modeling
    one. Anything the front end failed to resolve changes the lines
    themselves, not merely their order.
    """
    from ..ir import dump_ir

    text = _REF_ID_RE.sub(r"\1<id>", dump_ir(context))
    return sorted(text.splitlines())


def _ir_signature(context) -> Dict[str, int]:
    """Counts that say *how much* of the model reached the IR.

    ``nodes`` per IR class, plus ``_refs`` (resolved back-references) and
    ``_types`` (entries in ``type_m``). These are the numbers reported when two
    orders disagree; D3 showed as 1929 nodes / 358 refs against 1160 / 298.
    """
    counts: Dict[str, int] = {}
    for line in _normalized_dump(context):
        stripped = line.strip()
        if stripped.startswith("_type: "):
            key = stripped[len("_type: "):]
            counts[key] = counts.get(key, 0) + 1
        elif stripped.startswith("_ref: "):
            counts["_refs"] = counts.get("_refs", 0) + 1
    counts["_types"] = len(getattr(context, "type_m", {}) or {})
    return counts


def _signature_delta(a: Dict[str, int], b: Dict[str, int]) -> List[str]:
    """Human-readable ``key: a -> b`` lines for every count that differs."""
    return ["%s: %d -> %d" % (k, a.get(k, 0), b.get(k, 0))
            for k in sorted(set(a) | set(b))
            if a.get(k, 0) != b.get(k, 0)]


def _prelude(target_cfg) -> List[Tuple[str, str]]:
    """Build the `target_cfg_pkg` prelude for a check that names no target.

    `pssc.Check` generates nothing, so it has no target to publish capabilities
    on its behalf -- but a model whose elaboration is gated on `target_cfg_pkg`
    still has to be checkable on both answers. Naming the constants directly is
    what makes that possible without a second copy of the source tree.
    """
    if not target_cfg:
        return []
    from ..targets import target_cfg as _tc
    cfg = _tc.parse_overrides(target_cfg)
    return [(_tc.source_name("check"), _tc.render("check", cfg))]


def _link(sources: List[str], prelude=()) -> Tuple[Any, List[str]]:
    """Parse + elaborate ``sources``; return ``(ir_context, errors)``."""
    from ..driver import translate

    ctx = translate(sources, prelude=prelude)
    return ctx.ir_context, list(ctx.errors)


def evaluate(sources: List[str], target_cfg=None,
             order_check: bool = False) -> Dict[str, Any]:
    """The whole check: link ``sources`` (twice, with ``order_check``) and
    judge the result. Runs in the child process (see :func:`main`).

    Returns ``{"status": 0|1, "markers": [[severity, message], ...]}``, the
    severities ``"error"`` and ``"info"``, which :func:`Check` replays onto
    the task.
    """
    markers: List[List[str]] = []

    def error(msg):
        markers.append(["error", "pssc.Check: " + msg])

    def done(status, info=None):
        if info:
            markers.append(["info", "pssc.Check: " + info])
        return {"status": status, "markers": markers}

    try:
        prelude = _prelude(target_cfg)
    except ValueError as e:
        error(str(e))
        return done(1)

    try:
        context, errors = _link(sources, prelude)
    except Exception as e:  # noqa: BLE001 -- any front-end failure is a marker
        traceback.print_exc()
        error("%s: %s" % (type(e).__name__, e))
        return done(1)

    for msg in errors:
        error(msg)
    if errors:
        return done(1)

    if not order_check:
        return done(0, "%d file(s) elaborated; %d type(s)"
                    % (len(sources), len(getattr(context, "type_m", {}) or {})))

    # --- the order-independence check -------------------------------------
    try:
        reversed_context, reversed_errors = _link(list(reversed(sources)),
                                                  prelude)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        error("reversed file order failed to elaborate (%s: %s); the model "
              "is order-dependent" % (type(e).__name__, e))
        return done(1)

    for msg in reversed_errors:
        error("reversed file order: " + msg)

    forward_sig = _ir_signature(context)
    reverse_sig = _ir_signature(reversed_context)
    delta = _signature_delta(forward_sig, reverse_sig)

    if not delta and _normalized_dump(context) == _normalized_dump(reversed_context):
        if reversed_errors:
            return done(1)
        return done(0, "order-independent -- %d node(s), %d resolved "
                    "reference(s) in both file orders"
                    % (sum(v for k, v in forward_sig.items()
                           if not k.startswith("_")),
                       forward_sig.get("_refs", 0)))

    error("the elaborated model DEPENDS ON FILE ORDER. Linking the same %d "
          "file(s) forwards and reversed produced different IR, with both "
          "runs reporting no errors. Differences (forward -> reversed): %s"
          % (len(sources), "; ".join(delta) if delta
             else "same counts, different content"))
    return done(1)


async def Check(ctxt, input):
    """``pssc.Check`` -- elaborate PSS source; optionally check file-order
    independence.

    The check runs in a child process (``python -m pssc.dvflow.check``, its
    output in the task's ``pssc_check.log``), never in the DFM process; see
    :func:`pssc.dvflow.common.run_child`.
    """
    from dv_flow.mgr import TaskDataResult

    sources = gather_pss_sources(input)
    if not sources:
        ctxt.error("pssc.Check: no 'pssSource' inputs found (needs a "
                   "std.FileSet with type: pssSource)")
        return TaskDataResult(status=1, changed=True)

    out_dir = input.rundir
    os.makedirs(out_dir, exist_ok=True)
    target_cfg = getattr(input.params, "target_cfg", None)
    rc, res = await run_child(
        "pssc.dvflow.check",
        {"sources": sources,
         "target_cfg": list(target_cfg) if target_cfg else None,
         "order_check": bool(getattr(input.params, "order_check", False))},
        out_dir, "pssc_check")

    for sev, msg in res.get("markers", []):
        (ctxt.error if sev == "error" else ctxt.info)(msg)
    if not res:
        ctxt.error("pssc.Check: exited with status %d (see %s)"
                   % (rc, os.path.join(out_dir, "pssc_check.log")))
        return TaskDataResult(status=1, changed=True)
    return TaskDataResult(status=res.get("status", 1), changed=True)


def main(argv=None) -> int:
    """``python -m pssc.dvflow.check REQUEST.json RESULT.json``: the child."""
    argv = sys.argv[1:] if argv is None else argv
    req_path, res_path = argv
    with open(req_path) as f:
        req = json.load(f)
    res = evaluate(req["sources"], req.get("target_cfg"),
                   req.get("order_check", False))
    with open(res_path, "w") as f:
        json.dump(res, f)
    sys.stdout.flush()
    return res["status"]


if __name__ == "__main__":
    sys.exit(main())
