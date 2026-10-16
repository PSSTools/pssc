"""Parse PSS and look at the IR ast2ir makes of it, compactly.

`body(src)` translates a model and returns the statements of one function;
`shape(node)` renders an IR node as a short string --
``For(i, self.n, [Assign(x, i)])`` -- so a test states the whole structure
it expects in one line. A required field ast2ir leaves None shows up as
``None``; an OPTIONAL one (a field whose default is None, such as a
`return`'s value) is left out when None, so a test about a dropped optional
field checks that field directly.

Rendering rules: the class name loses its ``Stmt``/``Expr``/``DataType``
prefix; a local is its name; ``self.x`` is a field or parameter; a constant
is its value, with ``'<width><s|u>`` when the literal carries a type (``'u``
alone: the default width, unsigned, as ``0x10`` is) and
``<name>=`` when it is a named constant or enum item the linker resolved; an
enum (``BinOp.Add``) is its member name; fields that are bookkeeping (docs,
comments, locations, provenance, pragmas) or empty are left out.
"""
from __future__ import annotations

import dataclasses
import enum
from pathlib import Path
from typing import Any, List

import zuspec.ir.core as ir

from pssc import driver

_SKIP = {"doc", "doc_trailing", "provenance", "loc", "comment",
         "comment_trailing", "pragmas", "ir_type", "py_type", "ref",
         "size_ref"}
_PREFIXES = ("Stmt", "Expr", "DataType", "TypeExpr")


def translate(tmp_path: Path, src: str, prelude: str = "import std_pkg::*;\n"):
    """The ast2ir context for ``src`` (errors are on ``ctx.errors``)."""
    p = tmp_path / "m.pss"
    p.write_text(prelude + src)
    return driver.translate([str(p)])


def function(ctx, name: str, comp: str = "pss_top"):
    return next(f for f in ctx.type_map[comp].functions if f.name == name)


def body(tmp_path: Path, src: str, fn: str = "f", comp: str = "pss_top"
         ) -> List[Any]:
    """The statements of ``comp::fn``; fails if translation reported errors."""
    ctx = translate(tmp_path, src)
    assert ctx.errors == [], ctx.errors
    return function(ctx, fn, comp).body


def in_function(decls: str, stmts: str, params: str = "") -> str:
    """A model whose ``pss_top::f(params)`` holds ``stmts``."""
    return (f"component pss_top {{\n{decls}\n"
            f"  target function void f({params}) {{\n{stmts}\n  }}\n}}\n")


def _name(cls) -> str:
    n = cls.__name__
    for p in _PREFIXES:
        if n.startswith(p) and len(n) > len(p):
            return n[len(p):]
    return n


def shape(node: Any) -> str:
    if node is None:
        return "None"
    if isinstance(node, enum.Enum):
        return node.name
    if isinstance(node, (list, tuple)):
        return "[" + ", ".join(shape(x) for x in node) + "]"
    if isinstance(node, ir.ExprRefLocal):
        return node.name
    if isinstance(node, ir.TypeExprRefSelf):
        return "self"
    if isinstance(node, ir.ExprAttribute):
        return f"{shape(node.value)}.{node.attr}"
    if isinstance(node, ir.ExprConstant):
        v = repr(node.value)
        if node.width:
            v += f"'{node.width}{'s' if node.signed else 'u'}"
        elif node.signed is not None:
            v += "'s" if node.signed else "'u"      # default width, typed
        if node.ref:
            v = f"{node.ref}={v}"
        return v
    if isinstance(node, ir.DataTypeInt):
        return f"{'int' if node.signed else 'bit'}[{node.bits}]"
    if not dataclasses.is_dataclass(node):
        return repr(node)
    parts = []
    for f in dataclasses.fields(node):
        if f.name in _SKIP:
            continue
        v = getattr(node, f.name)
        if v is None and f.default is None:
            continue
        if isinstance(v, (list, tuple, dict)) and not v:
            continue
        parts.append(shape(v))
    return f"{_name(type(node))}({', '.join(parts)})"
