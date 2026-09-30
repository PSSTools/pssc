"""Activity analysis pass (Phase 4): schedule block staging and pipeline detection.

Combines Phase E (elaboration) and Phase S (structural solve) into a
generation-time analysis that produces an ActivityPlan for each compound
action's activity block.

An ActivityPlan carries:
  - All action instances (explicit + inferred via ICL lookup)
  - Topological staging (which actions can run concurrently)
  - Flow-object bindings
  - Per-pipeline-chain compiled SolveProblem buffers (base64-encoded)
  - Var ID maps for each chain (for pin_var_h calls)

The plan is consumed by lower_activities.py to emit the joint-chain
DPI solve pattern described in PSS_TO_SV_IMPLEMENTATION_DESIGN.md §5.3-5.4.
"""
from __future__ import annotations

import base64
import dataclasses as dc
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import zuspec.ir.core as ir
from zuspec.ir.core.fields import FieldKind

from .analyze_flow import (
    ActivityFlowInfo,
    FlowBindingInfo,
    analyze_flow,
    get_flow_input_fields,
    get_flow_output_fields,
)
from .classify_constraints import determine_solve_mode, SolveMode

if TYPE_CHECKING:
    from .context import LoweringContext


# ------------------------------------------------------------------ #
# Data structures                                                       #
# ------------------------------------------------------------------ #

@dc.dataclass
class PipelineChain:
    """A sequence of pipeline actions that must be solved jointly.

    Each step in the chain is an action variable name.  The chain
    is linked by stream flow-object bindings (producer.out -> consumer.in).

    The compiled SolveProblem captures all per-action constraints and
    binding equalities.  Exit constraints (from the chain's consumer)
    are also compiled in so that back-propagation is handled by the
    solver.
    """
    steps: List[str]                   # SV variable names in execution order
    action_types: List[str]            # qualified PSS type name per step
    field_names: List[str]             # flow-object sub-field names (shared)
    problem_b64: str                   # base64-encoded SolveProblem
    var_id_map: Dict[str, int]         # "step.in.field" / "step.out.field" -> var_id
    entry_var: str                     # step label of the chain's first action
    entry_field: str                   # input field name on the first action


@dc.dataclass
class ActivityPlan:
    """Fully resolved execution plan for one compound action's activity.

    Produced by analyze_activity() and consumed by lower_activities.py.
    """
    flow_info: ActivityFlowInfo
    chains: List[PipelineChain]        # DPI joint-solve chains
    # Solve mode per action variable: var_name -> SolveMode
    solve_modes: Dict[str, SolveMode]
    # Whether any action in this activity requires DPI chain solving
    needs_dpi_chain: bool


# ------------------------------------------------------------------ #
# Main analysis entry point                                            #
# ------------------------------------------------------------------ #

def analyze_activity(
    ctx: 'LoweringContext',
    activity: ir.ActivitySequenceBlock,
) -> ActivityPlan:
    """Analyze one activity block and produce an ActivityPlan.

    Args:
        ctx: Lowering context with ir_ctx populated.
        activity: The activity block to analyze.

    Returns:
        ActivityPlan with flow info, chains, and solve modes.
    """
    # Phase E: flow-object binding analysis
    flow_info = analyze_flow(ctx, activity)

    # Phase E: classify constraints and determine solve modes
    solve_modes: Dict[str, SolveMode] = {}
    for trav in flow_info.traversals:
        has_consumer = trav.var_name in flow_info.consumer_bindings
        mode = determine_solve_mode(ctx, trav.action_type, has_consumer)
        solve_modes[trav.var_name] = mode

    # Phase S: identify pipeline chains (stream-linked action sequences)
    chains = _build_chains(ctx, flow_info, solve_modes)

    needs_dpi = any(c.problem_b64 for c in chains)

    return ActivityPlan(
        flow_info=flow_info,
        chains=chains,
        solve_modes=solve_modes,
        needs_dpi_chain=needs_dpi,
    )


# ------------------------------------------------------------------ #
# Chain detection and compilation                                       #
# ------------------------------------------------------------------ #

def _build_chains(
    ctx: 'LoweringContext',
    flow_info: ActivityFlowInfo,
    solve_modes: Dict[str, SolveMode],
) -> List[PipelineChain]:
    """Identify stream-linked action sequences that need joint DPI solving.

    A chain is a sequence of actions where each action's stream output
    is bound to the next action's stream input.  The chain needs a joint
    DPI solve when any action in it is classified DPI_JOINT_CHAIN.
    """
    # Build successor graph for stream bindings
    # var_name -> list of (consumer_var, consumer_field, producer_field)
    stream_successors: Dict[str, List[Tuple[str, str, str]]] = {}
    stream_predecessors: Dict[str, str] = {}   # consumer_var -> producer_var

    for b in flow_info.bindings:
        if b.flow_kind == "stream":
            stream_successors.setdefault(b.producer_var, []).append(
                (b.consumer_var, b.consumer_field, b.producer_field)
            )
            stream_predecessors[b.consumer_var] = b.producer_var

    # Find chain roots (actions that are stream producers but not consumers)
    chain_roots = [
        var for var in stream_successors
        if var not in stream_predecessors
    ]

    chains: List[PipelineChain] = []
    for root in chain_roots:
        chain_steps, chain_types, entry_field = _walk_chain(
            root, stream_successors, flow_info, ctx
        )
        if len(chain_steps) < 2:
            continue  # Not a chain

        # Only build a DPI problem if any step needs it
        needs_dpi = any(
            solve_modes.get(v, SolveMode.SV_NATIVE) == SolveMode.DPI_JOINT_CHAIN
            for v in chain_steps
        )

        if needs_dpi:
            problem_b64, var_id_map, field_names = _compile_chain(
                ctx, chain_steps, chain_types, flow_info
            )
        else:
            problem_b64 = ""
            var_id_map = {}
            field_names = []

        chains.append(PipelineChain(
            steps=chain_steps,
            action_types=chain_types,
            field_names=field_names,
            problem_b64=problem_b64,
            var_id_map=var_id_map,
            entry_var=root,
            entry_field=entry_field,
        ))

    return chains


def _walk_chain(
    root: str,
    successors: Dict[str, List[Tuple[str, str, str]]],
    flow_info: ActivityFlowInfo,
    ctx: 'LoweringContext',
) -> Tuple[List[str], List[str], str]:
    """Walk the chain from root, returning (steps, types, entry_input_field)."""
    steps = [root]
    types = [_get_action_type(root, flow_info)]
    entry_field = ""

    # Find what input field the root has for stream (it's the chain's consumer output)
    # The root is a producer of a stream, so check if it also consumes one
    for b in flow_info.consumer_bindings.get(root, []):
        if b.flow_kind == "stream":
            entry_field = b.consumer_field
            break

    current = root
    visited = {root}
    while current in successors:
        next_steps = successors[current]
        if len(next_steps) != 1:
            break  # branching chain -- stop here
        next_var, _, _ = next_steps[0]
        if next_var in visited:
            break
        steps.append(next_var)
        types.append(_get_action_type(next_var, flow_info))
        visited.add(next_var)
        current = next_var

    return steps, types, entry_field


def _get_action_type(var_name: str, flow_info: ActivityFlowInfo) -> str:
    for trav in flow_info.traversals:
        if trav.var_name == var_name:
            return trav.action_type
    return ""


def _compile_chain(
    ctx: 'LoweringContext',
    steps: List[str],
    action_types: List[str],
    flow_info: ActivityFlowInfo,
) -> Tuple[str, Dict[str, int], List[str]]:
    """Compile the joint SolveProblem for a pipeline chain.

    Returns (problem_b64, var_id_map, field_names).
    Returns ("", {}, []) if compilation fails.
    """
    try:
        from zuspec.solver.chain_problem_builder import ChainProblemBuilder, ChainField
        from zuspec.solver.lib import _load_lib

        lib = _load_lib()
        if lib is None:
            return ("", {}, [])

        # Collect stream fields from the first action's stream output
        # (all actions in the chain share the same stream type and fields)
        first_type = action_types[0] if action_types else ""
        field_names = _get_stream_field_names(ctx, first_type)
        fields = _get_stream_fields(ctx, first_type)
        if not fields:
            return ("", {}, [])

        builder = ChainProblemBuilder()
        for step_label, action_type in zip(steps, action_types):
            step_fields = _get_stream_fields(ctx, action_type) or fields
            builder.add_action(step_label, step_fields, list(step_fields))

        # Add stream bindings between adjacent steps
        for i in range(len(steps) - 1):
            builder.add_binding(steps[i], "out", steps[i + 1], "in")

        # Compile
        problem_bytes, var_id_map = builder.build(lib=lib)
        problem_b64 = base64.b64encode(problem_bytes).decode("ascii")
        return (problem_b64, var_id_map, field_names)

    except Exception:
        return ("", {}, [])


def _get_stream_fields(
    ctx: 'LoweringContext',
    action_type_name: str,
) -> List['ChainField']:
    """Get ChainField descriptors for the stream output fields of an action type."""
    try:
        from zuspec.solver.chain_problem_builder import ChainField
    except ImportError:
        return []

    if not ctx.ir_ctx or not action_type_name:
        return []

    # Find the action type in the IR
    dtype = None
    for qname, dt in ctx.ir_ctx.type_map.items():
        if qname == action_type_name or qname.endswith(f"::{action_type_name}"):
            dtype = dt
            break
    if dtype is None:
        return []

    # Find the stream output field
    for field in getattr(dtype, 'fields', []):
        if field.kind != FieldKind.Output:
            continue
        ft = field.datatype
        # Resolve the flow-object type
        flow_dtype = None
        if hasattr(ft, 'flow_kind') and ft.flow_kind is ir.FlowKind.STREAM:
            flow_dtype = ft
        elif isinstance(ft, ir.DataTypeRef):
            for rname, rdt in ctx.ir_ctx.type_map.items():
                if rname == ft.ref_name or rname.endswith(f"::{ft.ref_name}"):
                    if hasattr(rdt, 'flow_kind') and rdt.flow_kind is ir.FlowKind.STREAM:
                        flow_dtype = rdt
                    break

        if flow_dtype is not None:
            result = []
            for sf in getattr(flow_dtype, 'fields', []):
                if sf.kind == FieldKind.Field:
                    width, lo, hi = _field_domain(sf)
                    result.append(ChainField(
                        name=sf.name,
                        width=width,
                        is_signed=False,
                        lo=lo,
                        hi=hi,
                    ))
            if result:
                return result

    return []


def _get_stream_field_names(
    ctx: 'LoweringContext',
    action_type_name: str,
) -> List[str]:
    fields = _get_stream_fields(ctx, action_type_name)
    return [f.name for f in fields]


def _field_domain(field: ir.Field) -> Tuple[int, int, int]:
    """Return (width, lo, hi) for a field based on its datatype."""
    dt = field.datatype
    if hasattr(dt, 'bits'):
        width = dt.bits
        if hasattr(dt, 'signed') and dt.signed:
            lo = -(1 << (width - 1))
            hi = (1 << (width - 1)) - 1
        else:
            lo = 0
            hi = (1 << width) - 1
        return width, lo, hi
    # Fallback for enums and other types
    return 32, 0, 0xFF_FF_FF_FF
