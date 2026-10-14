"""Emission entry point for the ``cpp-progseq`` target.

Assembles the single generated header and copies the core headers alongside.

SECTION ORDER IS A DEPENDENCY ORDER, not a preference. C++ needs a type to be
complete before it is used by value, and each section below names the one above
it: the value unions are the register templates' parameters, the register-group
classes hold registers, the API types appear in operation signatures, the
import interface is what every component holds, and the components hold their
register groups and their children.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from ..progseq_model import CompKind

_log = logging.getLogger("pssc.progseq.cpp")

#: Core headers copied beside the generated one. `pssc_chan.hpp` is copied only
#: when the model has a channel and `pssc_env.hpp` only when something can call
#: `pssc::message` -- an unused header in an output directory reads as a
#: dependency the platform has to satisfy.
_CORE_REG = "pssc_reg.hpp"
_CORE_CHAN = "pssc_chan.hpp"
_CORE_ENV = "pssc_env.hpp"


def model_has_channels(model) -> bool:
    """True if any component in the model declares a `channel_c` field."""
    from ..progseq_model import channel_fields
    return any(channel_fields(c) for c in model.comp_dtypes_root_first)


def model_needs_env(*, match_default: str, message_style: str) -> bool:
    """True if `pssc::message` is reachable -- from a body, or from an
    unmatched `match` lowered to `PSSC_UNREACHABLE`."""
    return message_style != "none" or match_default == "unreachable"


def core_header_names(model, *, match_default: str, message_style: str):
    """The core headers this generation needs, in include order.

    Content-dependent: a model with no channel gets no `pssc_chan.hpp`, because
    an output directory carrying headers nothing includes invites the reader to
    wonder what is missing. `CppProgSeqTarget` does the copying (P4.T5).
    """
    names = [_CORE_REG]
    if model_has_channels(model):
        names.append(_CORE_CHAN)
    if model_needs_env(match_default=match_default, message_style=message_style):
        names.append(_CORE_ENV)
    return names


def generate(model, namespace: str, *,
             dispatch: str = "virtual",
             yield_mode: str = "none", match_default: str = "message",
             message_style: str = "import", class_map=None) -> List[Path]:
    """Generate the C++ programming API for the model's whole component tree.

    The walk, the component order, the register collection and the legality
    gate all happened before this was called, and copying the core headers
    happens after -- see `OpModelTarget`. What is left here is C++.

    Returns the list of written file paths.
    """
    if dispatch == "template":
        raise NotImplementedError(
            "cpp-progseq --dispatch template is not yet implemented; "
            "use --dispatch virtual (the default)")

    from .. import op_model as om
    tree, out_dir = model.tree, model.out_dir
    _log.info("cpp-progseq: root=%s namespace=%s components: %d regular, %d reg-group",
              tree.name, namespace, om.count(tree, CompKind.REGULAR),
              om.count(tree, CompKind.REG_GROUP))

    from .lower_reg_model import lower_value_unions, lower_reg_groups
    from .lower_api_types import lower_api_types
    from .lower_progseq import (class_names, lower_components, emit_import_api,
                                parse_class_map)

    from ...driver import CompileError
    try:
        names = class_names(model, namespace, parse_class_map(class_map))
    except ValueError as e:
        # Two component types with one class name: the model's error.
        raise CompileError(str(e)) from None
    # As in C: no integer type is wider than 64 bits (`int_width`).
    from ..int_width import refuse_too_wide
    refuse_too_wide(model, 64, "C++")
    comps = model.comp_dtypes_root_first
    _log.info("cpp-progseq: classes: %s",
              ", ".join(f"{getattr(c, 'name', '?')}->{names[c]}" for c in comps))

    imports = model.imports
    unions = lower_value_unions(comps)
    groups = lower_reg_groups(comps)
    # The register value structs are emitted by the register model; the API
    # types pass must not emit them a second time.
    api_types = lower_api_types(comps, model.value_structs,
                                model.ctor_names,
                                getattr(model.ctx, "type_map", None))
    import_api = emit_import_api(imports, namespace,
                                 yield_mode=yield_mode)
    components = lower_components(model, names, namespace, imports=imports,
                                  yield_mode=yield_mode,
                                  match_default=match_default,
                                  message_style=message_style)

    has_chan = model_has_channels(model)
    needs_env = model_needs_env(match_default=match_default,
                                message_style=message_style)

    guard = f"{namespace.upper()}_HPP"
    h: List[str] = []
    h.append("// Generated by pssc (cpp-progseq) -- do not edit.")
    h.append(f"// Root component: {tree.name}; dispatch: {dispatch}.")
    h.append("// Value layouts assume little-endian bitfield allocation"
             " (gcc/clang, x86/ARM, LP64/LLP64).")
    h.append(f"#ifndef {guard}")
    h.append(f"#define {guard}")
    h.append("")
    for inc in ("<array>", "<cstddef>", "<cstdint>", "<memory>", "<utility>"):
        h.append(f"#include {inc}")
    h.append(f'#include "{_CORE_REG}"')
    if has_chan:
        h.append(f'#include "{_CORE_CHAN}"')
    if needs_env:
        h.append(f'#include "{_CORE_ENV}"')
    h.append("")
    h.append(f"namespace {namespace} {{")
    h.append("")
    for section in (unions, groups, api_types, import_api, components):
        if not section:
            continue
        h.append(section)
        h.append("")
    h.append(f"}}  // namespace {namespace}")
    h.append("")
    h.append(f"#endif  // {guard}")
    h.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    hpp = out_dir / f"{namespace}.hpp"
    hpp.write_text("\n".join(h))
    written.append(hpp)

    _log.info("cpp-progseq: wrote %d file(s): %s",
              len(written), ", ".join(p.name for p in written))
    return written
