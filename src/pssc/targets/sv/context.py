"""Shared lowering context for PSS-to-SV translation.

Tracks name mangling, type registry, and source location state
across all lowering passes.
"""
from __future__ import annotations

import dataclasses as dc
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# SV keywords that clash with user field/variable names: IEEE 1800 Annex B,
# from the one table (`keywords.py`). A PSS field named one of these is
# prefixed with ``_zsp_``.
from .keywords import SV_KEYWORDS as _SV_KEYWORDS

import zuspec.ir.core as ir


@dc.dataclass
class LoweringContext:
    """State shared across all PSS-to-SV lowering passes."""

    # Maps PSS qualified type name -> mangled SV class name
    sv_name_map: Dict[str, str] = dc.field(default_factory=dict)

    # Maps PSS type name -> SV IR node (for forward-ref resolution)
    sv_node_map: Dict[str, Any] = dc.field(default_factory=dict)

    # Set of names already emitted (to avoid duplicates)
    emitted: Set[str] = dc.field(default_factory=set)

    # The AstToIrContext from the parser (type_map, parent_comp_names, etc.)
    ir_ctx: Optional[Any] = dc.field(default=None)

    # Generation-time warnings: list of (message, location_hint) tuples.
    # Populated by lowering passes for unsupported or degraded constructs.
    warnings: List[Tuple[str, str]] = dc.field(default_factory=list)

    # Pre-computed ActivityPlan objects keyed by action qualified name.
    # Populated by pss_to_sv() before lowering begins when ir_ctx is set.
    activity_plans: Dict[str, Any] = dc.field(default_factory=dict)

    # Set True by lowering when a construct actually emits a zsp_dpi_* solver
    # call, so emit_files only pulls in zsp_dpi_pkg when it is needed (importing
    # it without linking the C impl produces undefined-symbol errors).
    uses_dpi_solver: bool = dc.field(default=False)
    uses_reg_model: bool = dc.field(default=False)

    def import_function_names(self) -> Set[str]:
        """Names of package-scope import functions (target/solve).

        Calls to these inside exec bodies are routed through the import-API
        handle (``comp.import_if.<name>(...)``) by the expression lowering.
        """
        ir_ctx = self.ir_ctx
        funcs = getattr(ir_ctx, "import_functions", None) if ir_ctx else None
        return {f.name for f in funcs} if funcs else set()

    def warn(self, msg: str, location: str = "") -> None:
        """Record a generation-time warning and print it to stderr."""
        import sys
        entry = (msg, location)
        self.warnings.append(entry)
        loc = f" [{location}]" if location else ""
        print(f"zuspec-sv warning{loc}: {msg}", file=sys.stderr)

    def resolve_sv_class_name(self, ref_name: str) -> str:
        """Return the canonical mangled SV class name for a PSS type reference.

        Handles unqualified names by scanning the type_map and sv_name_map,
        preferring the canonical (most-qualified) key that was used to emit
        the class.

        Also handles instance-path references (e.g. ``enc::encode_pipeline``
        where ``enc`` is a component instance of type ``encoder_c``): when the
        exact path is not found in type_map, we fall back to matching on the
        last path segment (``encode_pipeline``) across all registered types.
        """
        if self.ir_ctx is None:
            return self.mangle_name(ref_name)
        type_map = self.ir_ctx.type_map
        # Exact match
        target = type_map.get(ref_name)
        if target is None:
            # Suffix scan for *::ref_name (full ref_name as a suffix)
            for k, v in type_map.items():
                if k.endswith(f"::{ref_name}"):
                    target = v
                    break
        if target is None and "::" in ref_name:
            # ref_name may be an instance-qualified path (e.g. enc::encode_pipeline)
            # where the first segment is an instance name, not a type name.
            # Try matching on the last segment only across all registered types.
            short = ref_name.split("::")[-1]
            candidates = [
                (k, v) for k, v in type_map.items()
                if k == short or k.endswith(f"::{short}")
            ]
            if len(candidates) == 1:
                target = candidates[0][1]
            elif candidates:
                # Pick the most-qualified candidate
                target = max(candidates, key=lambda kv: kv[0].count("::"))[1]
        if target is None:
            return self.mangle_name(ref_name)
        tid = id(target)
        # Return the mangled name that was already recorded in sv_name_map
        for k, m in self.sv_name_map.items():
            if id(type_map.get(k)) == tid:
                return m
        # Not yet mangled: use the most-qualified key
        best = ref_name
        for k, v in type_map.items():
            if id(v) == tid and k.count("::") > best.count("::"):
                best = k
        return self.mangle_name(best)

    def safe_field_name(self, name: str) -> str:
        """Return a SV-safe version of a field/variable name.

        If *name* is an SV reserved keyword, returns ``_zsp_<name>``.
        """
        if name in _SV_KEYWORDS:
            return f"_zsp_{name}"
        return name

    def mangle_name(self, pss_name: str) -> str:
        """Convert a PSS qualified name to a valid SV identifier.

        ``MyComp::MyAction`` -> ``MyComp__MyAction``
        ``pkg::Type``        -> ``pkg__Type``
        """
        if pss_name in self.sv_name_map:
            return self.sv_name_map[pss_name]
        sv_name = re.sub(r'::', '__', pss_name)
        # Replace any remaining non-identifier chars
        sv_name = re.sub(r'[^A-Za-z0-9_]', '_', sv_name)
        self.sv_name_map[pss_name] = sv_name
        return sv_name

    def pss_type_to_sv_type_str(self, dtype: ir.DataType) -> str:
        """Map a PSS IR DataType to an SV type string.

        Returns a string suitable for use as an SV field type declaration.
        """
        if isinstance(dtype, ir.DataTypeInt):
            if dtype.bits == 1:
                return "bit"
            if dtype.signed:
                if dtype.bits == 32:
                    return "int"
                return f"int signed [{dtype.bits - 1}:0]"
            return f"bit [{dtype.bits - 1}:0]"

        if isinstance(dtype, ir.DataTypeEnum):
            return self.mangle_name(dtype.name) if dtype.name else "int"

        if isinstance(dtype, ir.DataTypeString):
            return "string"

        if isinstance(dtype, ir.DataTypeChandle):
            return "chandle"

        if isinstance(dtype, ir.DataTypeBool) if hasattr(ir, 'DataTypeBool') else False:
            return "bit"

        if isinstance(dtype, (ir.DataTypeList, ir.DataTypeArray,
                              ir.DataTypeMap, ir.DataTypeSet)):
            # A type string goes BEFORE the declared name, and every SV form
            # of a collection puts its dimension AFTER it (`bit [7:0] x[16]`,
            # `x[$]`, `x[string]`). `bit [7:0] [16] x;` is not SV. Refused
            # rather than emitted (ruling R9); op-model-sv renders arrays.
            from ...driver import CompileError
            kind = type(dtype).__name__[len("DataType"):].lower()
            raise CompileError(
                f"this SV target cannot declare a {kind} (only op-model-sv "
                f"renders collections); see "
                f"docs/design/construct-test-audit/00-plan.md, ruling R9")

        if isinstance(dtype, ir.DataTypeRef):
            # Resolve through sv_name_map to get the canonical mangled class name.
            # DataTypeRef fields may reference action or component types whose
            # mangled names were determined during the forward-decl pass.
            # Also handles instance-path references (e.g. "tx::send_pkt" where "tx"
            # is a component instance of type "tx_c") by delegating to resolve_sv_class_name
            # when the simple lookup fails.
            if self.ir_ctx is not None:
                target = self.ir_ctx.type_map.get(dtype.ref_name)
                if target is None:
                    for k, v in self.ir_ctx.type_map.items():
                        if k.endswith(f"::{dtype.ref_name}"):
                            target = v
                            break
                if target is not None:
                    tid = id(target)
                    for k, m in self.sv_name_map.items():
                        if id(self.ir_ctx.type_map.get(k)) == tid:
                            return m
                    best = dtype.ref_name
                    for k, v in self.ir_ctx.type_map.items():
                        if id(v) == tid and k.count("::") > best.count("::"):
                            best = k
                    return self.mangle_name(best)
                # Fall back to instance-path resolution (same as resolve_sv_class_name)
                return self.resolve_sv_class_name(dtype.ref_name)
            return self.mangle_name(dtype.ref_name)

        if isinstance(dtype, (ir.DataTypeStruct, ir.DataTypeClass, ir.DataTypeComponent)):
            # Same canonical lookup for inline type objects
            if self.ir_ctx is not None and dtype.name:
                target = self.ir_ctx.type_map.get(dtype.name)
                if target is not None:
                    for k, m in self.sv_name_map.items():
                        if id(self.ir_ctx.type_map.get(k)) == id(target):
                            return m
            return self.mangle_name(dtype.name) if dtype.name else "int"

        # Fallback
        return "int"
