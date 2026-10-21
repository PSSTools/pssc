"""The operation model as JSON: what was generated, without reading it.

`--emit-manifest FILE` writes this beside the generated code. It answers "what
operations and registers does this API have, and where does each register sit"
for a consumer that is not a compiler -- a build system deciding what to
rebuild, a test generator enumerating operations, a documentation pass, a
register-map cross-check against an RDL source, or a person.

WHY IT IS NOT DERIVED FROM THE OUTPUT. Every one of those consumers otherwise
parses generated C, and a parser for generated C is a second implementation of
the generator's naming rules that nobody updates. The manifest is written from
the `OpModel` -- the same object the emitters render -- so it cannot describe a
different API from the one that was emitted.

VERSIONED FROM DAY ONE. :data:`SCHEMA` is a name and an integer, both in the
document. A consumer that checks it gets a comprehensible failure when the
shape changes; one that does not at least has the version in the file it kept.
The rule for bumping it is in the constant's own comment.

Plan: P8.T2.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .progseq_model import _dt_name
from .reg_layout import collect_accessors

__all__ = ["SCHEMA", "VERSION", "build", "write"]

#: The document's own name, so a consumer can tell this file from another JSON.
SCHEMA = "pssc.op-model-manifest"

#: Bumped when a consumer that read the previous version would be WRONG --
#: a field removed, renamed, or given a different meaning. Adding a field is
#: not a bump: a reader that ignores it is still correct.
VERSION = 1


def _type_name(dtype) -> str:
    """A PSS type, as a short string. Not a language's spelling of it.

    The manifest describes the MODEL, so `bit[32]` stays `bit[32]` rather than
    becoming `uint32_t` in one manifest and `logic [31:0]` in another. A
    consumer wanting a C type asks the C target.

    One name a reader will look for and not find: `addr_handle_t` reports as
    `chandle`, because it is `typedef chandle addr_handle_t` and the typedef
    name does not reach the IR. Reporting the typedef would mean this file
    inventing a name the model no longer carries.
    """
    if dtype is None:
        return "void"
    cn = _dt_name(dtype)
    if cn == "DataTypeInt":
        bits = int(getattr(dtype, "bits", 32) or 32)
        signed = bool(getattr(dtype, "signed", False))
        return f"int{'' if bits == 32 else f'[{bits}]'}" if signed \
            else f"bit[{bits}]"
    if cn == "DataTypeBool":
        return "bool"
    if cn == "DataTypeChandle":
        return "chandle"
    if cn in ("DataTypeStruct", "DataTypeEnum", "DataTypeComponent"):
        return (getattr(dtype, "name", "") or cn).split("::")[-1]
    if cn == "DataTypeArray":
        elem = _type_name(getattr(dtype, "element_type", None))
        n = getattr(dtype, "size", None)
        return f"{elem}[{n}]" if n is not None and int(n) >= 0 else f"{elem}[]"
    if cn == "DataTypeChannel":
        return f"channel_c<{_type_name(getattr(dtype, 'element_type', None))}>"
    return cn


def _params(fn) -> List[Dict[str, str]]:
    return [{"name": a.arg, "type": _type_name(a.annotation)}
            for a in fn.args.args]


def _annotation_value(v):
    if isinstance(v, (list, tuple)):
        return [_annotation_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _annotation_value(x) for k, x in v.items()}
    if hasattr(v, "enum") and hasattr(v, "item"):          # AnnotationEnum
        return {"enum": v.enum, "item": v.item, "value": v.value}
    return v


def _annotations(node) -> List[Dict[str, Any]]:
    """The source annotations (LRM 7.13) on *node*, as the front end resolved
    and folded them: `type` is the qualified annotation type, `values` every
    field's value, defaults included. An enum item is
    `{"enum", "item", "value"}`."""
    return [{"type": a.type_qname,
             "values": {k: _annotation_value(v) for k, v in a.values.items()}}
            for a in getattr(node, "annotations", None) or []]


def _function(fn) -> Dict[str, Any]:
    return {
        "name": fn.name,
        "returns": _type_name(getattr(fn, "returns", None)),
        "params": _params(fn),
        "doc": getattr(fn, "doc", None) or "",
        "annotations": _annotations(fn),
    }


def _registers(comp) -> List[Dict[str, Any]]:
    """Every register reachable from *comp*, with its address arithmetic.

    `offset` and `strides` are exactly what the generated accessors fold in, and
    they come from the same walk (`reg_layout`), so a consumer comparing the
    manifest against a register map is comparing against what was emitted --
    not against a second computation that could agree while the output does not.
    """
    out = []
    for acc in collect_accessors(comp):
        out.append({
            "path": list(acc.path),
            "offset": acc.const_off,
            "strides": list(acc.strides),
            "bits": acc.value_bits,
            "access_width": acc.prim_bits,
            "access": acc.access,
            "value_struct": (
                (getattr(acc.value_struct, "name", "") or "").split("::")[-1]
                if acc.is_struct else None),
        })
    return out


def _value_structs(model) -> List[Dict[str, Any]]:
    from ..reg_field_resolve import struct_layout

    out = []
    for s in model.value_structs:
        slices = struct_layout(s)
        out.append({
            "name": (getattr(s, "name", "") or "").split("::")[-1],
            "bits": sum(sl.width for sl in slices),
            "fields": [{"name": sl.name, "lsb": sl.lsb, "width": sl.width}
                       for sl in slices],
        })
    return out


def _enums(model, symbol) -> List[Dict[str, Any]]:
    """The enums the API uses, with each item's value and, where the target
    names one, its generated spelling: C and SV prefix an item
    (`WB_DMA_STATUS_WB_DMA_DONE`), C++ and Python scope it by its enum, and a
    consumer must not have to know which."""
    from .sv.lower_api_types import collect_api_types

    enums, _ = collect_api_types(
        model.comp_dtypes, model.value_structs, model.ctor_names,
        [fn for e in model.entries for fn in e.functions]
        + list(getattr(model, "functions", ()) or ()),
        getattr(getattr(model, "ctx", None), "type_map", None) or {})
    out = []
    for e in enums:
        items = []
        for k, v in e.items.items():
            item = {"name": k, "value": int(v)}
            sym = symbol(e, k) if symbol is not None else None
            if sym is not None:
                item["symbol"] = sym
            items.append(item)
        out.append({"name": (getattr(e, "name", "") or "").split("::")[-1],
                    "items": items,
                    "annotations": _annotations(e)})
    return out


def _component(node, model) -> Dict[str, Any]:
    comp = node.dtype
    ctor = model.ctor(comp)
    return {
        "name": (getattr(comp, "name", "") or "").split("::")[-1],
        "is_root": model.is_root(comp),
        "doc": getattr(comp, "doc", None) or "",
        "annotations": _annotations(comp),
        "constructor": _function(ctor) if ctor is not None else None,
        "operations": [_function(fn) for fn in model.operations(comp)],
        "entries": [{"name": e.name, "action": e.action}
                    for e in model.entries_of(comp)],
        "sub_components": [
            {"name": s.name,
             "type": (getattr(s.dtype, "name", "") or "").split("::")[-1],
             "count": s.size}
            for s in model.sub_components(comp)],
        "channels": [{"name": f.name,
                      "element": _type_name(getattr(f.datatype, "element_type",
                                                    None)),
                      "depth": int(getattr(f.datatype, "depth", 1) or 1)}
                     for f in model.channels(comp)],
        "registers": _registers(comp),
        "groups": _groups(comp, model),
    }


def _groups(comp, model) -> List[Dict[str, Any]]:
    """Each register group of *comp* and the constructor parameter that gives
    its handle (`group_binding`): `param` is that parameter's name when the
    handle is the parameter alone, else `null` (an expression, a binding
    outside the constructor, or one from an enclosing component), and
    `implicit` says the generator bound it, the constructor having bound no
    group (the single-group convention)."""
    b = model.binding(comp) if hasattr(model, "binding") else None
    if b is None:
        return []
    out = []
    for g in b.groups:
        out.append({"name": g,
                    "param": b.params.get((g,)),
                    "implicit": b.implicit is not None and b.implicit[0] == g})
    return out


def build(model, *, target: str, settings: Optional[Dict[str, Any]] = None,
          files: Sequence = (), runtime_files: Sequence[str] = (),
          include_files: Sequence[str] = (),
          enum_symbol=None) -> Dict[str, Any]:
    """The manifest document for one generation.

    ``files`` is the path list the target returned, IN ORDER -- that order is a
    compilation order for some targets, so it is recorded rather than sorted.
    Each is labelled `generated` or `runtime`; one another file includes,
    and which is therefore no compilation unit, also carries `"include": true`
    (``include_files``, the target's answer). ``enum_symbol(enum, item)`` is
    how the target spells an enum item (`OpModelTarget.enum_item_symbol`).

    The labels come from ``runtime_files``, which is the target's own
    `core_file_names()` -- the list it copied FROM. Deciding by extension would
    be wrong (the C target copies `pssc_mem.h` beside a generated `wb_dma.h`),
    and deciding by comparing bytes against the shipped copy would answer a
    different question: whether the file happens to match today, rather than
    where it came from.
    """
    from .. import __version__

    return {
        "schema": SCHEMA,
        "version": VERSION,
        "pssc_version": __version__,
        "target": target,
        "root": (getattr(model.root, "name", "") or "").split("::")[-1],
        "ctor_names": sorted(model.ctor_names),
        # The options that change the generated API's SHAPE, which is what a
        # consumer holding a stale manifest needs to notice. What counts is the
        # target's own answer (`OpModelTarget.abi_settings`) -- a link style or
        # a lifecycle is ABI, a comment style is not.
        "settings": dict(settings or {}),
        "imports": [_function(fn) for fn in
                    (model.imports[k] for k in sorted(model.imports))],
        # The root's exported functions: with the exported actions, what a
        # target that takes its API from exports puts on it.
        "exports": [_function(fn) for fn in getattr(model, "exports", ())],
        "value_structs": _value_structs(model),
        "enums": _enums(model, enum_symbol),
        "components": [_component(n, model) for n in model.components],
        "files": [dict({"name": Path(p).name,
                        "role": ("runtime" if Path(p).name in set(runtime_files)
                                 else "generated")},
                       **({"include": True}
                          if Path(p).name in set(include_files) else {}))
                  for p in files],
    }


def write(path, document: Dict[str, Any]) -> Path:
    """Write *document* to *path*, deterministically.

    `sort_keys` on the mappings whose key order is not itself meaningful, a
    fixed indent, and a trailing newline: a manifest that differs run to run
    defeats every incremental build that reads it, which is most of the reason
    to have one.
    """
    p = Path(str(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return p
