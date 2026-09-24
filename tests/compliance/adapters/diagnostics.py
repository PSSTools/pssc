"""What pssc's adapters write besides the log: outcome.json and diagnostics.json.

The shapes are the corpus's adapter contract (pss-corpus HANDOFF.md). An error's
location comes from pssparser's markers, which ride on the ``ParseException``
-- directly, or as the ``__context__`` of the ``CompileError`` pssc's driver
re-raises it as. An error that carries no marker is left out rather than given
an invented location: the checker then says UNLOCATED, which is the truth.
"""

import json
import os
from typing import Any, Dict, List, Optional

OUTCOME_SCHEMA = "pss-corpus/outcome/1.0"
DIAGNOSTICS_SCHEMA = "pss-corpus/diagnostics/1.0"


def from_exception(exc: Optional[BaseException]) -> List[Dict[str, Any]]:
    """The located diagnostics carried anywhere on *exc*'s cause/context chain."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        markers = getattr(exc, "markers", None)
        if markers:
            return [_diag(m) for m in markers]
        exc = exc.__cause__ or exc.__context__
    return []


def _diag(m: Dict[str, Any]) -> Dict[str, Any]:
    d = {"severity": m.get("severity", "error"), "file": m.get("file"),
         "line": m.get("line"), "message": m.get("message", "")}
    if m.get("col"):
        d["column"] = m["col"]
    return d


def write(out_dir: str, outcome: str, detail: str, diags: List[Dict[str, Any]],
          **ident: Any) -> None:
    with open(os.path.join(out_dir, "outcome.json"), "w") as fp:
        json.dump({"schema": OUTCOME_SCHEMA, "outcome": outcome, **ident,
                   "detail": detail}, fp, indent=2)
    if diags:
        with open(os.path.join(out_dir, "diagnostics.json"), "w") as fp:
            json.dump({"schema": DIAGNOSTICS_SCHEMA, "diagnostics": diags}, fp, indent=2)
