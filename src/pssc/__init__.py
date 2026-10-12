"""pssc — the PSS compiler.

Public API re-exported here; the front end lives in :mod:`pssc.frontend`,
AST->IR in :mod:`pssc.ast2ir`, IR->Python in :mod:`pssc.runtime`, and code
generation under :mod:`pssc.targets`.
"""
import os
import shutil
from pathlib import Path
from typing import List, Optional, Union

from .ast2ir import AstToIrTranslator, AstToIrContext
from .runtime import IrToRuntimeBuilder, ClassRegistry
from .frontend import (
    Parser,
    ParseException,
)
from .__version__ import version as __version__

class PssTranslationError(Exception):
    """Raised when PSS source cannot be fully translated to IR."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  ".join(errors)
        super().__init__(f"PSS IR translation failed with {len(errors)} error(s):\n  {joined}")


def _configure_registry(reg: ClassRegistry, ctx: AstToIrContext,
                        export_actions: Optional[List[str]]) -> ClassRegistry:
    """Attach the export-api projection config (imports + export actions).

    Enables the ``registry.create(imports) -> ExportApi`` workflow.  Import specs
    come from the PSS ``import target``/``import solve`` declarations; the export
    set is the explicit list (qualified) or the auto-detected single root.  Both
    are advisory — the registry stays a plain class container too.
    """
    from zuspec.be.py import ImportSpec

    import_specs = {
        f.name: ImportSpec(
            name=f.name,
            is_target=bool(getattr(f, 'is_target', False)),
            is_solve=bool(getattr(f, 'is_solve', False)),
        )
        for f in getattr(ctx, 'import_functions', [])
    }

    if export_actions:
        selected = [_resolve_qualified_name(ctx, a) for a in export_actions]
    else:
        try:
            comp_name, short = _auto_detect_roots(ctx)
            selected = [f"{comp_name}::{short}"]
        except ValueError:
            selected = []   # ambiguous: expose all actions at create() time

    reg.configure_exports(export_actions=selected, import_specs=import_specs)
    return reg


def load_pss(pss_text: str, *, export_actions: Optional[List[str]] = None) -> ClassRegistry:
    """Parse PSS source text and return a registry of randomizable Python classes.

    Each PSS ``struct`` becomes a plain Python dataclass whose fields can be
    randomized with ``zuspec.dataclasses.randomize()``.  The returned registry is
    also the export-api factory — call :meth:`ClassRegistry.create` to get a
    runnable, import-wired instance and invoke its exported actions::

        registry = load_pss(model_text)
        ep = registry.create(MyImports(), seed=1)   # fresh run, wired imports
        await ep.Entry()                            # runs the action lifecycle

    Example (randomize a struct directly)::

        from pssc import load_pss
        from zuspec.dataclasses import randomize

        ns = load_pss(\"\"\"
            struct Packet {
                rand bit[8] addr;
                constraint addr % 4 == 0;
            }
        \"\"\")
        pkt = ns.Packet()
        randomize(pkt, seed=42)
        assert pkt.addr % 4 == 0
    """
    parser = Parser()
    parser.parses([('inline.pss', pss_text)])
    root = parser.link()
    ctx = AstToIrTranslator().translate(root)
    if ctx.errors:
        raise PssTranslationError(ctx.errors)
    return _configure_registry(IrToRuntimeBuilder(ctx).build(), ctx, export_actions)


def load_pss_files(paths: List[Union[str, os.PathLike]], *,
                   export_actions: Optional[List[str]] = None) -> ClassRegistry:
    """Parse one or more ``.pss`` files and return a registry of Python classes.

    Files are parsed together so they can reference each other's types.  As with
    :func:`load_pss`, the registry is the export-api factory
    (``registry.create(imports)``).

    Example::

        from pssc import load_pss_files
        from zuspec.dataclasses import randomize

        ns = load_pss_files(['bus.pss', 'cpu.pss'])
        cmd = ns.WriteCmd()
        randomize(cmd, seed=1)
    """
    str_paths = [str(p) for p in paths]
    parser = Parser()
    parser.parse(str_paths)
    root = parser.link()
    ctx = AstToIrTranslator().translate(root)
    if ctx.errors:
        raise PssTranslationError(ctx.errors)
    return _configure_registry(IrToRuntimeBuilder(ctx).build(), ctx, export_actions)


def get_deps():
    return ["pssparser"]

def get_libs():
    return []

def get_libdirs():
    return []

def get_incdirs():
    return []


# ---------------------------------------------------------------------------
# PSS-to-SystemVerilog generation API
# ---------------------------------------------------------------------------

from pathlib import Path
import shutil


def _get_runtime_lib_path() -> Path:
    """Return the path to the bundled zsp_rt_pkg.sv runtime library."""
    return Path(__file__).parent / "share" / "sv" / "zsp_rt_pkg.sv"


def generate_sv(pss_text: str, output_dir: str, **options) -> List[Path]:
    """Parse PSS source text and generate SystemVerilog files."""
    parser = Parser()
    parser.parses([('inline.pss', pss_text)])
    root = parser.link()
    ir_ctx = AstToIrTranslator().translate(root)
    if ir_ctx.errors:
        raise PssTranslationError(ir_ctx.errors)
    return _generate_sv_from_ctx(ir_ctx, output_dir, **options)


def generate_sv_files(paths: List[Union[str, os.PathLike]], output_dir: str, **options) -> List[Path]:
    """Parse PSS source files and generate SystemVerilog files."""
    str_paths = [str(p) for p in paths]
    parser = Parser()
    parser.parse(str_paths)
    root = parser.link()
    ir_ctx = AstToIrTranslator().translate(root)
    if ir_ctx.errors:
        raise PssTranslationError(ir_ctx.errors)
    return _generate_sv_from_ctx(ir_ctx, output_dir, **options)


def _resolve_qualified_name(ir_ctx: AstToIrContext, raw_name: str,
                            comp_hint: Optional[str] = None) -> str:
    """Resolve a raw PSS type name to its qualified ``type_map`` key.

    Accepts an unqualified name (``Entry``), a component-qualified name
    (``pss_top::Entry``), or an already-final name.  Returns the matching
    ``type_map`` key, or the input unchanged if no match is found (so callers
    that already pass a final/mangled name still work).
    """
    if raw_name in ir_ctx.type_map:
        return raw_name
    if comp_hint:
        candidate = f"{comp_hint}::{raw_name}"
        if candidate in ir_ctx.type_map:
            return candidate
    for key in ir_ctx.type_map:
        if key == raw_name or key.endswith(f"::{raw_name}"):
            return key
    return raw_name


def _auto_detect_roots(ir_ctx: AstToIrContext):
    """Best-effort detection of the root component and root action.

    Returns ``(comp_name, action_name)`` as PSS names, or ``(None, None)`` if no
    actions exist.  Raises ``ValueError`` when multiple equally-plausible roots
    remain (the caller may catch this and fall back to no export API).
    """
    import zuspec.ir.core as _ir
    from .targets.sv.pss_to_sv import _is_stdlib

    actions = [
        name for name, dt in ir_ctx.type_map.items()
        if isinstance(dt, _ir.DataTypeClass) and not _is_stdlib(name)
    ]
    if not actions:
        return None, None

    short = lambda q: q.rsplit("::", 1)[-1]
    action_shorts = {short(a) for a in actions}
    referenced: set = set()

    def _walk_activity(node, seen):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        at = getattr(node, "action_type", None)
        if isinstance(at, str):
            referenced.add(short(at))
        for attr in ("stmts", "body", "branches"):
            for child in (getattr(node, attr, None) or []):
                _walk_activity(child, seen)

    for a_qname in actions:
        dt = ir_ctx.type_map[a_qname]
        for f in (getattr(dt, "fields", None) or []):
            ref = (getattr(getattr(f, "datatype", None), "ref_name", None)
                   or getattr(getattr(f, "datatype", None), "name", None))
            if ref and short(ref) in action_shorts and short(ref) != short(a_qname):
                referenced.add(short(ref))
        _walk_activity(getattr(dt, "activity_ir", None), set())

    roots = [a for a in actions if short(a) not in referenced] or actions

    def _comp_of(act_qname):
        comp = ir_ctx.parent_comp_names.get(act_qname)
        if comp is None and "::" in act_qname:
            comp = act_qname.rsplit("::", 1)[0]
        return comp

    pss_top_roots = [a for a in roots if _comp_of(a) == "pss_top"]
    candidates = pss_top_roots if len(pss_top_roots) == 1 else roots

    if len(candidates) != 1:
        raise ValueError(
            "Cannot auto-detect a single root action; candidates: "
            f"{sorted(candidates)}. Pass root_action_type/export_actions explicitly."
        )

    act_qname = candidates[0]
    comp_name = _comp_of(act_qname) or "pss_top"
    return comp_name, short(act_qname)


def _generate_sv_from_ctx(ir_ctx: AstToIrContext, output_dir: str, **options) -> List[Path]:
    """Internal: lower IR context and write SV output files.

    ``projection`` selects the package shape:
      * ``'oo_api'`` (default) -- interface-class export API + factory + impl,
        driven by an external testbench (``pss_top::type_id().create(imp)``).
      * ``'harness'`` -- legacy standalone ``zsp_test_top`` module.
    """
    from .targets.sv.pss_to_sv import pss_to_sv_with_ctx
    from .targets.sv.emit_files import emit_files
    from .targets.sv.lower_top import generate_top_module
    from .targets.sv.lower_export_api import ExportAction, build_oo_api_nodes

    multi_file = options.pop('multi_file', True)
    inference_mode = options.pop('inference_mode', 'static')
    include_runtime = options.pop('include_runtime', True)
    projection = options.pop('projection', 'oo_api')
    package_name = options.pop('package_name', 'zsp_gen_pkg')
    export_actions_opt = options.pop('export_actions', None)
    # comp_type / root_action_type: PSS names for oo_api; mangled SV names for
    # the legacy harness path (back-compat -- passed straight to generate_top_module).
    comp_type = options.pop('comp_type', None)
    root_action_type = options.pop('root_action_type', None)

    sv_nodes, ctx = pss_to_sv_with_ctx(ir_ctx)
    rt_src = _get_runtime_lib_path()
    emit_dpi = getattr(ctx, 'uses_dpi_solver', False)

    def _resolve_export_action(act_raw, comp_hint=None) -> ExportAction:
        act_q = _resolve_qualified_name(ir_ctx, act_raw, comp_hint=comp_hint)
        comp_q = ir_ctx.parent_comp_names.get(act_q)
        if comp_q is None and "::" in act_q:
            comp_q = act_q.rsplit("::", 1)[0]
        act_dt = ir_ctx.type_map.get(act_q)
        return ExportAction(
            task_name=act_q.rsplit("::", 1)[-1],
            action_sv=ctx.mangle_name(act_q),
            comp_sv=ctx.mangle_name(comp_q) if comp_q else ctx.mangle_name(act_q),
            has_activity=getattr(act_dt, 'activity_ir', None) is not None,
        )

    top_node = None
    if projection == 'oo_api':
        # Resolve the export set: explicit list, else the (auto-detected) root.
        if not export_actions_opt and not root_action_type:
            try:
                comp_type, root_action_type = _auto_detect_roots(ir_ctx)
            except ValueError:
                comp_type = root_action_type = None  # ambiguous -> plain package
        if export_actions_opt:
            specs = [_resolve_export_action(a, comp_hint=comp_type)
                     for a in export_actions_opt]
        elif root_action_type:
            specs = [_resolve_export_action(root_action_type, comp_hint=comp_type)]
        else:
            specs = []
        prefix, suffix = build_oo_api_nodes(
            sv_nodes, specs, ctx=ctx,
            import_funcs=getattr(ir_ctx, "import_functions", None))
        sv_nodes = prefix + sv_nodes + suffix
    else:
        # Legacy standalone harness projection (zsp_test_top). Resolve the root
        # from the explicit PSS name, else auto-detect it.
        root_pss, comp_pss = root_action_type, comp_type
        if not root_pss:
            try:
                comp_pss, root_pss = _auto_detect_roots(ir_ctx)
            except ValueError:
                comp_pss = root_pss = None
        if root_pss:
            ea = _resolve_export_action(root_pss, comp_hint=comp_pss)
            top_node = generate_top_module(
                comp_type=ea.comp_sv,
                root_action_type=ea.action_sv,
                has_activity=ea.has_activity,
                import_if_type=options.pop('import_if_type', None),
                import_if_driver=options.pop('import_if_driver', None),
                watchdog_ns=options.pop('watchdog_ns', 0),
            )

    if multi_file:
        return emit_files(
            nodes=sv_nodes,
            output_dir=output_dir,
            runtime_lib_path=rt_src if (include_runtime and rt_src.exists()) else None,
            top_module_node=top_node,
            emit_dpi=emit_dpi,
            package_name=package_name,
        )

    from zuspec.be.sv.ir.sv_emit import SVEmitter

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    emitter = SVEmitter()

    all_nodes = list(sv_nodes)
    if top_node is not None:
        all_nodes.append(top_node)

    sv_text = emitter.emit_all(all_nodes)
    written: List[Path] = []

    gen_path = out / 'zsp_pkg.sv'
    gen_path.write_text(sv_text + "\n")
    written.append(gen_path)

    if include_runtime and rt_src.exists():
        rt_dst = out / 'zsp_rt_pkg.sv'
        # Not copy2: it preserves the source's mode, and package data is
        # commonly read-only -- a second compile into the same output dir
        # would then fail to overwrite its own prior output.
        shutil.copyfile(str(rt_src), str(rt_dst))
        written.append(rt_dst)

    return written


# ---------------------------------------------------------------------------
# Phase 2 public API: the canonical IR hand-off, driver, and CLI-backed compile.
# Imported last so the targets registry (which reaches back into this module for
# the SV path) sees a fully-initialized package.
# ---------------------------------------------------------------------------
from .ir import to_core_context  # noqa: E402
from .driver import compile, CompileResult, CompileError  # noqa: E402
