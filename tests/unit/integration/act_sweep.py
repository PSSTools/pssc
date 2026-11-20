"""Opt-in sweep (P4.10f1): with ``PSSC_ACT_SWEEP=c``, every model an
integration test runs through be-bc's ``run_model`` is run again on the C
engine, and the two runs' ``message()`` lines and outcome (ran / raised)
are compared. Each run writes one JSON line to ``PSSC_ACT_SWEEP_OUT``
(default ``/tmp/act_sweep.jsonl``): ``verdict`` is ``same``, ``differ``,
``refused`` (the C engine does not run it yet), ``crashed`` (the C run, in a
forked child, died) or ``skipped`` (the run
passes the Python VM something the C engine has no counterpart for, or the
test set ``model.act_sweep_unmirrored`` after changing the model in memory
where the image does not carry it). The
Python run's result is what the test sees; the sweep fails no test.

Without ``PSSC_ACT_SWEEP``, the files in :data:`GUARD` are swept by default
(P4.10f4) and write no record: a run that is not ``same`` or ``skipped``
fails its test at teardown, so the default suite guards C == Python on
pools, picks, claims, loop indices, cone reuse and scheduling."""

import json
import os
import sys

from zuspec.be.bc.interp import oracle

ENV = "PSSC_ACT_SWEEP"
OUT = os.environ.get("PSSC_ACT_SWEEP_OUT", "/tmp/act_sweep.jsonl")

#: run_model arguments the C rerun cannot mirror: the run is not compared
_UNMIRRORED = ("obj", "import_provider", "memory", "on_solve")


#: integration files swept by default (~5k of the full sweep's ~17k runs, about a minute)
GUARD = ("test_scope_solve.py", "test_lookahead.py", "test_generic_constraints.py",
         "test_bc_comp_attr_constraints.py", "test_bc_resources.py", "test_bc_flow_objects.py", "test_bc_loop_index_with.py",
         "test_bc_cone_reuse.py", "test_comp_choice.py", "test_bc_recursion.py",
         "test_bc_channels.py", "test_bc_comp_array.py", "test_bc_if_chain.py",
         "test_bc_interaction_patterns.py")


def enabled() -> bool:
    return os.environ.get(ENV, "") == "c"


def _symbol(model):
    for name, e in (getattr(model, "entries", None) or {}).items():
        if e.coro == model.entry_coro:
            return name
    return None


def _outcome(exc):
    return "ok" if exc is None else "error"


def run_c(model, seed: int, verbosity: int):
    """``(lines, exc)`` of a run of ``model``'s entry on the C engine: its
    info ``message()`` lines and what it raised (None)."""
    from zuspec.be.bc.interp.extern import Memory, RecordingImportProvider
    from zuspec.be.bc.trace import binary, events as ev
    from zuspec.rt.eng.host import Engine, run_sync

    from pssc.testing import native

    symbol = _symbol(model)
    if symbol is None:
        raise native.NativeUnsupported("no entry symbol names the entry coroutine")
    native.check_model(model)
    nobj = native._action_obj(model, symbol)
    lib = native.engine_lib(native.needs_solver(model))
    data = bytearray()
    exc = None
    with Engine(lib, model.to_bytes(), opts=f"verbosity={verbosity}") as eng:
        eng.trace(data.extend, ev.EV_MESSAGE)
        try:
            run_sync(eng.call(symbol, (), seed=seed, obj_slots=nobj),
                     Memory(), RecordingImportProvider())
        except Exception as e:      # noqa: BLE001 - recorded, compared
            exc = e
    lines = []
    if data:
        for d in binary.read(bytes(data))[2]:
            det = d.get("detail") or {}
            if d["kind"] == "message" and det.get("severity", "info") == "info":
                lines.append(det.get("text"))
    return lines, exc


def run_c_forked(model, seed: int, verbosity: int) -> dict:
    """:func:`run_c` in a forked child, so an engine crash is a verdict
    (``crashed``), not the end of the sweep: ``{"lines", "exc"}`` or
    ``{"refused"}`` / ``{"setup"}`` / ``{"crashed"}``."""
    from pssc.testing.native import NativeUnsupported

    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            try:
                lines, exc = run_c(model, seed, verbosity)
                res = {"lines": lines,
                       "exc": exc and f"{type(exc).__name__}: {exc}"[:300]}
            except NativeUnsupported as e:
                res = {"refused": str(e)}
            except Exception as e:      # noqa: BLE001
                res = {"setup": f"{type(e).__name__}: {e}"[:300]}
            os.write(w, json.dumps(res).encode())
        finally:
            os._exit(0)
    os.close(w)
    chunks = []
    while True:
        b = os.read(r, 65536)
        if not b:
            break
        chunks.append(b)
    os.close(r)
    _, status = os.waitpid(pid, 0)
    if not chunks:
        return {"crashed": f"wait status {status}"}
    return json.loads(b"".join(chunks))


def _record(rec):
    rec["test"] = os.environ.get("PYTEST_CURRENT_TEST", "?").rsplit(" ", 1)[0]
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")


def wrap(run_model, bad=None):
    """``run_model`` that also runs the model on C and records the verdict;
    with ``bad`` (a list), appends each record that is not ``same`` or
    ``skipped`` to it instead."""
    def swept(model, *args, **kw):
        seed = args[1] if len(args) > 1 else kw.get("seed", 0)
        mirrored = (not args and not any(kw.get(k) is not None for k in _UNMIRRORED)
                    # a test changed the model in memory where C cannot see it
                    and not getattr(model, "act_sweep_unmirrored", None)
                    # a misuse run_model refuses before it runs
                    and not (kw.get("max_restarts") is not None
                             and kw.get("solve_cache") is not None))
        py_lines = []
        out = kw.get("out")

        def tee(line):
            py_lines.append(line)
            (out or print)(line)
        kw["out"] = tee
        py_exc = None
        try:
            res = run_model(model, *args, **kw)
        except Exception as e:
            py_exc = e
        rec = {"seed": seed}
        if not mirrored:
            rec["verdict"] = "skipped"
        else:
            c = run_c_forked(model, seed, kw.get("verbosity", oracle.VERBOSITY_MEDIUM))
            if "refused" in c:
                rec.update(verdict="refused", why=c["refused"])
            elif "lines" not in c:
                rec.update(verdict="crashed" if "crashed" in c else "differ",
                           why=c.get("crashed") or "C setup: " + c["setup"])
            else:
                c_lines, c_exc = c["lines"], c["exc"]
                same = py_lines == c_lines and _outcome(py_exc) == _outcome(c_exc)
                rec["verdict"] = "same" if same else "differ"
                if not same:
                    rec.update(py=py_lines[:20], c=c_lines[:20],
                               py_exc=py_exc and f"{type(py_exc).__name__}: {py_exc}"[:300],
                               c_exc=c_exc)
        if bad is None:
            _record(rec)
        elif rec["verdict"] not in ("same", "skipped"):
            bad.append(rec)
        if py_exc is not None:
            raise py_exc
        return res
    swept.__wrapped__ = run_model
    return swept


def patch(monkeypatch, bad=None):
    """Point every ``run_model`` the integration tests can reach at the
    sweep's: be-bc's modules and every loaded integration module that
    imported it by name."""
    import zuspec.be.bc.interp as interp
    orig = oracle.run_model
    swept = wrap(orig, bad)
    monkeypatch.setattr(oracle, "run_model", swept)
    monkeypatch.setattr(interp, "run_model", swept)
    for name, mod in list(sys.modules.items()):
        if "integration" in name and getattr(mod, "run_model", None) is orig:
            monkeypatch.setattr(mod, "run_model", swept)
