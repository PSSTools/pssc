"""Build a matrix module's cases together.

A case's C, C++ or SV trace costs a compiler or Verilator run. Made one at a
time, the matrix's run time grows with every case; made together, one per
core, it grows with the slowest. `matrix_results` generates every SELECTED
case of the module (so `-k` still builds only what it runs), finishes the
builds with `trace_harness.run_many`, and hands each test its trace -- or the
exception its generation or build raised, which the test re-raises as its
own failure.
"""
from __future__ import annotations

import re

import pytest

from .. import trace_harness as th
from ._matrix import REGS, Refused, expected


@pytest.fixture(scope="module")
def matrix_results(request, tmp_path_factory):
    cases = request.module.CASES
    results, keys, builds = {}, [], []
    for item in request.session.items:
        if item.module is not request.module:
            continue
        params = getattr(getattr(item, "callspec", None), "params", {})
        name, target = params.get("name"), params.get("target")
        if name not in cases or target is None or th.available(target):
            continue
        case = cases[name]
        if isinstance(expected(case, target), Refused):
            continue
        d = tmp_path_factory.mktemp(
            re.sub(r"\W+", "_", f"{target}_{name}")[:40])
        try:
            got = th.prepare(target, d, REGS + case.pss, case.args, case.mem)
        except Exception as e:                  # noqa: BLE001 -- re-raised
            results[(name, target)] = e
            continue
        if isinstance(got, th.Build):
            keys.append((name, target))
            builds.append(got)
        else:
            results[(name, target)] = got
    results.update(zip(keys, th.run_many(builds)))
    return results
