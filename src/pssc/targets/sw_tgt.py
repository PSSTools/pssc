"""C (software) targets via ``zuspec-be-sw`` — Context-first, no ``zuspec-dataclasses``.

``c-host`` (output style 3): host-targeted C coroutine runtime. The translated PSS
IR ``Context`` is lowered (actions -> components, activities inlined) and handed to
``zuspec.be.sw.CGenerator.generate(ctx)``, which emits the C coroutine runtime.

A generated ``main.c`` harness instantiates the root action and drives its activity
to completion via the timebase. With ``--runtime-solve`` (PSS style 4), rand fields
are solved by **dv-solve at runtime** (each run seedable) instead of baked at
compile time (style 5).
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import List, Optional

from .base import Target
from .sw_lower import to_sw_context, find_root_action
from ..ir import to_core_context


def _sanitize(name: str) -> str:
    """Mirror zuspec-be-sw's ``sanitize_c_name`` (``pss_top::Root`` -> ``pss_top__Root``)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _resolve_root(core, requested: Optional[str]) -> Optional[str]:
    """Resolve the root action to a qualified ``type_m`` key."""
    if requested:
        if requested in core.type_m:
            return requested
        matches = [k for k in core.type_m if k.split("::")[-1] == requested]
        return matches[0] if len(matches) == 1 else None
    return find_root_action(core)


def _plan_globals(plan):
    """Yield ``(global_name, var_id, entry_index)`` for every solved slot."""
    for i, entry in enumerate(plan):
        for slot in entry["slots"]:
            yield ("g_" + entry["prefix"] + slot.cname, slot.var_id, i)


def _root_harness(root_qual: str, runtime_solve: bool, with_main: bool = True) -> str:
    """Emit ``pssc_run(int seed)`` — instantiate ``root_qual`` and drive its
    activity — plus, when ``with_main``, a CLI ``main`` that calls it. The
    ``pssc_run`` entry is also the DPI boundary the sv-dpi facade calls."""
    t = _sanitize(root_qual)
    hdr = t.lower()
    pre = "#include <stdlib.h>\n#include <time.h>\n"
    if runtime_solve:
        pre += "extern void pssc_solve_all(unsigned long long seed);\n"
    solve_call = "    pssc_solve_all((unsigned long long)seed);\n" if runtime_solve else ""
    main = ""
    if with_main:
        main = """
int main(int argc, char **argv) {
    int seed = argc > 1 ? atoi(argv[1]) : (int)time(0);
    pssc_run(seed);
    return 0;
}
"""
    return f"""{pre}#include "zsp_alloc.h"
#include "zsp_init_ctxt.h"
#include "zsp_timebase.h"
#include "{hdr}.h"

void pssc_run(int seed) {{
    (void)seed;
{solve_call}    zsp_alloc_t alloc;
    zsp_alloc_malloc_init(&alloc);

    zsp_timebase_t tb;
    zsp_timebase_init(&tb, &alloc, ZSP_TIME_PS);

    zsp_init_ctxt_t ctxt;
    ctxt.alloc = &alloc;
    ctxt.timebase = &tb;

    {t} root;
    {t}_init(&ctxt, &root, "root", NULL);
    {t}_body(&root, &tb);
    zsp_timebase_run(&tb);
}}
{main}"""


def _sv_dpi_facade(seed: int = 1) -> str:
    """SV facade: import the c-host ``pssc_run`` entry via DPI and call it from a
    top module. (Style 2: SV testbench drives the C coroutine runtime.)"""
    return f"""// pssc-generated SV/DPI facade over the c-host C runtime.
package pssc_dpi_pkg;
  import "DPI-C" function void pssc_run(input int seed);
endpackage

module pssc_top;
  import pssc_dpi_pkg::*;
  initial begin
    pssc_run({seed});
    $finish;
  end
endmodule
"""


def _solve_c(plan) -> str:
    """The ``pssc_solve.c`` translation unit: includes only dv-solve headers (kept
    separate from the runtime headers, which carry a conflicting ``zsp_alloc.h``).
    Defines the solved-field globals and ``pssc_solve_all`` which solves each
    traversal's embedded problem with a per-run seed."""
    lines = [
        "#include <stdint.h>",
        "#include <string.h>",
        '#include "zsp_block_alloc.h"',
        '#include "zsp_problem.h"',
        '#include "zsp_ctx.h"',
        '#include "zsp_search.h"',
        "",
    ]
    for g, _vid, _i in _plan_globals(plan):
        lines.append(f"int32_t {g};")
    lines.append("")
    for i, entry in enumerate(plan):
        data = ",".join(str(x) for x in entry["bytes"])
        lines.append(f"static const unsigned char prob_{i}[] = {{{data}}};")
    lines += ["", "void pssc_solve_all(unsigned long long seed) {",
              "    static unsigned char cbuf[1<<20];"]
    for i, entry in enumerate(plan):
        lines.append("    {")
        lines.append("        zsp_block_alloc_t *ba = zsp_block_alloc_create(0, 1<<20);")
        lines.append("        SolveCtx *c = solver_create(cbuf, sizeof(cbuf), ba);")
        lines.append(f"        solver_compile(c, (SolveProblem*)prob_{i});")
        lines.append("        SolveOpts o; memset(&o, 0, sizeof(o));")
        lines.append(f"        o.seed = seed + {i}ull; o.fair_pick = 1;")
        lines.append("        solver_solve(c, &o);")
        for slot in entry["slots"]:
            g = "g_" + entry["prefix"] + slot.cname
            lines.append(f"        {g} = (int32_t)solver_get_value(c, {slot.var_id});")
        lines.append("        solver_destroy(c); zsp_block_alloc_destroy(ba);")
        lines.append("    }")
    lines += ["}", ""]
    return "\n".join(lines)


def _inject_externs(c_path: Path, plan) -> None:
    """Add ``extern`` decls for the solved-field globals to a generated .c file so
    the coroutine (which reads them) compiles."""
    externs = "\n".join(f"extern int32_t {g};" for g, _v, _i in _plan_globals(plan))
    if not externs:
        return
    text = c_path.read_text()
    lines = text.splitlines()
    last_inc = max((i for i, l in enumerate(lines) if l.startswith("#include")), default=-1)
    lines.insert(last_inc + 1, "#include <stdint.h>\n" + externs)
    c_path.write_text("\n".join(lines) + "\n")


class _CTarget(Target):
    """Shared C-coroutine codegen. Subclasses set the default solve mode; the
    host/embedded distinction is the runtime tier (future)."""

    #: When True, rand fields are solved by dv-solve at runtime (style 4); when
    #: False they are pre-solved at compile time and baked in (style 5).
    default_runtime_solve = False

    #: Emit a CLI ``main`` in the harness (False for the DPI entry, which SV calls).
    harness_with_main = True

    #: Also emit the SV/DPI facade (``pssc_top.sv``) over the C runtime.
    emit_sv_dpi = False

    def run(self, ctx, opts: argparse.Namespace) -> List[Path]:
        from zuspec.be.sw import CGenerator

        core = getattr(ctx, "ir_context", None)
        if core is None:
            core = to_core_context(ctx)

        # Per-target default, overridable either way on the CLI.
        runtime_solve = self.default_runtime_solve
        if getattr(opts, "runtime_solve", False):
            runtime_solve = True
        if getattr(opts, "presolve", False):
            runtime_solve = False
        solve_plan = [] if runtime_solve else None
        sw_ctx = to_sw_context(core, solve_plan=solve_plan)

        out = Path(str(getattr(opts, "output_dir", ".") or "."))
        out.mkdir(parents=True, exist_ok=True)
        files = list(CGenerator(output_dir=str(out)).generate(sw_ctx))

        root = _resolve_root(core, getattr(opts, "root_action", None))

        if runtime_solve and solve_plan:
            # separate solver TU + extern decls in the root coroutine .c
            solve_c = out / "pssc_solve.c"
            solve_c.write_text(_solve_c(solve_plan))
            files.append(solve_c)
            if root is not None:
                _inject_externs(out / (_sanitize(root).lower() + ".c"), solve_plan)

        if root is not None:
            main_c = out / "main.c"
            main_c.write_text(_root_harness(
                root, runtime_solve and bool(solve_plan), with_main=self.harness_with_main))
            if main_c not in files:
                files.append(main_c)
            if self.emit_sv_dpi:
                sv = out / "pssc_top.sv"
                sv.write_text(_sv_dpi_facade())
                files.append(sv)
        return files


class CHostTarget(_CTarget):
    name = "c-host"
    description = "Host C coroutine runtime, dv-solve runtime solving (style 3)"
    default_runtime_solve = True

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        # The C targets share these options; only c-host registers them so the
        # single compile parser sees each option once.
        parser.add_argument(
            "--root-action", dest="root_action", metavar="NAME", default=None,
            help="C targets: action whose activity the generated main runs "
            "(simple or qualified name; default: auto-detect the single top action)",
        )
        parser.add_argument(
            "--runtime-solve", dest="runtime_solve", action="store_true", default=False,
            help="C targets: force dv-solve runtime solving",
        )
        parser.add_argument(
            "--presolve", dest="presolve", action="store_true", default=False,
            help="C targets: force compile-time pre-solving (bake constant values)",
        )


class CHostPresolvedTarget(_CTarget):
    name = "c-host-presolved"
    description = "Host C coroutine runtime, pre-solved constraints (style 3)"
    default_runtime_solve = False


class SvDpiTarget(_CTarget):
    name = "sv-dpi"
    description = "SV facade driving the c-host C runtime via DPI (style 2)"
    default_runtime_solve = True
    harness_with_main = False   # SV calls pssc_run via DPI instead of main()
    emit_sv_dpi = True


# ---------------------------------------------------------------------------
# sv-dpi-bridge: multi-action C scenario driven from SV via the zsp_bridge DPI
# runtime (Backend B, Phase C1). See design/pssc-c-bridge-runtime-design.md and
# design/pssc-c1-impl-plan.md. No imports yet (C2+).
# ---------------------------------------------------------------------------

def _c_share_dir() -> Path:
    """pssc's C seam directory (ships zsp_bridge.{h,c})."""
    return Path(__file__).resolve().parents[1] / "share" / "c"


def _besw_share() -> tuple:
    """(include_dir, rt_dir) of the zuspec-be-sw runtime."""
    from zuspec.be import sw as _sw
    base = Path(_sw.__file__).parent / "share"
    return base / "include", base / "rt"


def _dvsolve_share() -> tuple:
    """(include_dirs, lib_dir) for dv-solve (runtime constraint solving).

    Derived from dv-solve's own ``get_incdirs()``/``get_libdirs()``, which
    answer for both an editable checkout and an installed wheel.

    This previously walked ``Path(_dv.__file__).parents[2]`` to
    ``<root>/src/c`` and ``<root>/build`` -- the SOURCE-TREE layout. Against an
    installed wheel those parents land in site-packages and neither path
    exists, so building the solver TU failed with

        fatal error: zsp_block_alloc.h: No such file or directory

    i.e. the runtime-solve bridge could not be built by anyone using released
    wheels, only from a checkout. ``_besw_share()`` above was already written
    the portable way; this was the odd one out.

    Returns a LIST of include dirs (a wheel reports the base plus the
    namespaced ``dv_solve/`` subdir). These stay the include set for the SOLVER
    translation unit ONLY -- dv-solve and be-sw ship conflicting ``zsp_alloc.h``
    definitions, so the two include sets must not be merged.
    """
    import dv_solve as _dv  # the installed package, if present
    return [Path(d) for d in _dv.get_incdirs()], Path(_dv.get_libdirs()[0])


def _resolve_actions(core, opts) -> List[dict]:
    """Resolve the export-action set to ``[{id, short, ctype, header}]``.

    Uses ``--export-action`` names when given, else the auto-detected single
    root. ``ctype`` is the sanitized C type (``pss_top__Entry``); ``header`` is
    its lowercased ``.h``; ``short`` is the unqualified action name.
    """
    names = getattr(opts, "export_actions", None)
    quals: List[str] = []
    if names:
        for n in names:
            q = _resolve_root(core, n)
            if q is None:
                raise ValueError(f"sv-dpi-bridge: cannot resolve export action '{n}'")
            quals.append(q)
    else:
        q = find_root_action(core)
        if q is None:
            raise ValueError(
                "sv-dpi-bridge: no export actions; pass --export-action NAME")
        quals.append(q)

    actions = []
    for i, q in enumerate(quals):
        ctype = _sanitize(q)
        actions.append({
            "id": i,
            "short": q.split("::")[-1],
            "ctype": ctype,
            "header": ctype.lower() + ".h",
            "dt": core.type_m.get(q),   # IR dtype, for presolving rand fields
        })
    return actions


def _rewrite_import_calls(core, solve_names, target_names) -> None:
    """Rewrite exec-body import calls (mutates action bodies in ``core``):

    * **solve** imports -> a *global* call (``do_read(...)``), resolved at link by
      an SV ``export "DPI-C"`` function (synchronous);
    * **target** imports -> an *await* of the self-method call, which makes be-sw
      emit ``zsp_timebase_call(&<comp>_<import>_task, ...)`` + a suspend, so the
      coroutine blocks while the SV import task (possibly time-consuming) runs and
      the bridge mailbox re-wakes it.
    """
    import zuspec.ir.core as ir
    solve_names = set(solve_names or ())
    target_names = set(target_names or ())
    if not solve_names and not target_names:
        return

    def _is_self_call(e, names):
        return (isinstance(e, ir.ExprCall)
                and isinstance(e.func, ir.ExprAttribute)
                and isinstance(e.func.value, ir.TypeExprRefSelf)
                and e.func.attr in names)

    def rw_expr(e):
        if e is None or not hasattr(e, "__class__"):
            return e
        for attr in ("func", "value", "lhs", "rhs", "slice", "expr"):
            if hasattr(e, attr):
                setattr(e, attr, rw_expr(getattr(e, attr)))
        if hasattr(e, "args") and isinstance(e.args, list):
            e.args = [rw_expr(x) for x in e.args]
        if _is_self_call(e, solve_names):
            e.func = ir.ExprRefUnresolved(name=e.func.attr)   # synchronous global
        return e

    def rw_stmt(s):
        # A void target import call appears as a bare statement -> await it.
        if isinstance(s, ir.StmtExpr) and _is_self_call(s.expr, target_names):
            s.expr.args = [rw_expr(a) for a in s.expr.args]   # solve args first
            s.expr = ir.ExprAwait(value=s.expr)
            return
        for attr in ("expr", "value", "condition", "test"):
            if hasattr(s, attr) and getattr(s, attr) is not None:
                setattr(s, attr, rw_expr(getattr(s, attr)))
        for attr in ("body", "orelse"):
            if hasattr(s, attr) and isinstance(getattr(s, attr), list):
                for x in getattr(s, attr):
                    rw_stmt(x)

    for dt in core.type_m.values():
        for fn in getattr(dt, "functions", []) or []:
            for s in getattr(fn, "body", []) or []:
                rw_stmt(s)


_STDLIB_PKG_PREFIXES = ("executor_pkg::", "addr_reg_pkg::", "sync_pkg::", "std_pkg::")


def _resolve_action_qual(core, short):
    """Resolve a traversal's ``action_type`` short name to its qualified type_m
    key (preferring the ``::``-qualified entry that be-sw mangles from)."""
    import zuspec.ir.core as ir
    best = None
    for k, dt in core.type_m.items():
        if not isinstance(dt, ir.DataTypeClass):
            continue
        if k == short or k.endswith("::" + short):
            if "::" in k:
                return k
            best = best or k
    return best


def _user_action_specs(core) -> List[dict]:
    """All user (non-stdlib) action types: ``[{qual, ctype, header, dt}]`` deduped
    by identity (qualified key preferred). be-sw generates a body coroutine for
    each, so each may reference its own import sub-tasks."""
    import zuspec.ir.core as ir
    seen, out = set(), []
    for name, dt in core.type_m.items():
        if not isinstance(dt, ir.DataTypeClass):
            continue
        if any(name.startswith(p) for p in _STDLIB_PKG_PREFIXES) or "::" not in name:
            continue
        if id(dt) in seen:
            continue
        seen.add(id(dt))
        ct = _sanitize(name)
        out.append({"qual": name, "ctype": ct, "header": ct.lower() + ".h", "dt": dt})
    return out


def _parallel_branches(core, dt):
    """If ``dt``'s activity is a single top-level ``parallel`` of ``do <Leaf>``
    traversals, return the branch leaves' qualified names; else None."""
    import zuspec.ir.core as ir
    node = getattr(dt, "activity_ir", None)
    if node is None:
        return None
    if isinstance(node, ir.ActivitySequenceBlock):
        stmts = node.stmts or []
        if len(stmts) != 1:
            return None
        node = stmts[0]
    if not isinstance(node, ir.ActivityParallel):
        return None
    leaves = []
    for ch in (node.stmts or []):
        if not isinstance(ch, ir.ActivityAnonTraversal):
            return None
        q = _resolve_action_qual(core, ch.action_type)
        if q is None:
            return None
        leaves.append(q)
    return leaves or None


def _field_assigns(a: dict, var: str, runtime: bool) -> List[str]:
    """C assignments that set a spawned action's own ``rand`` fields between
    ``_init`` and ``_body``: a runtime-solved global (``root.x = g_Entry__x;``)
    when ``runtime``, else a baked constant (``root.x = 4;``).

    Covers top-level scalar rand fields (the common atomic-action case); deeper
    paths / arrays are left to the activity-inlining solve path. Non-rand fields
    keep the ``_init`` default of 0.
    """
    dt = a.get("dt")
    if dt is None:
        return []
    from .sw_lower import _field_locals, solve_global_name
    out: List[str] = []
    if runtime:
        solve = a.get("solve") or {}
        prefix = solve.get("prefix", "")
        slot_cnames = {s.cname for s in solve.get("slots", [])}
        for fld in getattr(dt, "fields", []):
            for cname, path, _edt in _field_locals(fld):
                if len(path) == 1 and cname in slot_cnames:
                    out.append(f"        {var}.{path[0]} = {solve_global_name(prefix, cname)};")
        return out
    try:
        from .sw_solve import solve_action, seed_for
        solved = solve_action(dt, seed=seed_for(getattr(dt, "name", "") or ""))
    except Exception:
        return []
    for fld in getattr(dt, "fields", []):
        for cname, path, _edt in _field_locals(fld):
            if len(path) == 1 and cname in solved:
                out.append(f"        {var}.{path[0]} = {int(solved[cname])};")
    return out


def _bridge_dispatch_c(actions: List[dict], runtime: bool) -> str:
    """The generated ``pssc_bridge_dispatch.c``: ACTION_* ids + spawn switch.

    Each case instantiates the action's root coroutine in **static** storage (it
    must outlive ``spawn`` until ``run`` drains it -- one active instance per
    action, the documented serial contract), injects its solved rand fields, and
    posts its ``_body`` onto the bridge timebase. When ``runtime``, the per-spawn
    ``seed`` drives ``pssc_solve_all`` (dv-solve) into the ``g_*`` globals.
    """
    from .sw_lower import solve_global_name
    has_par = any(a.get("parallel") for a in actions)
    L = ['#include "zsp_bridge.h"', "#include <stdint.h>"]
    if has_par:
        L.append("#include <stdarg.h>")
    for a in actions:
        L.append(f'#include "{a["header"]}"')
    if runtime:
        L.append("extern void pssc_solve_all(unsigned long long seed);")
        for a in actions:
            solve = a.get("solve") or {}
            for s in solve.get("slots", []):
                L.append(f'extern int32_t {solve_global_name(solve["prefix"], s.cname)};')
    for a in actions:
        if a.get("parallel"):
            L.append(f'extern zsp_frame_t *pssc_par_{a["ctype"]}_task('
                     "zsp_timebase_t *, zsp_thread_t *, int, va_list *);")
    L.append("")
    for a in actions:
        L.append(f'#define ACTION_{a["short"]} {a["id"]}')
    L.append("")
    L.append("void pssc_bridge_dispatch(zsp_bridge_t *b, int action_id, long long seed) {")
    if runtime:
        L.append("    pssc_solve_all((unsigned long long)seed);")
    else:
        L.append("    (void)seed;")
    L.append("    switch (action_id) {")
    for a in actions:
        ct = a["ctype"]
        L.append(f'    case ACTION_{a["short"]}: {{')
        if a.get("parallel"):
            # spawn the fork/join coroutine instead of the (sequential) body
            L.append(f"        zsp_thread_t *t = zsp_timebase_thread_create("
                     f"&b->tb, &pssc_par_{ct}_task, ZSP_THREAD_FLAGS_NONE, b);")
            L.append("        t->exit_f = (zsp_thread_exit_f)&zsp_timebase_thread_free;")
        else:
            L.append(f"        static {ct} root;")
            L.append(f'        {ct}_init(&b->ctxt, &root, "root", NULL);')
            L.extend(_field_assigns(a, "root", runtime))
            L.append(f"        {ct}_body(&root, &b->tb);")
        L.append("        break;")
        L.append("    }")
    L.append("    default: break;")
    L.append("    }")
    L.append("}")
    return "\n".join(L) + "\n"


def _import_sv_type(ann) -> str:
    """SV type for an import arg/return. Scalar integral only in this slice."""
    bits = int(getattr(ann, "bits", 32) or 32) if ann is not None else 32
    if bits <= 32:
        return "int"
    return "longint"


def _import_kinds(imports):
    """Split imports into (solve, target). solve = synchronous value/function;
    target = blocking SUT task (may consume time)."""
    imports = list(imports or [])
    solve = [f for f in imports if not getattr(f, "is_target", False) or f.returns is not None]
    target = [f for f in imports if getattr(f, "is_target", False) and f.returns is None]
    return solve, target


def _bridge_imports_sv(imports) -> List[str]:
    """The import seam:

    * ``pssc_import_if`` the testbench implements (target -> **task**, solve ->
      **function**) + a global handle/setter;
    * one ``export "DPI-C"`` **function** per *solve* import (the C scenario calls
      it synchronously by name);
    * ``FN_*`` ids and ``pssc_dispatch_import`` for *target* imports, which the
      forking trampoline runs out of the request mailbox.
    """
    solve, target = _import_kinds(imports)
    if not solve and not target:
        return []
    L = ["",
         "  // Import seam: testbench implements pssc_import_if + registers it via",
         "  // pssc_set_imports. solve imports are synchronous export \"DPI-C\"",
         "  // functions; target imports are tasks run from the request mailbox.",
         "  interface class pssc_import_if;"]
    for f in target:
        args = ", ".join(f"{_import_sv_type(a.annotation)} {a.arg}" for a in f.args.args)
        L.append(f"    pure virtual task {f.name}({args});")
    for f in solve:
        args = ", ".join(f"{_import_sv_type(a.annotation)} {a.arg}" for a in f.args.args)
        ret = _import_sv_type(f.returns) if f.returns is not None else "void"
        L.append(f"    pure virtual function {ret} {f.name}({args});")
    L.append("  endclass")
    L.append("  pssc_import_if g_pssc_imp;")
    L.append("  function void pssc_set_imports(pssc_import_if imp); g_pssc_imp = imp; endfunction")
    # solve imports: synchronous export "DPI-C" functions
    for f in solve:
        argdecl = ", ".join(f"{_import_sv_type(a.annotation)} {a.arg}" for a in f.args.args)
        argcall = ", ".join(a.arg for a in f.args.args)
        ret = _import_sv_type(f.returns) if f.returns is not None else "void"
        L.append(f'  export "DPI-C" function {f.name};')
        if ret == "void":
            L.append(f"  function void {f.name}({argdecl}); g_pssc_imp.{f.name}({argcall}); endfunction")
        else:
            L.append(f"  function {ret} {f.name}({argdecl}); return g_pssc_imp.{f.name}({argcall}); endfunction")
    # target imports: FN ids + a dispatcher run from the mailbox
    if target:
        for i, f in enumerate(target):
            L.append(f"  localparam int FN_{f.name} = {i};")
        L.append("  task automatic pssc_dispatch_import(int fn_id, chandle ab);")
        L.append("    case (fn_id)")
        for f in target:
            call = ", ".join(f"zsp_bridge_arg_i(ab, {j})" for j in range(len(f.args.args)))
            L.append(f"      FN_{f.name}: g_pssc_imp.{f.name}({call});")
        L.append("    endcase")
        L.append("  endtask")
    return L


def _bridge_pkg_sv(actions: List[dict], imports=None) -> str:
    """``pssc_bridge_pkg.sv``: DPI decls, ACTION_* localparams, the import seam
    (when the model has imports), and a ``pssc_run_action`` trampoline task.

    ``zsp_bridge_run`` is a **context** import: the C scenario may re-enter SV
    (calling an exported import) while it runs, which requires the SV scope to be
    set (IEEE 1800 35.5.3; matches the Verilator finding).
    """
    _solve, target = _import_kinds(imports)
    L = ["// pssc-generated bridge package (sv-dpi-bridge).",
         "package pssc_bridge_pkg;",
         '  import "DPI-C" function chandle zsp_bridge_create();',
         '  import "DPI-C" function void    zsp_bridge_destroy(chandle b);',
         '  import "DPI-C" function void    zsp_bridge_spawn(chandle b, int action_id, longint seed);',
         '  import "DPI-C" context function void zsp_bridge_run(chandle b);',
         '  import "DPI-C" function int     zsp_bridge_done(chandle b);',
         '  import "DPI-C" context function void zsp_bridge_capture_scope();',
         '  import "DPI-C" function int     zsp_bridge_next_request(chandle b, output int req_id, output int fn_id, output chandle args);',
         '  import "DPI-C" function void    zsp_bridge_complete(chandle b, int req_id, longint ret);',
         '  import "DPI-C" function longint zsp_bridge_arg_i(chandle args, int idx);']
    L += _bridge_imports_sv(imports)
    L.append("")
    for a in actions:
        L.append(f'  localparam int ACTION_{a["short"]} = {a["id"]};')
    L.append("")
    if target:
        # Trampoline as a forking event loop: run the C scheduler, drain blocking
        # import requests, fork each SV import task (it may consume time), and
        # complete it (re-waking the coroutine); repeat until done.
        L += [
            "  // Spawn one action; service blocking imports via fork/complete.",
            "  task automatic pssc_run_action(chandle b, int action_id, longint seed);",
            "    int rid, fid; chandle ab; int outstanding = 0; event progress;",
            "    zsp_bridge_capture_scope();",
            "    zsp_bridge_spawn(b, action_id, seed);",
            "    forever begin",
            "      zsp_bridge_run(b);",
            "      while (zsp_bridge_next_request(b, rid, fid, ab)) begin",
            "        outstanding++;",
            "        fork",
            "          begin",
            "            automatic int     l_rid = rid;",
            "            automatic int     l_fid = fid;",
            "            automatic chandle l_ab  = ab;",
            "            pssc_dispatch_import(l_fid, l_ab);",
            "            zsp_bridge_complete(b, l_rid, 64'd0);",
            "            outstanding--; -> progress;",
            "          end",
            "        join_none",
            "      end",
            "      if (zsp_bridge_done(b) && outstanding == 0) break;",
            "      @progress;",
            "    end",
            "  endtask",
        ]
    else:
        L += [
            "  // Spawn one action and drive the C scheduler to completion.",
            "  task automatic pssc_run_action(chandle b, int action_id, longint seed);",
            "    zsp_bridge_capture_scope();   // arm SV scope for C->SV imports",
            "    zsp_bridge_spawn(b, action_id, seed);",
            "    zsp_bridge_run(b);",
            "    while (zsp_bridge_done(b) == 0) zsp_bridge_run(b);",
            "  endtask",
        ]
    L += [
        "",
        "  // As above, but draw the C-side seed from the calling process's random",
        "  // state ($urandom is the thread PRNG, reproducible under the testbench seed).",
        "  task automatic pssc_run_action_rand(chandle b, int action_id);",
        "    pssc_run_action(b, action_id, {$urandom(), $urandom()});",
        "  endtask",
        "endpackage",
    ]
    return "\n".join(L) + "\n"


def _bridge_import_tasks_c(actions: List[dict], target_imports) -> str:
    """Generated coroutine sub-tasks for blocking (target) imports + FN_* ids.

    One ``<comp>_<import>_task`` per (export action, target import): marshal the
    call args into a request, post it (suspending the coroutine), and on resume
    return ``thread->rval``. Referenced by the action body's ``zsp_timebase_call``.
    """
    target_imports = list(target_imports or [])
    if not target_imports:
        return ""
    L = ['#include "zsp_bridge.h"', "#include <stdarg.h>", "#include <stdint.h>", ""]
    for i, f in enumerate(target_imports):
        L.append(f"#define FN_{f.name} {i}")
    L.append("")
    for a in actions:
        ct = a["ctype"]
        for f in target_imports:
            n = len(f.args.args)
            fn = f"{ct}_{f.name}_task"
            L.append(f"zsp_frame_t *{fn}(zsp_timebase_t *tb, zsp_thread_t *thread, int idx, va_list *args) {{")
            L.append("    zsp_frame_t *ret = thread->leaf; (void)tb;")
            L.append("    typedef struct { zsp_bridge_req_t req; } locals_t;")
            L.append("    switch (idx) {")
            L.append("    case 0: {")
            L.append(f"        ret = zsp_timebase_alloc_frame(thread, sizeof(locals_t), &{fn});")
            L.append("        locals_t *L = zsp_frame_locals(ret, locals_t);")
            L.append("        (void)va_arg(*args, void *);   /* self */")
            for j in range(n):
                L.append(f"        long long a{j} = (long long)va_arg(*args, int);")
            L.append(f"        L->req.fn_id = FN_{f.name}; L->req.argc = {n};")
            for j in range(n):
                L.append(f"        L->req.argv[{j}] = a{j};")
            L.append("        zsp_bridge_post_request(thread, &L->req);")
            L.append("        ret->idx = 1; break;")
            L.append("    }")
            L.append("    case 1: ret = zsp_timebase_return(thread, (uintptr_t)thread->rval); break;")
            L.append("    }")
            L.append("    return ret;")
            L.append("}")
            L.append("")
    return "\n".join(L) + "\n"


def _bridge_parallel_c(par_specs: List[dict]) -> str:
    """Generated fork/join coroutines for top-level ``parallel`` actions.

    Per parallel action: one branch coroutine per ``do <Leaf>`` that constructs
    the leaf, runs its (be-sw-generated) body coroutine, then ``done_one`` + wakes
    the suspended parent on join; and a ``pssc_par_<comp>_task`` that forks the
    branches and suspends until they join. The dispatcher spawns this task instead
    of the action's (sequentially-inlined) body. Concurrent blocking imports post
    at the same instant -> the SV trampoline forks them concurrently.
    """
    if not par_specs:
        return ""
    headers = sorted({lb["header"] for p in par_specs for lb in p["leaves"]})
    bodies = sorted({lb["body"] for p in par_specs for lb in p["leaves"]})
    L = ['#include "zsp_bridge.h"', '#include "zsp_par_block.h"',
         "#include <stdarg.h>", "#include <stdint.h>"]
    L += [f'#include "{h}"' for h in headers]
    L.append("")
    # prototypes for the leaf body coroutines (un-static'd in their own TU)
    for body in bodies:
        L.append(f"zsp_frame_t *{body}(zsp_timebase_t *, zsp_thread_t *, int, va_list *);")
    L.append("")
    for p in par_specs:
        ct = p["ctype"]
        for i, lb in enumerate(p["leaves"]):
            lct, lbody = lb["ctype"], lb["body"]
            fn = f"pssc_par_{ct}_b{i}"
            L += [
                f"static zsp_frame_t *{fn}(zsp_timebase_t *tb, zsp_thread_t *thread, int idx, va_list *args) {{",
                "    zsp_frame_t *ret = thread->leaf; (void)tb;",
                f"    typedef struct {{ zsp_par_block_t *pb; zsp_thread_t *parent; {lct} leaf; }} locals_t;",
                "    switch (idx) {",
                "    case 0: {",
                f"        ret = zsp_timebase_alloc_frame(thread, sizeof(locals_t), &{fn});",
                "        locals_t *L = zsp_frame_locals(ret, locals_t);",
                "        L->pb = va_arg(*args, zsp_par_block_t *);",
                "        L->parent = va_arg(*args, zsp_thread_t *);",
                "        zsp_bridge_t *b = va_arg(*args, zsp_bridge_t *);",
                f'        {lct}_init(&b->ctxt, &L->leaf, "leaf", NULL);',
                "        ret->idx = 1;",
                f"        ret = zsp_timebase_call(thread, &{lbody}, &L->leaf);",
                "        break;",
                "    }",
                "    case 1: {",
                "        locals_t *L = zsp_frame_locals(thread->leaf, locals_t);",
                "        zsp_par_block_done_one(L->pb);",
                "        if (zsp_par_block_join(L->pb)) {",
                "            L->parent->flags &= ~ZSP_THREAD_FLAGS_BLOCKED;",
                "            zsp_timebase_schedule(tb, L->parent);",
                "        }",
                "        ret = zsp_timebase_return(thread, 0);",
                "        break;",
                "    }",
                "    }",
                "    return ret;",
                "}",
            ]
        n = len(p["leaves"])
        task = f"pssc_par_{ct}_task"
        L += [
            f"zsp_frame_t *{task}(zsp_timebase_t *tb, zsp_thread_t *thread, int idx, va_list *args) {{",
            "    zsp_frame_t *ret = thread->leaf; (void)tb;",
            "    typedef struct { zsp_par_block_t pb; } locals_t;",
            "    switch (idx) {",
            "    case 0: {",
            f"        ret = zsp_timebase_alloc_frame(thread, sizeof(locals_t), &{task});",
            "        locals_t *L = zsp_frame_locals(ret, locals_t);",
            "        zsp_bridge_t *b = va_arg(*args, zsp_bridge_t *);",
            f"        zsp_par_block_init(&L->pb, {n});",
        ]
        for i in range(n):
            L.append(f"        {{ zsp_thread_t *bt = zsp_timebase_thread_create("
                     f"tb, &pssc_par_{ct}_b{i}, ZSP_THREAD_FLAGS_NONE, &L->pb, thread, b);"
                     " bt->exit_f = (zsp_thread_exit_f)&zsp_timebase_thread_free; }")
        L += [
            "        thread->flags |= ZSP_THREAD_FLAGS_BLOCKED;",
            "        ret->idx = 1; break;",
            "    }",
            "    case 1: ret = zsp_timebase_return(thread, 0); break;",
            "    }",
            "    return ret;",
            "}",
            "",
        ]
    return "\n".join(L) + "\n"


def _bridge_import_protos_h(a: dict, target_imports) -> str:
    """Forward declarations for one action's blocking-import sub-tasks, appended
    to that action's generated header (its body_task takes the function address).
    Plain prototypes are idempotent under re-inclusion."""
    target_imports = list(target_imports or [])
    if not target_imports:
        return ""
    L = ["", "/* pssc bridge: blocking-import sub-tasks */", "#include <stdarg.h>"]
    for f in target_imports:
        L.append(f"zsp_frame_t *{a['ctype']}_{f.name}_task("
                 "zsp_timebase_t *, zsp_thread_t *, int, va_list *);")
    return "\n".join(L) + "\n"


class SvDpiBridgeTarget(Target):
    name = "sv-dpi-bridge"
    description = ("Multi-action C scenario as a DPI shared lib (libpssc_scenario.so) "
                  "driven from SV via the zsp_bridge runtime (Phase C1)")

    # `--export-action` is the shared compile-level option (see cli.build_parser).

    def run(self, ctx, opts: argparse.Namespace) -> List[Path]:
        import subprocess
        from zuspec.be.sw import CGenerator

        core = getattr(ctx, "ir_context", None) or to_core_context(ctx)
        actions = _resolve_actions(core, opts)
        runtime = bool(getattr(opts, "runtime_solve", False))
        imports = list(getattr(ctx, "import_functions", None) or [])

        out = Path(str(getattr(opts, "output_dir", ".") or "."))
        out.mkdir(parents=True, exist_ok=True)

        # Imports: solve -> synchronous global call (SV export "DPI-C"); target ->
        # await (suspend) serviced via the request mailbox + a sub-task coroutine.
        solve_imports, target_imports = _import_kinds(imports)
        _rewrite_import_calls(core, {f.name for f in solve_imports},
                              {f.name for f in target_imports})

        # All user actions get a be-sw body coroutine (each may reference its own
        # import sub-tasks). Detect export actions whose activity is a top-level
        # `parallel` -> generate a fork/join task instead of the sequential body.
        user_specs = _user_action_specs(core)
        by_qual = {u["qual"]: u for u in user_specs}
        par_specs = []
        for a in actions:
            leaves = _parallel_branches(core, a.get("dt")) if a.get("dt") is not None else None
            if not leaves:
                continue
            lspecs = [by_qual[q] for q in leaves if q in by_qual]
            if len(lspecs) == len(leaves):
                a["parallel"] = True
                par_specs.append({"ctype": a["ctype"], "leaves": [
                    {"ctype": u["ctype"], "header": u["header"],
                     "body": u["ctype"] + "_body_task"} for u in lspecs]})

        # Generate the C scenario (the body coroutines; rand fields are injected
        # by the dispatcher -- baked (presolve) or solved per-spawn (runtime)).
        #
        # Disable be-sw's async->sync body conversion: the bridge always drives
        # action bodies as async coroutines on the timebase, so the inlined
        # `_body_sync` variant is dead code -- and its statement generator renders
        # a rewritten global import call as `0(...)` (the async generator renders
        # it correctly), which would fail to compile. Async-only sidesteps that.
        sw_ctx = to_sw_context(core, solve_plan=None)
        from zuspec.be.sw.async_analyzer import AsyncAnalyzer
        _orig_sc = AsyncAnalyzer.is_sync_convertible
        AsyncAnalyzer.is_sync_convertible = lambda self, c, f: False
        try:
            gen = list(CGenerator(output_dir=str(out)).generate(sw_ctx))
        finally:
            AsyncAnalyzer.is_sync_convertible = _orig_sc

        # Runtime-solve: a per-action dv-solve problem -> pssc_solve.c (separate
        # TU: dv-solve headers must not co-occur with the runtime's zsp_alloc.h).
        if runtime:
            from .sw_solve import problem_bytes
            plan = []
            for a in actions:
                pb, slots = problem_bytes(a["dt"]) if a.get("dt") is not None else (None, [])
                if pb is not None and slots:
                    a["solve"] = {"prefix": a["short"] + "__", "slots": slots}
                    plan.append({"prefix": a["solve"]["prefix"], "bytes": pb, "slots": slots})
            if plan:
                (out / "pssc_solve.c").write_text(_solve_c(plan))

        # Bridge runtime (copied) + generated dispatcher + SV shim.
        share_c = _c_share_dir()
        for fn in ("zsp_bridge.h", "zsp_bridge.c"):
            (out / fn).write_text((share_c / fn).read_text())
        (out / "pssc_bridge_dispatch.c").write_text(_bridge_dispatch_c(actions, runtime))
        (out / "pssc_bridge_pkg.sv").write_text(_bridge_pkg_sv(actions, imports))

        # Blocking (target) imports: the sub-task coroutines, plus their
        # prototypes appended to each action's generated header (the body_task TU
        # takes &<comp>_<import>_task). Header-scoped -- a global force-include
        # would leak into runtime TUs whose stale zsp_thread.h conflicts.
        have_target = bool(target_imports)
        if have_target:
            # Import sub-tasks for every user action (any action's body may call a
            # target import). Prototypes appended to each action's own header --
            # a global force-include would clash with the stale zsp_thread.h.
            (out / "pssc_bridge_imports.c").write_text(
                _bridge_import_tasks_c(user_specs, target_imports))
            for u in user_specs:
                hdr = out / u["header"]
                if hdr.exists():
                    hdr.write_text(hdr.read_text()
                                   + _bridge_import_protos_h(u, target_imports))

        # Top-level parallel actions: the fork/join coroutines. be-sw makes each
        # <leaf>_body_task `static`; un-static the leaves the parallel coroutines
        # call so they link across TUs.
        if par_specs:
            for ct in {lb["ctype"] for p in par_specs for lb in p["leaves"]}:
                cf = out / (ct.lower() + ".c")
                if cf.exists():
                    cf.write_text(cf.read_text().replace(
                        f"static zsp_frame_t *{ct}_body_task(",
                        f"zsp_frame_t *{ct}_body_task("))
            (out / "pssc_bridge_parallel.c").write_text(_bridge_parallel_c(par_specs))

        written = list(gen) + [
            out / "zsp_bridge.h", out / "zsp_bridge.c",
            out / "pssc_bridge_dispatch.c", out / "pssc_bridge_pkg.sv",
        ]
        if runtime and (out / "pssc_solve.c").exists():
            written.append(out / "pssc_solve.c")
        if have_target:
            written.append(out / "pssc_bridge_imports.c")
        if par_specs:
            written.append(out / "pssc_bridge_parallel.c")

        # Build libpssc_scenario.so when a compiler is available.
        if shutil.which("gcc"):
            inc, rt = _besw_share()
            objs: List[str] = []
            link_extra: List[str] = []
            # Separate TU for the dv-solve solver (own include path).
            if runtime and (out / "pssc_solve.c").exists():
                dv_incs, dv_lib = _dvsolve_share()
                solve_o = out / "pssc_solve.o"
                rs = subprocess.run(
                    ["gcc", "-w", "-c", "-fPIC", *[f"-I{i}" for i in dv_incs],
                     str(out / "pssc_solve.c"), "-o", str(solve_o)],
                    capture_output=True, text=True)
                if rs.returncode != 0:
                    raise RuntimeError(f"pssc_solve.c build failed:\n{rs.stderr}")
                objs.append(str(solve_o))
                link_extra = [f"-L{dv_lib}", "-ldv_solve", f"-Wl,-rpath,{dv_lib}"]

            srcs = [str(f) for f in gen if f.suffix == ".c" and f.name != "main.c"]
            srcs += [str(out / "zsp_bridge.c"), str(out / "pssc_bridge_dispatch.c")]
            if have_target:
                srcs.append(str(out / "pssc_bridge_imports.c"))
            if par_specs:
                srcs.append(str(out / "pssc_bridge_parallel.c"))
            srcs += [str(p) for p in rt.glob("*.c")]
            lib = out / "libpssc_scenario.so"
            cmd = ["gcc", "-w", "-fPIC", "-shared",
                   f"-I{inc}", f"-I{share_c}", f"-I{out}", *srcs, *objs,
                   "-o", str(lib), *link_extra]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"libpssc_scenario.so build failed:\n{r.stderr}")
            written.append(lib)

        return written


class CEmbeddedTarget(_CTarget):
    name = "c-embedded"
    description = "Embedded C coroutine runtime, dv-solve runtime solving (style 4)"
    default_runtime_solve = True


class CEmbeddedPresolvedTarget(_CTarget):
    name = "c-embedded-presolved"
    description = "Embedded C coroutine runtime, pre-solved constraints (style 5)"
    default_runtime_solve = False
