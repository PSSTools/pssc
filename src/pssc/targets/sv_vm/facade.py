"""The classes of the `op-model-sv-vm` facade: op-model-sv's API, run by a
`pssvm_pkg` engine (design §10.2(b)).

Every method keeps op-model-sv's prototype, text for text (`_api_methods`), so
code written against `<root>_exp_if` compiles against either. A method's body
marshals its arguments by the value ABI (one 64-bit word per leaf, a struct in
declaration order, as the image's `ENTRY.sig`), calls the function by its
`comp::fn` symbol through `pssvm_ctxt.call` on the instance it stands for, and
casts the result back. The tree is the engine's: the facade holds only paths.

Not carried yet (refused when the API needs them): a string argument or result,
and a callee's writes to a struct argument (the ABI passes values in).
"""
from __future__ import annotations

from typing import List

from ...diag import Refusal
from ..sv.lower_progseq import (
    _DT_ENUM, _DT_INT, _DT_STRUCT, _api_methods, _ctor, _dt_name,
    _end, _import_fns, _import_proto, _root_name, _strip_pkg, exp_if_name,
    func_kind, FuncKind, imp_if_name, mangle, sv_type, SUB_PREFIX)

#: The members a generated class keeps: the engine context and its path.
CTXT = "pss_ctxt"
PATH = "pss_path"


def root_vm_name(root) -> str:
    return f"{_root_name(root)}_root_vm"


def vm_impl_name(comp) -> str:
    """A sub-component type's API class over the engine."""
    return f"{_strip_pkg(comp.name)}_exp_vm"


def plat_name(root) -> str:
    return f"{_root_name(root)}_vm_plat"


def _struct_of(dtype, type_map):
    """The declaration of struct type ``dtype`` (a reference may carry only
    its name)."""
    if getattr(dtype, "fields", None):
        return dtype
    name = getattr(dtype, "name", None)
    for key in (name, _strip_pkg(name)):
        t = type_map.get(key) if key else None
        if t is not None and getattr(t, "fields", None):
            return t
    for t in type_map.values():
        if (_dt_name(t) == _DT_STRUCT and getattr(t, "fields", None)
                and _strip_pkg(getattr(t, "name", "")) == _strip_pkg(name)):
            return t
    raise Refusal(f"op-model-sv-vm: no declaration of struct '{name}'")


def leaves(dtype, expr: str, type_map, what: str) -> List[str]:
    """The SV expressions of ``expr``'s value-ABI leaves, in order."""
    cn = _dt_name(dtype)
    if cn == _DT_STRUCT and sv_type(dtype) != "addr_handle_t":
        out: List[str] = []
        for f in _struct_of(dtype, type_map).fields:
            out += leaves(f.datatype, f"{expr}.{mangle(f.name)}", type_map, what)
        return out
    if cn in (_DT_INT, _DT_ENUM) or sv_type(dtype) == "addr_handle_t":
        return [expr]
    raise Refusal(f"op-model-sv-vm: {what}: a {sv_type(dtype)} cannot cross "
                  f"the engine boundary yet (value ABI: integers, enums, "
                  f"addresses and structs of them)")


def _leaf_names(dtype, name: str, type_map, what: str) -> List[str]:
    """``leaves`` by PSS name (struct fields dotted, unmangled), as the
    image's entries name their parameters."""
    if _dt_name(dtype) == _DT_STRUCT and sv_type(dtype) != "addr_handle_t":
        out: List[str] = []
        for f in _struct_of(dtype, type_map).fields:
            out += _leaf_names(f.datatype, f"{name}.{f.name}", type_map, what)
        return out
    leaves(dtype, name, type_map, what)
    return [name]


def _spec_line(fn, symbol: str, type_map) -> str:
    params = []
    for a in fn.args.args:
        params += _leaf_names(a.annotation, a.arg, type_map, symbol)
    ret = "->r" if fn.returns is not None else ""
    return f"{symbol}({','.join(params)}){ret}"


def api_spec(model) -> str:
    """What the facade calls in the engine, for `pssvm_engine::api_check`:
    sorted ``;``-joined ``comp::fn(p,s.f)`` lines (``->r`` when it returns
    a value) -- the root's constructor and every API type's functions."""
    type_map = getattr(model.ctx, "type_map", None) or {}
    lines = set()
    ctor = _ctor(model.root, model.ctor_names)
    if ctor is not None:
        lines.add(_spec_line(ctor, f"{model.root.name}::{ctor.name}", type_map))
    for api in model.apis:
        for _, _, what in _api_methods(model, api):
            if hasattr(what, "args") and hasattr(what, "returns"):
                lines.add(_spec_line(what, f"{api.comp.name}::{what.name}",
                                     type_map))
    return ";".join(sorted(lines))


def _cast_back(dtype, word: str) -> str:
    """A 64-bit result word as ``dtype``."""
    t = sv_type(dtype)
    if _dt_name(dtype) == _DT_ENUM:
        return f"{t}'({word})"
    return f"{word}[$bits({t})-1:0]" if t not in ("int", "addr_handle_t") \
        else f"{t}'({word})"


def _call_body(fn, symbol: str, path: str, type_map, pad: str) -> List[str]:
    """Marshal, `pssvm_ctxt.call`, cast back."""
    what = f"{symbol}"
    lines = [f"{pad}longint unsigned pss_args[$];",
             f"{pad}longint unsigned pss_ret;",
             f"{pad}bit pss_ok;"]
    for a in fn.args.args:
        for e in leaves(a.annotation, mangle(a.arg), type_map, what):
            lines.append(f"{pad}pss_args.push_back(64'({e}));")
    lines.append(f'{pad}{CTXT}.call("{symbol}", {path}, pss_args, pss_ret, '
                 f"pss_ok);")
    if fn.returns is not None:
        if _dt_name(fn.returns) == _DT_STRUCT and \
                sv_type(fn.returns) != "addr_handle_t":
            raise Refusal(f"op-model-sv-vm: {what}: a struct result cannot "
                          f"cross the engine boundary yet")
        leaves(fn.returns, "status", type_map, what)
        lines.append(f"{pad}status = {_cast_back(fn.returns, 'pss_ret')};")
    return lines


def _methods(model, api, owner_type: str, path: str) -> List[str]:
    """The API's methods, over the engine: functions call by symbol on
    ``path``; an accessor returns the child's API object, built once."""
    type_map = getattr(model.ctx, "type_map", None) or {}
    lines: List[str] = []
    for proto, _, what in _api_methods(model, api):
        lines += ["", f"    virtual {proto};"]
        if proto.startswith("task "):
            lines += _call_body(what, f"{owner_type}::{what.name}", path,
                                type_map, "      ")
        elif proto.startswith("function int "):
            lines.append(f"      return {what.size};")
        else:
            n = mangle(what.name)
            idx = "[i]" if what.is_array else ""
            lines.append(f"      return {SUB_PREFIX}{n}{idx};")
        lines.append(f"    {_end(proto)}")
    return lines


def _subs(model, api, path: str) -> List[str]:
    """``(declarations, construction)`` of the children's API objects."""
    decls, build = [], []
    for sub in api.children:
        n = mangle(sub.name)
        cls = vm_impl_name(sub.dtype)
        child = (f'{{{path}, {path} == "" ? "" : ".", "{sub.name}"}}')
        if sub.is_array:
            decls.append(f"    protected {cls} {SUB_PREFIX}{n}[{sub.size}];")
            build.append(f"foreach ({SUB_PREFIX}{n}[i]) {SUB_PREFIX}{n}[i] = "
                         f'new({CTXT}, $sformatf("%s[%0d]", {child}, i));')
        else:
            decls.append(f"    protected {cls} {SUB_PREFIX}{n};")
            build.append(f"{SUB_PREFIX}{n} = new({CTXT}, {child});")
    return decls, build


def emit_vm_impl(model, api) -> str:
    """``<type>_exp_vm implements <type>_exp_if``: one instance's API, by
    its path in the engine's tree."""
    decls, build = _subs(model, api, PATH)
    lines = [f"  // The API of one `{_strip_pkg(api.comp.name)}` instance, "
             f"run by the engine.",
             f"  class {vm_impl_name(api.comp)} implements "
             f"{exp_if_name(api.comp)};",
             f"    protected pssvm_ctxt {CTXT};",
             f"    protected string {PATH};"] + decls + [
             "",
             f"    function new(pssvm_ctxt ctxt, string path);",
             f"      {CTXT} = ctxt;",
             f"      {PATH} = path;"] + [f"      {b}" for b in build] + [
             "    endfunction"]
    lines += _methods(model, api, api.comp.name, PATH)
    lines.append("  endclass")
    return "\n".join(lines)


def _import_case(fn, ctor_names, type_map) -> List[str]:
    """One `case` arm of `call_import`: unpack the words, call ``Timp``."""
    n = mangle(fn.name)
    args = []
    for i, a in enumerate(fn.args.args):
        if leaves(a.annotation, "x", type_map, f"import {fn.name}") != ["x"]:
            raise Refusal(f"op-model-sv-vm: import {fn.name}: a struct "
                          f"argument cannot cross the engine boundary yet")
        args.append(_cast_back(a.annotation, f"req.args[{i}]"))
    body: List[str] = []
    if func_kind(fn, ctor_names) is FuncKind.IMPORT_TASK:
        if fn.returns is not None:
            body.append(f"begin {sv_type(fn.returns)} r; "
                        f"pss_imp.{n}({', '.join(['r'] + args)}); ret = 64'(r); end")
        else:
            body.append(f"pss_imp.{n}({', '.join(args)});")
    elif fn.returns is not None:
        body.append(f"ret = 64'(pss_imp.{n}({', '.join(args)}));")
    else:
        body.append(f"pss_imp.{n}({', '.join(args)});")
    return [f'        "{fn.name}": {body[0]}']


def emit_vm_platform(model) -> str:
    """``<root>_vm_plat #(Timp) extends pssvm_platform_if``: the engine's
    bus accesses and imports, on the user's import-API object."""
    root = model.root
    type_map = getattr(model.ctx, "type_map", None) or {}
    lines = [f"  // The engine's accesses and imports, on the platform "
             f"object.",
             f"  class {plat_name(root)} #(type Timp = {imp_if_name(root)}) "
             f"extends pssvm_platform_if;",
             "    Timp pss_imp;",
             "",
             "    function new(Timp imp);",
             "      pss_imp = imp;",
             "    endfunction",
             ""]
    for w in (8, 16, 32, 64):
        lines += [f"    virtual task read{w}(longint unsigned addr, output "
                  f"bit [{w - 1}:0] data); pss_imp.read{w}(addr, data); endtask",
                  f"    virtual task write{w}(longint unsigned addr, "
                  f"bit [{w - 1}:0] data); pss_imp.write{w}(addr, data); endtask"]
    lines += ["",
              "    virtual task call_import(pssvm_req req, "
              "output longint unsigned ret);",
              "      ret = 0;",
              "      case (req.name)"]
    for fn in _import_fns(model.ctx, model.ctor_names):
        lines += _import_case(fn, model.ctor_names, type_map)
    lines += ["        default: super.call_import(req, ret);",
              "      endcase",
              "    endtask",
              "  endclass"]
    return "\n".join(lines)


def emit_root_vm(model) -> str:
    """``<root>_root_vm #(Timp)``: `create(imp, <ctor args>)` as
    `<root>_root`'s, over the engine `pssvm_default_engine()` holds. The
    root's constructor runs in the engine; it must not yield (host ABI
    `construct-yield`), so `create` stays a function."""
    root = model.root
    name = root_vm_name(root)
    rtype = root.name
    ctor = _ctor(root, model.ctor_names)
    args = list(ctor.args.args) if ctor is not None else []
    params = "".join(f", {sv_type(a.annotation)} {mangle(a.arg)}"
                     for a in args)
    type_map = getattr(model.ctx, "type_map", None) or {}
    rapi = model.api(root)
    decls, build = _subs(model, rapi, PATH)
    lines = [f"  class {name} #(type Timp = {imp_if_name(root)}) implements "
             f"{exp_if_name(root)};",
             f"    protected pssvm_ctxt {CTXT};",
             f'    protected string {PATH} = "";'] + decls + [
             "",
             "    // Only create() builds a model.",
             "    protected function new(pssvm_ctxt ctxt);",
             f"      {CTXT} = ctxt;"] + [f"      {b}" for b in build] + [
             "    endfunction",
             "",
             f"    static function {exp_if_name(root)} create(Timp pss_imp"
             f"{params});",
             "      pssvm_engine pss_eng = pssvm_default_engine();",
             f"      {plat_name(root)} #(Timp) pss_plat;",
             "      string pss_bad;",
             "      if (pss_eng == null) begin",
             f'        $display("PSSVM-ERROR: {name}::create: no engine '
             f'(pssvm_set_default_engine)");',
             "        return null;",
             "      end",
             "      // The image must hold what this facade calls.",
             f'      pss_bad = pss_eng.api_check("{api_spec(model)}");',
             '      if (pss_bad != "") begin',
             f'        $display("PSSVM-ERROR: {name}::create: image API '
             f'differs: %s", pss_bad);',
             "        return null;",
             "      end",
             "      pss_plat = new(pss_imp);"]
    if ctor is not None:
        words = []
        for a in args:
            words += leaves(a.annotation, mangle(a.arg), type_map,
                            f"{rtype}::{ctor.name}")
        lines += ["      begin",
                  "        longint unsigned pss_args[$];",
                  "        int pss_h;"]
        lines += [f"        pss_args.push_back(64'({e}));" for e in words]
        lines += [f'        pss_h = pss_eng.call_start("{rtype}::{ctor.name}", '
                  f'"", pss_args);',
                  "        if (pss_h == 0 || pss_eng.run(pss_h) != PSSVM_DONE) "
                  "begin",
                  f'          $display("PSSVM-ERROR: {name}::create: %s", '
                  f"pss_eng.error(pss_h));",
                  "          if (pss_h != 0) pss_eng.call_end(pss_h);",
                  "          return null;",
                  "        end",
                  "        pss_eng.call_end(pss_h);",
                  "      end"]
    lines += ["      begin",
              "        pssvm_ctxt pss_c = new(pss_eng, pss_plat);",
              f"        {name} #(Timp) pss_model = new(pss_c);",
              "        return pss_model;",
              "      end",
              "    endfunction"]
    lines += _methods(model, rapi, rtype, PATH)
    for entry in model.entries:
        lines += ["", f"    // Exported action `{entry.action}`.",
                  f"    virtual task {mangle(entry.name)}();",
                  f'      $display("PSSVM-ERROR: exported action '
                  f'{entry.action}: not run by the engine yet");',
                  "    endtask"]
    lines.append("  endclass")
    return "\n".join(lines)


def root_sel_name(root) -> str:
    return f"{_root_name(root)}_root_sel"


def emit_root_sel(model) -> str:
    """``<root>_root_sel #(Timp)``, with `--with-gen` only: `create(imp,
    <ctor args>)` builds the model `+pss_impl=vm|native|gen` names (default
    `gen`) -- `<root>_root_vm`'s over the engine, or op-model-sv's
    `<root>_root`. One testbench, compiled once, then runs either
    implementation. `vm` and `native` build the same facade: which engine
    sits under it (pyvm or the C engine) is the testbench's choice."""
    from ..sv.lower_progseq import factory_name
    root = model.root
    name = root_sel_name(root)
    ctor = _ctor(root, model.ctor_names)
    args = list(ctor.args.args) if ctor is not None else []
    params = "".join(f", {sv_type(a.annotation)} {mangle(a.arg)}"
                     for a in args)
    fwd = "".join(f", {mangle(a.arg)}" for a in args)
    return "\n".join([
        f"  class {name} #(type Timp = {imp_if_name(root)});",
        "    // The implementation +pss_impl names: \"vm\", \"native\" (both the",
        "    // facade over pssvm_default_engine()) or \"gen\" (default).",
        "    static function string impl();",
        "      string pss_s;",
        '      if (!$value$plusargs("pss_impl=%s", pss_s)) pss_s = "gen";',
        "      return pss_s;",
        "    endfunction",
        "",
        f"    static function {exp_if_name(root)} create(Timp pss_imp"
        f"{params});",
        "      string pss_s = impl();",
        '      if (pss_s == "vm" || pss_s == "native")',
        f"        return {root_vm_name(root)} #(Timp)::create(pss_imp{fwd});",
        '      if (pss_s == "gen")',
        f"        return {factory_name(root)} #(Timp)::create(pss_imp{fwd});",
        f'      $display("PSSVM-ERROR: {name}::create: +pss_impl=%s: '
        f'expected vm, native or gen", pss_s);',
        "      return null;",
        "    endfunction",
        "  endclass"])
