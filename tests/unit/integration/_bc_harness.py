"""PSS source → bc backend → dv-solve. The seam construct-level tests run on.

A construct-level test asks what a PSS construct *means* per the LRM, and observes
the answer through the smallest vehicle that can observe it: the bytecode backend
interpreted in Python, solving with real dv-solve. It is deliberately not a codegen
test -- a generated-SV diff cannot tell "correctly inert" from "silently dropped".

The pipeline, and what each stage is responsible for:

    PSS text
      → pssc.Parser().parse()/.link()      syntax + name resolution
      → AstToIrTranslator().translate()    AST → Layer-0 PSS-semantic IR
      → PSSToScenarioPass().lower()        → Layer-1 scenario (solve problems)
      → zuspec.be.bc.lower.lower_module()  → ZBC
      → zuspec.be.bc.interp.run_model()    execute, SOLVE via dv-solve

Because every stage below the frontend is shared with suites that test it directly,
a failure here is attributable to the frontend -- which is the diagnostic property
that makes the category worth having.

Note the existing ``test_pss_*_constraints_rt.py`` suites use a *different* vehicle
(``load_pss`` + ``zuspec.dataclasses.randomize``, the Python-translation path). This
harness is the bc path; the two are independent observers of the same frontend.
"""
import os
import re
import tempfile
from typing import Dict, Iterable, List, Optional

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core import Function
from zuspec.ir.core.xf import PSSToScenarioPass
from zuspec.be.bc.interp import NativeBlobBackend, Obj, run_model
from zuspec.be.bc.lower import lower_module


def translate(src: str):
    """PSS text → Layer-0 IR context. Raises on a parse or link error."""
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def _ensure_lowerable(dt) -> None:
    """Give a rand-only atomic action an empty ``body`` so the pass can see it.

    ``PSSToScenarioPass`` recognizes an action by its exec body or its activity, so
    an action that only declares rand fields and constraints -- which is exactly the
    shape a constraint test wants -- is otherwise invisible. An empty body is a
    semantic no-op and leaves the solve problem intact.
    """
    fns = list(getattr(dt, "functions", []) or [])
    if getattr(dt, "activity_ir", None) is None and \
            not any(getattr(f, "name", None) == "body" for f in fns):
        dt.functions = fns + [Function(name="body", body=[])]


def solve_pss(src: str, *, action: str = "pss_top::A",
              seed: int = 0) -> Dict[str, int]:
    """Solve *action*'s rand fields once. Returns ``{field: value}``."""
    return solve_many(src, action=action, seeds=[seed])[0]


def solve_many(src: str, *, action: str = "pss_top::A",
               seeds: Iterable[int] = range(32)) -> List[Dict[str, int]]:
    """Solve *action* once per seed. Returns one ``{field: value}`` per seed.

    The model is lowered once and re-run per seed, so a difference across seeds is
    the solver's, not the compiler's.
    """
    ctx = translate(src)
    dt = ctx.type_map[action]
    _ensure_lowerable(dt)

    simple = action.rsplit("::", 1)[-1]
    model = lower_module(PSSToScenarioPass(exports=[simple]).lower(ctx),
                         entry_action=simple)

    names = [f.name for f in getattr(dt, "fields", [])
             if getattr(f, "rand_kind", None) is not None]
    return [run_model(model, obj=Obj(field_names=names), seed=seed,
                      solve_backend=NativeBlobBackend()).fields
            for seed in seeds]


def expect_error(src: str, match: str, *, action: str = "pss_top::A") -> str:
    """Assert compiling *src* fails with a message matching *match*.

    Covers the whole frontend, not just the parser: a construct may be rejected at
    parse, at link (name resolution), or during translation, and which one it is
    counts as an implementation detail rather than a spec fact.
    """
    try:
        ctx = translate(src)
    except Exception as exc:                       # parse or link
        text = str(exc)
    else:                                          # translation-time diagnostics
        text = "\n".join(ctx.errors)
        if not text:
            pytest.fail(f"expected an error matching {match!r}; compiled cleanly")
    if not re.search(match, text):
        pytest.fail(f"expected an error matching {match!r}, got:\n{text}")
    return text


def solves_in(results: List[Dict[str, int]], field: str,
              lo: Optional[int] = None, hi: Optional[int] = None) -> None:
    """Assert every solved *field* lies within ``[lo, hi]``, naming the seed."""
    for seed, fields in enumerate(results):
        val = fields[field]
        if lo is not None and val < lo:
            pytest.fail(f"seed={seed}: {field}={val} < {lo}")
        if hi is not None and val > hi:
            pytest.fail(f"seed={seed}: {field}={val} > {hi}")
