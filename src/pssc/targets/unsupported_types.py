"""Types a target does not render, found before anything is emitted.

Some PSS types have no rendering on some op-model targets, by ruling
(docs/design/construct-test-audit/00-plan.md §7, §7.1): list, map and set on
every op-model target (R4), `string` on C and C++ (R3). A model using one was
an internal error when an emitter first met the type ("unsupported C type
for DataTypeString"), or was refused for a METHOD of it ("no function named
'push_back' ... declare it as an import"), which names the wrong problem.
This walk refuses the TYPE, naming every place it is used -- a field, a
parameter, a result, a local -- before the call gate runs.

The walk is `int_width`'s: every field of every component in the tree, the
struct types those reach, and each function's parameters, result and body.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List

from ..diag import at
from .progseq_model import _dt_name

#: Datatypes the walk never enters (rendered, or not, as themselves).
_OPAQUE = ("DataTypeComponent", "DataTypeAction", "DataTypeClass")

#: A refused type's PSS spelling, by IR class.
_PSS_NAME = {"DataTypeList": "list", "DataTypeMap": "map",
             "DataTypeSet": "set", "DataTypeString": "string"}


def _is_core(dtype) -> bool:
    """A core-library struct (`addr_reg_pkg::transparent_addr_region_s`).
    Whether a target takes one is the call gate's question -- it refuses the
    functions that make or take one -- not a model's use of a type."""
    from .call_legality import CORE_PACKAGES
    name = getattr(dtype, "name", None) or ""
    return "::" in name and name.split("::")[0] in CORE_PACKAGES


def _name(dtype) -> str:
    return (getattr(dtype, "name", None) or "?").split("::")[-1]


def find(model, refused: Dict[str, str]) -> List[str]:
    """``"<where>: a <type> (<why>)"`` for each use of a type in
    ``refused`` (IR class name -> reason), in model order, each once."""
    out: List[str] = []
    seen_structs = set()

    def hit(dtype, where: str, node) -> bool:
        cn = _dt_name(dtype)
        if cn in refused:
            out.append(at(node, f"{where}: a {_PSS_NAME.get(cn, cn)} "
                                f"({refused[cn]})"))
            return True
        return False

    def datatype(dtype, where: str, node) -> None:
        """``node``: the declaration a refusal is located at."""
        if dtype is None or hit(dtype, where, node):
            return
        cn = _dt_name(dtype)
        if cn == "DataTypeStruct":
            if id(dtype) in seen_structs or _is_core(dtype):
                return
            seen_structs.add(id(dtype))
            for f in getattr(dtype, "fields", None) or []:
                datatype(getattr(f, "datatype", None),
                         f"{_name(dtype)}.{f.name}", (f, dtype))
        elif cn == "DataTypeArray":
            datatype(getattr(dtype, "element_type", None), where, node)

    def body(stmts, where: str) -> None:
        seen = set()

        def walk(n) -> None:
            if n is None or isinstance(n, (str, int, float, bool, type)):
                return
            if isinstance(n, (list, tuple)):
                for c in n:
                    walk(c)
                return
            if not dc.is_dataclass(n) or id(n) in seen:
                return
            seen.add(id(n))
            cn = type(n).__name__
            if cn in _OPAQUE:
                return
            if cn == "StmtAnnAssign":
                local = getattr(getattr(n, "target", None), "name", "?")
                datatype(n.annotation, f"{where}: local '{local}'", n)
            for f in dc.fields(n):
                if not (cn == "StmtAnnAssign" and f.name == "annotation"):
                    walk(getattr(n, f.name, None))

        walk(stmts)

    funcs: List[Any] = []
    for comp in model.comp_dtypes_root_first:
        cname = _name(comp)
        for f in getattr(comp, "fields", None) or []:
            datatype(getattr(f, "datatype", None), f"{cname}.{f.name}",
                     (f, comp))
        funcs += [(cname, fn) for fn in getattr(comp, "functions", None) or []]
    funcs += [(None, fn) for fn in getattr(model, "functions", ()) or ()]
    for owner, fn in funcs:
        where = f"{owner}::{fn.name}" if owner else str(fn.name)
        for a in getattr(getattr(fn, "args", None), "args", None) or []:
            datatype(a.annotation, f"{where}: parameter '{a.arg}'", (a, fn))
        datatype(getattr(fn, "returns", None), f"{where}: result", fn)
        body(getattr(fn, "body", None), where)
    return list(dict.fromkeys(out))


def refuse(model, refused: Dict[str, str], language: str) -> None:
    """Raise `CompileError` naming every use of a type in ``refused``."""
    found = find(model, refused)
    if not found:
        return
    from ..driver import CompileError
    raise CompileError(
        f"{len(found)} use(s) of a type {language} does not render", found)
