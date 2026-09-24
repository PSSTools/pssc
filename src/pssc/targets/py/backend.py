"""Assemble a PSS operation model into one Python module.

Built as a class, and for the same two reasons `c/backend.py` is:

* **Every `emit_*` returns text; only `generate`/`write_files` touch disk.** A
  method that writes cannot be wrapped by a subclass calling `super()`, which is
  the whole point of the class.
* **The module is exactly its `sections()`**, in order, each section carrying
  its own trailing blank -- so a subclass changes one by name
  (`targets/sections.py`) rather than by re-deriving the whole file.

ONE FILE, unlike C's header/implementation pair. Python has no separate
declaration to keep in step, and splitting a generated model across modules
would buy nothing but an import cycle between a component and its parent.

WHAT THE GENERATED MODULE IMPORTS: nothing outside the STANDARD LIBRARY,
unless the model has channels. That is a property worth keeping rather than an
accident -- a generated driver gets copied onto a lab machine, and a single
file needing no install is one that still runs there. The platform seam is
structural: the module generates its own `Protocol` for it and requires nothing
to inherit that, and the base classes the generated code needs are emitted into
the module itself. A model with channels needs the runtime's `Chan1` and says
so with an import at the top -- the one exception, and it is visible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from ..progseq_model import (INIT_EXEC_KINDS, FuncKind, _dt_name,
                             channel_fields, exec_kind, field_is_reg_group,
                             sub_components)
from .. import comp_inherit as ci
from .. import executors as xtr
from .. import pkg_functions as pf
from . import lower_api_types as api
from . import lower_import_api as impapi
from . import lower_progseq as lp
from . import lower_reg_model as reg
from .naming import (BIND_METHOD, CONSTRUCT_METHOD, CTOR_METHOD,
                     DEFAULT_EXECUTOR, EXECUTOR_ATTR, IMPORTS_ATTR,
                     IMPORTS_PARAM, INIT_METHOD, class_name, def_kw,
                     field_storage, group_base, init_kind_method, is_async,
                     mangle, module_name)

_DT_STRUCT = "DataTypeStruct"
_DT_ARRAY = "DataTypeArray"


def _no_component():
    """The scope a package function's body is rendered in: a component with
    nothing in it, so `self.x` can only be a parameter or a package constant
    and a call can only be a package function, an import or a built-in."""
    import zuspec.ir.core as ir
    return ir.DataTypeComponent(name="", super=None, fields=[], functions=[])


_NO_COMPONENT = _no_component()


def _initializers():
    """The function a component's field initializers are rendered in: none.
    They are constant expressions, so there is no scope to carry."""
    import zuspec.ir.core as ir
    return ir.Function(name="<initializers>")


_INITIALIZERS = _initializers()


class PyOpModelBackend(object):
    """Turns an `OpModel` into the text of one Python module."""

    def __init__(self, module: str = "", await_style: str = "sync"):
        #: The generated module's name, without `.py`. Empty means "derive it
        #: from the root component", which is what every command line that does
        #: not pass `--py-module` gets.
        self.module = module
        #: `"sync"` or `"async"` -- `--py-await`. Held here rather than read
        #: from options at each site so that a subclass driving the backend
        #: directly picks a form the same way the command line does.
        self.await_style = await_style
        #: `id(model)` -> the components section. The import-API Protocol is
        #: derived from the text the bodies emit (`lower_import_api`), and the
        #: same text is then emitted; rendering it twice would be correct and
        #: wasteful, and would run every body's diagnostics twice.
        self._components_memo: Dict[int, str] = {}
        #: `id(model)` -> `(async ids, functions section)`; see `_pkg_async`.
        self._functions_memo: Dict[int, Tuple[frozenset, str]] = {}

    #: The body emitter in force. Marked `provisional` for the same reason the C
    #: backend's is: `BodyWalker` is not a published override surface, and a
    #: change to how bodies are walked moves what a subclass here sees.
    body_emitter_cls = lp._BodyEmitter
    ctor_emitter_cls = lp._CtorEmitter

    # -- assembly ------------------------------------------------------------

    def module_sections(self, model) -> Sequence[Tuple[str, Any]]:
        """`[(name, emit)]` -- the module, in order.

        THE list. `emit_module` joins exactly these and nothing else, so a
        subclass inserting a section (`sections.insert_after`) gets it into the
        output without re-deriving the file, and a section it renames raises
        here rather than silently vanishing.
        """
        return [
            ("banner", self.emit_banner),
            ("runtime_imports", self.emit_runtime_imports),
            ("import_api", self.emit_import_api),
            ("api_types", self.emit_api_types),
            ("value_types", self.emit_value_types),
            ("formatting", self.emit_formatting),
            ("integers", self.emit_integers),
            ("functions", self.emit_functions),
            ("executors", self.emit_executors),
            ("components", self.emit_components),
        ]

    def emit_module(self, model) -> str:
        """The whole module: its sections, in order, each with its own blank."""
        out: List[str] = []
        for _, emit in self.module_sections(model):
            text = emit(model)
            if text:
                out.append(text.rstrip("\n"))
        return "\n\n\n".join(out) + "\n"

    def sections(self, model) -> Dict[str, str]:
        """`{"<file>:<section>": text}`. Nothing is written.

        What the differential test helper compares. Attributing a change to the
        section that produced it turns "the module changed" into "the
        `components` section changed", which is a claim an extension author can
        declare in advance.
        """
        name = self.file_name(model)
        return {f"{name}:{sec}": emit(model)
                for sec, emit in self.module_sections(model)}

    def module_name(self, model) -> str:
        """The generated module's importable name. `--py-module` wins."""
        return self.module or module_name(
            getattr(model.root, "name", "op_model"))

    def file_name(self, model) -> str:
        return self.module_name(model) + ".py"

    # -- sections ------------------------------------------------------------

    def emit_banner(self, model) -> str:
        """The module docstring: what this is, what it needs, how to drive it.

        A docstring rather than a comment block, because Python has one and a
        generated module people `help()` should answer.
        """
        from ... import __version__

        root = (getattr(model.root, "name", "") or "").split("::")[-1]
        cls = class_name(root)
        ctor = model.ctor(model.root)
        op = self._first_operation(model)
        args = self._example_ctor_args(ctor)
        return "\n".join([
            f'"""Programming API for `{root}`, generated by pssc '
            f'{__version__} (op-model-py).',
            "",
            "DO NOT EDIT. Regenerate from the PSS model instead.",
            "",
            f"The platform seam is `{impapi.import_api_name(model)}` below: a "
            "PROTOCOL, not a",
            "base class. Pass any object with those methods -- nothing has to",
            "inherit anything. `pssc_rt.py`, copied beside this file, ships a",
            "stub to bring the model up on and a `check_import_api()` that",
            "reports what an object is missing.",
            "",
        ] + self._usage(model, cls, args, op) + ['"""'])

    def _example_ctor_args(self, ctor) -> List[str]:
        """VALUES for the banner's snippet, not the parameter names.

        The snippet has to run, and a line reading `WbDma(MemoryBus(), base)`
        does not: `base` is a name nothing in the snippet defines. The address
        handle gets a plausible one and everything else gets zero -- neither is
        a claim about the device, which is why the snippet stops at construction
        unless there is a no-argument operation to call.
        """
        if ctor is None:
            return []
        addr = self._addr_arg(ctor)
        return ["0x1000" if mangle(a.arg) == addr else "0"
                for a in ctor.args.args]

    def _usage(self, model, cls, args, op) -> List[str]:
        """The runnable snippet in the banner.

        RUNNABLE AS WRITTEN, against the runtime this generation copies beside
        the module -- there is a test that executes it. A usage example that
        was true when it was written and is not now is worse than none, and the
        async form is exactly where that would happen: it needs a different
        import, a different stub and an event loop to be in.
        """
        mod = self.module_name(model)
        if self.await_style != "async":
            return [
                f"    from {mod} import {cls}",
                "    from pssc_rt import MemoryBus",
                "",
                f"    dut = {cls}(MemoryBus(), {', '.join(args)})",
            ] + ([f"    dut.{op}()"] if op else [])
        return [
            "    import asyncio",
            f"    from {mod} import {cls}",
            "    from pssc_rt_async import AsyncMemoryBus",
            "",
            "    async def main():",
            f"        dut = {cls}(AsyncMemoryBus(), {', '.join(args)})",
        ] + ([f"        await dut.{op}()"] if op else ["        pass"]) + [
            "",
            "    asyncio.run(main())",
        ]

    def _first_operation(self, model):
        """A no-argument operation of the root, for the banner. Or ``None``.

        No-argument, because the snippet has to RUN and this file cannot invent
        a plausible value for `cfg`. A model whose root operations all take
        arguments gets a snippet that constructs and stops there, which is
        still true.
        """
        for fn in model.operations(model.root):
            if not fn.args.args:
                return mangle(fn.name)
        return None

    def emit_runtime_imports(self, model) -> str:
        """The module's imports: the standard library, then `pssc_rt` if needed.

        The invariant this section exists to keep is NOTHING FROM OUTSIDE THE
        STANDARD LIBRARY unless the model declares channels. A generated driver
        gets copied onto a lab machine, and a single file that runs there with
        no install step is worth more than any convenience an import would buy.

        `typing` and `__future__` are not an exception to that -- they ship with
        the interpreter. What is excluded is `pssc_rt`, and a model with
        channels needs its `Chan1` and says so.
        """
        lines = [
            "# Annotations are strings (PEP 563): the import API below names",
            "# classes this module defines further down, and a forward",
            "# reference costs nothing at run time.",
            "from __future__ import annotations",
            "",
            "from typing import Protocol, runtime_checkable",
        ]
        if any(model.channels(n.dtype) for n in model.components):
            # WHICH runtime depends on the form, and the difference is the one
            # thing the two forms genuinely disagree about: `get()`/`put()`
            # suspend, so the sync channel raises on them and the async one
            # waits.
            mod = ("pssc_rt_async" if self.await_style == "async"
                   else "pssc_rt")
            lines += [
                "",
                "# This model declares channels, so it needs the shipped",
                "# depth-1 channel. Everything else in this module stands"
                " alone.",
                f"from {mod} import Chan1",
            ]
        return "\n".join(lines)

    def emit_import_api(self, model) -> str:
        """The `<Root>ImportApi` Protocol -- what the platform must supply.

        Derived from the components section's own text, which is why that
        section is rendered here and memoised rather than walked twice; see
        `lower_import_api` for why the demand is read off the OUTPUT.
        """
        used = impapi.imports_used(self._bodies_text(model))
        return "\n".join(impapi.lower_import_api(
            model, await_style=self.await_style, used=used))

    def emit_api_types(self, model) -> str:
        return "\n".join(api.lower_api_types(
            model, self._default_of(model))).rstrip()

    def emit_value_types(self, model) -> str:
        return "\n\n\n".join(reg.lower_value_classes(
            model, self._default_of(model))).rstrip()

    def _default_of(self, model):
        """`field -> text`: a struct field's default, rendered the way a
        component field's is (`_initial_value`)."""
        be = self._initializer_emitter(_NO_COMPONENT, model)
        return lambda f: self._initial_value(f, be)

    def _initializer_emitter(self, comp, model):
        """An emitter for constant initializers in the scope of *comp*."""
        return self.ctor_emitter_cls(_INITIALIZERS, comp, model,
                                     imports=model.imports,
                                     ctor_names=model.ctor_names,
                                     await_style=self.await_style,
                                     pkg_async=self._pkg_async(model))

    def emit_formatting(self, model) -> str:
        """`_pss_fmt`, the PSS string formatter -- only if a message uses it.

        Carried by the module rather than imported, for the same reason as the
        value base classes: the module runs where it is copied. Its source is
        `pss_format.py`, which the generator also uses to check each format, so
        what is checked and what runs are one piece of code. Decided from the
        generated bodies' own text, as the import API's demand is.
        """
        if "_pss_fmt(" not in self._bodies_text(model):
            return ""
        from .pss_format import inline_source
        return ("# ----- PSS string formatting (LRM 21.1.1) for message(). "
                "-----\n\n" + inline_source())

    def emit_integers(self, model) -> str:
        """The PSS integer helpers the bodies call (`pss_int.py`) -- only
        those, so a model whose arithmetic Python's operators already spell
        carries none. Decided from the generated text, as `_pss_fmt` is."""
        from .pss_int import HELPERS, inline_source
        text = self._bodies_text(model)
        used = [n for n in HELPERS if f"{n}(" in text]
        if not used:
            return ""
        return ("# ----- PSS integer arithmetic (LRM 8.5.1, 8.7). -----\n\n"
                + inline_source(used))

    def emit_components(self, model) -> str:
        """One class per component, CHILDREN FIRST.

        Python resolves a name at call time, so the order is not a requirement
        the way C's is. It is `model.components` anyway, because that is the
        order every other backend emits in and a reader comparing two generated
        languages should not have to account for a difference that means
        nothing.
        """
        return self._components_text(model)

    def _bodies_text(self, model) -> str:
        """Everything that renders a PSS body: package functions, the default
        executor and components. What the import API and the formatter are
        derived from."""
        return (self.emit_functions(model) + "\n" + self.emit_executors(model)
                + "\n" + self._components_text(model))

    # -- executors -----------------------------------------------------------

    def emit_executors(self, model) -> str:
        """`_PssDefaultExecutor`: the memory primitives' default
        implementation, which is the platform (LRM 21.13.9.5).

        Only for a model with an executor (`executors.has_executors`); every
        other model calls the seam directly, as it always has. A component
        with no executor is bound to an instance of this class, and every
        executor class DERIVES from it, so a primitive an executor does not
        override reaches the platform by ordinary method lookup.

        It defines the primitives a delegated call can actually reach it with
        and no others -- decided from the bodies' text, as the import API's
        demand is, and narrowed by `executors.default_reachable` -- so the
        platform is asked for nothing the model does not do. A root that
        assigns an executor overriding every primitive it uses needs none.
        """
        if not xtr.has_executors(model):
            return ""
        text = self.emit_functions(model) + "\n" + self._components_text(model)
        # Called through an executor and able to reach the default
        # (`executors.default_reachable`), or called as the default outright.
        used = [p for p in xtr.PRIMITIVES
                if (f".{EXECUTOR_ATTR}.{p}(" in text
                    and xtr.default_reachable(model, p))
                or f"{DEFAULT_EXECUTOR}.{p}(" in text
                # `super().read32(...)` in an executor: its base's, which may
                # be this class (a user executor's base may be too, and then
                # the method is simply not reached).
                or f"super().{p}(" in text]
        d = def_kw(self.await_style)
        aw = "await " if is_async(self.await_style) else ""
        lines = [
            f"class {DEFAULT_EXECUTOR}(object):",
            '    """The memory primitives with no override: the platform.',
            "",
            "    A component with no executor is bound to one of these, and "
            "every",
            "    executor class derives from it (LRM 21.7.2.6, 21.13.9.5). The",
            "    descriptor is not passed on: 21.13.9 lets a tool ignore it "
            "when it",
            '    maps a primitive directly.',
            '    """',
            "",
            f"    def __init__(self, {IMPORTS_PARAM}: "
            f"{impapi.import_api_name(model)}):",
            f"        self.{IMPORTS_ATTR} = {IMPORTS_PARAM}",
        ]
        for p in used:
            direction, _ = xtr.PRIMITIVES[p]
            if direction == "read":
                lines += ["", f"    {d} {p}(self, hndl, desc):",
                          f"        return {aw}self.{IMPORTS_ATTR}.{p}(hndl)"]
            else:
                lines += ["", f"    {d} {p}(self, hndl, data, desc):",
                          f"        {aw}self.{IMPORTS_ATTR}.{p}(hndl, data)"]
        return "\n".join(lines)

    # -- package-scope functions ---------------------------------------------

    def emit_functions(self, model) -> str:
        """The package-scope functions the model calls (`pkg_functions.py`),
        as module-level functions, in first-reached order."""
        return self._functions(model)[1]

    def emit_function(self, fn, model, pkg_async) -> Tuple[List[str], bool]:
        """One package function, and whether its body awaits anything.

        Its first parameter is the CALLING component, named `self` because
        the body is rendered by the same emitter as an operation's and reaches
        the platform the same way, through `self._imports`. It reads nothing
        else from it: a package function has no component (LRM 22.2).
        """
        params = ["self"]
        args = list(fn.args.args) if fn.args else []
        defaults = list(getattr(fn.args, "defaults", None) or [])
        first_dflt = len(args) - len(defaults)
        be = self.body_emitter_cls(fn, _NO_COMPONENT, model,
                                   imports=model.imports,
                                   ctor_names=model.ctor_names,
                                   await_style=self.await_style,
                                   pkg_async=pkg_async)
        for i, a in enumerate(args):
            p = mangle(a.arg)
            if i >= first_dflt:
                p += f"={be.expr(defaults[i - first_dflt])}"
            params.append(p)
        body = be.emit(fn.body, 1)
        kw = "async def" if id(fn) in pkg_async else "def"
        lines = [f"{kw} {lp.pkg_function_name(fn)}({', '.join(params)}):"]
        lines += reg._docstring(getattr(fn, "doc", None), "    ") or [
            f'    """Package function `{pf.qualified_name(fn)}`."""']
        return lines + body, be.awaited_any

    def _pkg_async(self, model) -> frozenset:
        """`id()`s of the package functions that are `async def` here."""
        return self._functions(model)[0]

    def _functions(self, model) -> Tuple[frozenset, str]:
        """Colour, then render, the package functions.

        Sync form: none is coloured. Async form: a `target function` is, for
        the reason every operation is (`emit_operation`); an unqualified one
        is exactly when its body awaits -- directly, or by calling one that
        does. That is a fixed point, since a function can call itself: start
        from the `target` ones, re-render, add each body that now awaits,
        until nothing changes.
        """
        key = id(model)
        if key not in self._functions_memo:
            fns = list(model.functions)
            col = frozenset()
            if self.await_style == "async":
                col = frozenset(id(f) for f in fns
                                if getattr(f, "is_target", False))
                while True:
                    grown = col | {id(f) for f in fns
                                   if self.emit_function(f, model, col)[1]}
                    if grown == col:
                        break
                    col = frozenset(grown)
            text = ""
            if fns:
                text = ("# ----- Package functions the model calls. -----\n\n"
                        + "\n\n\n".join(
                            "\n".join(self.emit_function(f, model, col)[0])
                            for f in fns))
            self._functions_memo[key] = (col, text)
        return self._functions_memo[key]

    def _components_text(self, model) -> str:
        key = id(model)
        if key not in self._components_memo:
            self._components_memo[key] = "\n\n\n".join(
                self.emit_component(node, model)
                for node in self._class_nodes(model))
        return self._components_memo[key]

    @staticmethod
    def _class_nodes(model) -> List[Any]:
        """A node per class to emit: the tree's, plus base classes nothing
        instantiates (`OpModel.classes`), each base before its subclasses --
        Python evaluates `class Der(Base)` when the module loads."""
        import types
        by_type = {}
        for n in model.components:
            by_type.setdefault(id(n.dtype), n)
        classes = getattr(model, "classes", None) or [
            n.dtype for n in model.components]
        return [by_type.get(id(c)) or types.SimpleNamespace(dtype=c)
                for c in classes]

    # -- one component -------------------------------------------------------

    def emit_component(self, node, model) -> str:
        """One component class: constructor, register accessors, operations."""
        comp = node.dtype
        if ci.in_hierarchy(model.ctx, comp):
            return self.emit_hier_component(node, model)
        name = class_name(getattr(comp, "name", ""))
        base = (DEFAULT_EXECUTOR if xtr.is_executor(comp, self._tm(model))
                else "object")
        lines = [f"class {name}({base}):"]
        lines += reg._docstring(getattr(comp, "doc", None), "    ") or [
            f'    """Programming API for `{getattr(comp, "name", name)}`."""']
        lines.append("")
        lines += self.emit_constructor(node, model)
        init = self.emit_init(node, model)
        if init:
            lines.append("")
            lines += init
        for fn in model.super_blocks(comp):
            lines.append("")
            lines += self.emit_super_block(fn, comp, model)
        bind = self.emit_bind(node, model)
        if bind:
            lines.append("")
            lines += bind
        accs = reg.lower_accessors(comp, self.await_style,
                                   desc=self._delegated_desc(model))
        if accs:
            lines.append("")
            lines.append("    # ----- Register accessors, offsets folded. -----")
            lines += accs
        subs = self.emit_sub_accessors(comp)
        if subs:
            lines.append("")
            lines += subs
        for fn in model.operations(comp):
            lines.append("")
            lines += self.emit_operation(fn, comp, model)
        for entry in model.entries_of(comp):
            lines.append("")
            lines += self.emit_entry(entry, comp, model)
        return "\n".join(lines)

    # -- component inheritance, rendered natively -----------------------------

    @staticmethod
    def _declared(model, comp):
        """``(base, fields, functions)`` as *comp* DECLARES them: its base
        component (or None) and its own members, `super` intact
        (`comp_inherit.declared`)."""
        dec = ci.declared(model.ctx, comp)
        if dec is None:
            return (None, list(getattr(comp, "fields", None) or []),
                    list(getattr(comp, "functions", None) or []))
        return dec.base, list(dec.fields), list(dec.functions)

    def _data_names(self, model, comp) -> List[str]:
        view = self._view(self._declared(model, comp)[1])
        return [f.name for f in self._data_members(view)]

    @staticmethod
    def _view(fields):
        """A component-shaped object holding just *fields*, for the member
        walks (`sub_components`, `channel_fields`, ...) over declared ones."""
        import types
        return types.SimpleNamespace(fields=list(fields), functions=[],
                                     name="", super=None)

    def _chain(self, model, comp) -> List[Any]:
        out = []
        while comp is not None and all(comp is not c for c in out):
            out.append(comp)
            comp = self._declared(model, comp)[0]
        return out

    def _contested(self, model, decl, name) -> bool:
        """Is data field *name* declared both by *decl* and by another class
        on a line of inheritance through it? Then each lives under its own
        attribute (`naming.field_storage`)."""
        if any(name in self._data_names(model, c)
               for c in self._chain(model, decl)[1:]):
            return True
        return any(c is not decl and any(decl is x for x in self._chain(model, c))
                   and name in self._data_names(model, c)
                   for c in getattr(model, "classes", ()) or ())

    def _storage_map(self, model, comp) -> Dict[str, str]:
        """``{field: attribute}`` for every data field *comp* sees: the
        nearest declaration up its chain, under its own attribute when that
        name is contested."""
        out: Dict[str, str] = {}
        for c in self._chain(model, comp):
            for n in self._data_names(model, c):
                if n not in out:
                    out[n] = (field_storage(c.name, n)
                              if self._contested(model, c, n) else mangle(n))
        return out

    def emit_hier_component(self, node, model) -> str:
        """A component in an inheritance hierarchy, as a Python class derived
        from its base component's class (LRM 17.1, Table 27).

        It emits only what the component DECLARES; everything else is
        inherited by method resolution. Functions are virtual, as Python's
        methods are, and `super` is Python's `super()`. Construction is split
        so each class adds its part: `_pss_construct` (state, base's first)
        and `_pss_ctor` (the PSS constructor, which a derived one may shadow
        and reach with `super.initialize(...)`). Init blocks shadow per kind
        (`_pss_init_down`, `_pss_init_up`), and `super;` in one is the base's.
        A field a base and a derived class both declare is two fields
        (`naming.field_storage`), each read by its own class's code and
        exposed by a property.
        """
        comp = node.dtype
        tm = self._tm(model)
        base, own_fields, own_fns = self._declared(model, comp)
        view = self._view(own_fields)
        name = class_name(getattr(comp, "name", ""))
        if base is not None:
            parent = class_name(getattr(base, "name", ""))
        elif xtr.is_executor(comp, tm):
            parent = DEFAULT_EXECUTOR
        else:
            parent = "object"
        lines = [f"class {name}({parent}):"]
        lines += reg._docstring(getattr(comp, "doc", None), "    ") or [
            f'    """Programming API for `{getattr(comp, "name", name)}`."""']

        # -- __init__: the effective constructor's signature ----------------
        ctor = model.ctor(comp)
        cparams = [mangle(a.arg) for a in (ctor.args.args if ctor else [])]
        lines += ["", "    def __init__(" + ", ".join(
            ["self", f"{IMPORTS_PARAM}: {impapi.import_api_name(model)}"]
            + cparams) + "):",
            f"        self.{IMPORTS_ATTR} = {IMPORTS_PARAM}",
            f"        self.{CONSTRUCT_METHOD}()"]
        if ctor is not None:
            lines.append(f"        self.{CTOR_METHOD}({', '.join(cparams)})")
        if model.is_root(comp) and model.initializes(comp):
            lines.append(f"        self.{INIT_METHOD}()")
        if model.is_root(comp) and xtr.has_executors(model):
            lines.append(f"        self.{BIND_METHOD}("
                         f"{DEFAULT_EXECUTOR}(self.{IMPORTS_ATTR}))")

        # -- _pss_construct: this class's own state -------------------------
        lines += ["", f"    def {CONSTRUCT_METHOD}(self):"]
        if base is not None:
            lines.append(f"        super().{CONSTRUCT_METHOD}()")
        else:
            if xtr.has_executors(model):
                own = "self" if xtr.is_executor(comp, tm) else "None"
                lines.append(f"        self.{EXECUTOR_ATTR} = {own}")
        lines += self._bind_groups(view, None)
        for f in channel_fields(view):
            self._check_channel(f)
            arg = (f"self.{IMPORTS_ATTR}.event"
                   if self.await_style == "async" else "")
            lines.append(f"        self.{mangle(f.name)} = Chan1({arg})")
        storage = self._storage_map(model, comp)
        init = self._initializer_emitter(comp, model)
        for f in self._data_members(view):
            lines.append(f"        self.{storage.get(f.name, mangle(f.name))}"
                         f" = {self._initial_value(f, init)}")
        for sub in sub_components(view):
            lines.append(f"        self.{mangle(sub.name)} = "
                         f"{self._sub_storage(sub, model)}")
        if lines[-1] == f"    def {CONSTRUCT_METHOD}(self):":
            lines.append("        pass")

        # -- _pss_ctor: the constructor this class declares -----------------
        own_ctor = next((f for f in own_fns
                         if model.func_kind(f) is FuncKind.CONSTRUCTOR), None)
        if own_ctor is not None:
            lines += ["", "    def " + CTOR_METHOD + "(" + ", ".join(
                ["self"] + [mangle(a.arg) for a in own_ctor.args.args])
                + "):"]
            # Every group it has, inherited ones included: a constructor that
            # shadows its base's does not run it (C++ binds the same way).
            body: List[str] = self._bind_groups(comp,
                                                self._addr_arg(own_ctor))
            be = self._emitter(self.ctor_emitter_cls, own_ctor, comp, model)
            body += be.stmts(own_ctor.body, 2)
            lines += body or ["        pass"]
        elif base is not None and ctor is not None and \
                self._bind_groups(view, None):
            # The constructor is the base's; this class binds its own groups
            # after it.
            params = [mangle(a.arg) for a in ctor.args.args]
            lines += ["", "    def " + CTOR_METHOD + "(" + ", ".join(
                ["self"] + params) + "):",
                f"        super().{CTOR_METHOD}({', '.join(params)})"]
            lines += self._bind_groups(view, self._addr_arg(ctor))

        # -- init blocks: one method per kind this class declares -----------
        for kind in INIT_EXEC_KINDS:
            blocks = [f for f in own_fns if exec_kind(f) == kind]
            if not blocks:
                continue
            body = []
            for fn in blocks:
                body += self._init_body(fn, comp, model)
            lines += ["", f"    def {init_kind_method(kind)}(self):"] + body
            if not any(l.strip() and not l.strip().startswith("#")
                       for l in body):
                lines.append("        pass")
        if model.initializes(comp):
            lines += ["", f"    def {INIT_METHOD}(self):"]
            if model.init_blocks(comp, "init_down"):
                lines.append(f"        self.{init_kind_method('init_down')}()")
            for sub in sub_components(comp):
                if not model.initializes(sub.dtype):
                    continue
                if self._sub_count(sub) is not None:
                    lines += [f"        for _c in self.{mangle(sub.name)}:",
                              f"            _c.{INIT_METHOD}()"]
                else:
                    lines.append(
                        f"        self.{mangle(sub.name)}.{INIT_METHOD}()")
            if model.init_blocks(comp, "init_up"):
                lines.append(f"        self.{init_kind_method('init_up')}()")
            if lines[-1].endswith(f"def {INIT_METHOD}(self):"):
                lines.append("        pass")
        bind = self.emit_bind(node, model)
        if bind:
            lines += [""] + bind

        # -- register accessors and sub-component access: its own ----------
        groups = {f.name for f in own_fields if field_is_reg_group(f)}
        accs = reg.lower_accessors(comp, self.await_style,
                                   desc=self._delegated_desc(model),
                                   groups=groups)
        if accs:
            lines += ["", "    # ----- Register accessors, offsets folded. -----"]
            lines += accs
        subs = self.emit_sub_accessors(view)
        if subs:
            lines += [""] + subs

        # -- a field declared by a base and a derived class: a property -----
        for f in self._data_members(view):
            attr = storage.get(f.name, mangle(f.name))
            if attr == mangle(f.name):
                continue
            n = mangle(f.name)
            lines += ["", "    @property",
                      f"    def {n}(self):",
                      f"        return self.{attr}",
                      "", f"    @{n}.setter",
                      f"    def {n}(self, value):",
                      f"        self.{attr} = value"]

        # -- operations: those it declares ----------------------------------
        for fn in own_fns:
            if model.func_kind(fn) is FuncKind.EXPORT_OP:
                lines.append("")
                lines += self.emit_operation(fn, comp, model)
        for entry in model.entries_of(comp):
            lines.append("")
            lines += self.emit_entry(entry, comp, model)
        return "\n".join(lines)

    def emit_entry(self, entry, comp, model) -> List[str]:
        """An exported action, as a method of the component it runs in.

        No parameters and no result (`export_action.py`). Its body is the
        action's `exec body` in the component's scope, so it renders exactly
        as an operation does.
        """
        lines = self.emit_operation(entry.function, comp, model)
        lines.insert(1, f'        """Exported action `{entry.action}`."""')
        # The base actions' bodies its `super;` reaches, each a method of its
        # own so that its locals and a `return` stay its own.
        for fn in entry.supers:
            sup = self.emit_operation(fn, comp, model)
            sup.insert(1, f'        """`super;` of `{entry.action}`: the body '
                          f'of `{fn.metadata["super_of"]}`."""')
            lines += [""] + sup
        return lines

    def emit_constructor(self, node, model) -> List[str]:
        """`__init__`: the import API, the base, the model's fields, the body.

        The base is bound HERE, from the constructor's first address-typed
        argument, and the model's own `regs.set_handle(...)` may then override
        it from the body below. Both exist because models legitimately do it
        both ways: the bundled example declares an empty constructor and says
        the binding is the generator's job, the WB DMA model states it itself,
        and emitting only one of the two breaks a working model.

        The first ADDRESS-TYPED argument, not the first argument:
        `initialize(int id, addr_handle_t bank)` would otherwise bind the base
        to the channel number.
        """
        comp = node.dtype
        ctor = model.ctor(comp)
        # ANNOTATED, and on every component class rather than only the root: a
        # sub-component is constructed with its parent's object, so the type it
        # takes is the same type. `_sub_ctor_call` passes `self._imports`
        # straight down, and an unannotated child would be the one place a
        # checker stopped following the seam.
        params = ["self", f"{IMPORTS_PARAM}: {impapi.import_api_name(model)}"] \
            + [mangle(a.arg) for a in (ctor.args.args if ctor else [])]
        lines = [f"    def __init__({', '.join(params)}):",
                 f"        self.{IMPORTS_ATTR} = {IMPORTS_PARAM}"]
        if xtr.has_executors(model):
            # Recorded by `set_executor` in an init block, and resolved over
            # the tree afterwards (`emit_bind`). An executor is its own.
            own = "self" if xtr.is_executor(comp, self._tm(model)) else "None"
            lines.append(f"        self.{EXECUTOR_ATTR} = {own}")
        # Each register group bound to the constructor's address, which the
        # body may rebind. Nothing to bind to leaves it at 0: every accessor
        # then offsets from 0, visibly wrong in a trace rather than quietly.
        lines += self._bind_groups(comp, self._addr_arg(ctor))
        for f in channel_fields(comp):
            self._check_channel(f)
            # The async channel is built on the PLATFORM's event, not on one
            # this module picks. That is what lets the same generated model run
            # under `asyncio.run()` and inside a cocotb simulation -- see
            # `share/py/pssc_rt_async.py`.
            arg = (f"{IMPORTS_PARAM}.event" if self.await_style == "async"
                   else "")
            lines.append(f"        self.{mangle(f.name)} = Chan1({arg})")
        init = self._initializer_emitter(comp, model)
        for f in self._data_members(comp):
            lines.append(f"        self.{mangle(f.name)} = "
                         f"{self._initial_value(f, init)}")
        for sub in sub_components(comp):
            lines.append(f"        self.{mangle(sub.name)} = "
                         f"{self._sub_storage(sub, model)}")
        if ctor is not None:
            be = self.ctor_emitter_cls(ctor, comp, model,
                                       imports=model.imports,
                                       ctor_names=model.ctor_names,
                                       await_style=self.await_style,
                                       pkg_async=self._pkg_async(model))
            lines += be.stmts(ctor.body, 2)
        if model.is_root(comp) and model.initializes(comp):
            # The tree is built; initialize it (`emit_init`).
            lines.append(f"        self.{INIT_METHOD}()")
        if model.is_root(comp) and xtr.has_executors(model):
            # Initialized; every `set_executor` has run. Resolve the rest.
            lines.append(f"        self.{BIND_METHOD}("
                         f"{DEFAULT_EXECUTOR}(self.{IMPORTS_ATTR}))")
        return lines

    def emit_bind(self, node, model) -> List[str]:
        """`_pss_bind`: this component's executor, then its subtree's.

        LRM 21.7.2.6: a component that assigned no executor gets its parent's,
        and one with no executor anywhere above it gets the default -- the
        platform. Run once, by the root, after `_pss_init`: an `init_up` may
        assign an executor after the children's blocks have run, so the
        answer is not known until the whole pass is over.
        """
        if not xtr.has_executors(model):
            return []
        comp = node.dtype
        lines = [f"    def {BIND_METHOD}(self, inherited):",
                 f"        if self.{EXECUTOR_ATTR} is None:",
                 f"            self.{EXECUTOR_ATTR} = inherited"]
        for sub in sub_components(comp):
            # A child whose constructor the model never called is still None
            # (`_sub_storage`): it has nothing to run, so nothing to bind.
            built = model.ctor(sub.dtype) is None
            call = f"{BIND_METHOD}(self.{EXECUTOR_ATTR})"
            if self._sub_count(sub) is not None:
                lines.append(f"        for _c in self.{mangle(sub.name)}:")
                lines += ([f"            _c.{call}"] if built else
                          ["            if _c is not None:",
                           f"                _c.{call}"])
            elif built:
                lines.append(f"        self.{mangle(sub.name)}.{call}")
            else:
                lines += [f"        if self.{mangle(sub.name)} is not None:",
                          f"            self.{mangle(sub.name)}.{call}"]
        return lines

    @staticmethod
    def _tm(model):
        return getattr(getattr(model, "ctx", None), "type_map", None) or {}

    def _delegated_desc(self, model):
        """The descriptor a register accessor passes to its executor, or None
        for a model without executors (the accessors call the seam)."""
        if not xtr.has_executors(model):
            return None
        tm = self._tm(model)
        return lp.default_value(xtr.mem_access_desc(tm), tm)

    def emit_init(self, node, model) -> List[str]:
        """`_pss_init`: this component's `exec init_down`, then each
        sub-component's `_pss_init`, then its `exec init_up`.

        That is the order LRM 20.1.2 requires -- a parent's init_down before
        any child's, a parent's init_up after every child's -- walked depth
        first, which 20.1.3 names as one of the legal orders. Sub-components go
        in declaration order and array elements by index; the LRM leaves
        siblings unordered, and a fixed order is one answer it allows.

        A separate pass rather than the tail of `__init__`, because a parent's
        init_down writes into its children (`s.a = 1`) and a child's runs after
        that: the whole tree has to exist before either does. The ROOT's
        constructor starts the pass once its tree is built.

        Empty -- no method -- for a subtree that declares no init blocks.
        """
        comp = node.dtype
        if not model.initializes(comp):
            return []
        lines = [f"    def {INIT_METHOD}(self):"]
        body: List[str] = []
        for fn in model.init_blocks(comp, "init_down"):
            body += self._init_body(fn, comp, model)
        for sub in sub_components(comp):
            if not model.initializes(sub.dtype):
                continue
            if self._sub_count(sub) is not None:
                body += [f"        for _c in self.{mangle(sub.name)}:",
                         f"            _c.{INIT_METHOD}()"]
            else:
                body.append(f"        self.{mangle(sub.name)}.{INIT_METHOD}()")
        for fn in model.init_blocks(comp, "init_up"):
            body += self._init_body(fn, comp, model)
        return lines + (body or ["        pass"])

    def emit_super_block(self, fn, comp, model) -> List[str]:
        """A base component's exec block, as the method `super;` calls."""
        return ([f"    def {mangle(fn.name)}(self):",
                 f'        """`super;`: `exec {exec_kind(fn)}` of '
                 f'`{fn.metadata.get("inherited_from", "?")}`."""']
                + self._init_body(fn, comp, model)[1:])

    def _init_body(self, fn, comp, model) -> List[str]:
        """An init block's statements. Solve context, like the constructor's
        body, so the constructor's emitter renders them."""
        be = self._emitter(self.ctor_emitter_cls, fn, comp, model)
        return [f"        # exec {exec_kind(fn)}"] + be.stmts(fn.body, 2)

    def emit_operation(self, fn, comp, model) -> List[str]:
        """One exported operation, as a method.

        THE seam a mid-weight extension wraps to put a trace call, a lock or a
        prologue around every generated operation.
        """
        params = ["self"] + [mangle(a.arg) for a in fn.args.args]
        # EVERY exported operation is coloured in the async form, not only the
        # ones whose bodies happen to reach the seam. A PSS `target function`
        # is a thing that may consume time; whether this particular one does
        # today is a property of its body, and a caller writing `await
        # dut.arm()` should not have to re-check that when the body changes.
        # It is also the requirement stated for the two APIs: a target function
        # is async in the export API exactly when it is in the import API.
        lines = [f"    {def_kw(self.await_style)} "
                 f"{mangle(fn.name)}({', '.join(params)}):"]
        lines += reg._docstring(getattr(fn, "doc", None), "        ")
        be = self._emitter(self.body_emitter_cls, fn, comp, model)
        lines += be.emit(fn.body, 2)
        return lines

    def _emitter(self, cls, fn, comp, model):
        """An emitter for *fn* in *comp*; configured for native inheritance
        when *comp* is in a hierarchy (`emit_hier_component`)."""
        be = cls(fn, comp, model, imports=model.imports,
                 ctor_names=model.ctor_names, await_style=self.await_style,
                 pkg_async=self._pkg_async(model))
        if ci.in_hierarchy(model.ctx, comp):
            base = self._declared(model, comp)[0]
            be.native = True
            be.super_base = base
            be.field_storage = self._storage_map(model, comp)
            be.super_storage = (self._storage_map(model, base)
                                if base is not None else {})
        return be

    def emit_sub_accessors(self, comp) -> List[str]:
        """Named access to a sub-component array, and its size.

        The list is a plain attribute already, so this is one method, not a
        wrapper layer: `dut.ch(2)` reads better than `dut.ch[2]` at a call site
        that also says `dut.ch_size()`, and it is the spelling the C and C++
        APIs use.
        """
        out: List[str] = []
        for sub in sub_components(comp):
            n = self._sub_count(sub)
            if n is None:
                continue
            out += [
                "    # ----- Sub-component access. -----",
                f"    def {mangle(sub.name)}_size(self):",
                f"        return {n}",
                f"    def {mangle(sub.name)}_at(self, i):",
                f"        return self.{mangle(sub.name)}[i]",
            ]
        return out

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _addr_arg(ctor):
        """The constructor's first address-handle argument, or ``None``."""
        if ctor is None:
            return None
        for a in ctor.args.args:
            dt = a.annotation
            cn = _dt_name(dt)
            if cn == "DataTypeChandle":
                return mangle(a.arg)
            if cn == _DT_STRUCT and \
                    (getattr(dt, "name", "") or "").split("::")[-1] == "addr_handle_t":
                return mangle(a.arg)
        return None

    @staticmethod
    def _bind_groups(comp, addr) -> List[str]:
        """`self._pss_base_<g> = <addr>` for each register group of *comp*."""
        return [f"        self.{group_base(f.name)} = "
                f"{addr if addr is not None else 0}"
                for f in getattr(comp, "fields", None) or []
                if field_is_reg_group(f)]

    @staticmethod
    def _data_members(comp) -> List[Any]:
        """Component fields that become instance attributes.

        Register groups are excluded because they have no runtime
        representation -- an access folds to a constant offset from its
        group's base (`naming.group_base`), which is the whole reason a
        generated model carries no register objects.
        Channels and sub-components are excluded because the constructor builds
        them itself, above.

        What is left is the component's actual state, and it MUST be here:
        without it `self.caps` has nowhere to resolve to and the first operation
        that reads it raises AttributeError.
        """
        subs = {s.name for s in sub_components(comp)}
        chans = {f.name for f in channel_fields(comp)}
        return [f for f in getattr(comp, "fields", [])
                if f.name not in subs and f.name not in chans
                and not field_is_reg_group(f)]

    def _initial_value(self, f, be) -> str:
        """A field's initial value, defaults included, rendered by *be*.

        A default is part of a field's MEANING, not a convenience:
        `wb_dma_ch_caps_s` declares every capability true, and a model that read
        back all-false would silently refuse the operations those capabilities
        gate -- a driver reporting that the device cannot do things it can.
        A struct's own field defaults live in its class (`_pss_defaults`), so a
        struct-typed field is simply a new value of it.

        An initializer is an assignment (LRM 8.7.2): `bit[8] x = 300;` holds
        44, and `int x = -1;` is an expression, folded like any other.
        """
        iv = getattr(f, "initial_value", None)
        if iv is not None:
            return self._init_expr(be, iv, f.datatype)
        if _dt_name(f.datatype) == "DataTypeRef" \
                and be.types.resolve(f.datatype) is f.datatype:
            # Typed by a template parameter the IR does not specialize --
            # `addr_region_s<TRAIT>.trait`. There is no type to make a value
            # of; `None` says so, loudly, to anything that reads through it.
            return "None"
        return lp.default_value(f.datatype, be.types._tm)

    @staticmethod
    def _init_expr(be, e, dtype) -> str:
        """One initializer, converted to *dtype*."""
        if _dt_name(e) == "ExprRefUnresolved":
            return mangle(e.name)      # a package-scope constant
        return be.convert_to(e, be.types.of_datatype(dtype))

    @staticmethod
    def _sub_count(sub):
        """How many instances of a sub-component member there are, or ``None``.

        ``None`` means a SCALAR instance, not an unknown count: `SubComp.size`
        is None exactly when the field is not an array.
        """
        return sub.size

    def _sub_storage(self, sub, model) -> str:
        """Storage for a sub-component member: a list for an array, else one.

        A sub-component whose type declares a CONSTRUCTOR starts as `None`,
        because the model's own body below is what builds it, with arguments
        only the model knows. A member the model never constructs stays `None`,
        which fails loudly at the first use -- unlike C, where the embedded
        storage is simply zeroed and an unconstructed child reads as base 0.

        A type with NO constructor gives the model nothing to call, and a PSS
        component instance exists whether or not anything constructs it -- so
        it is built here, with no arguments.
        """
        n = self._sub_count(sub)
        if model.ctor(sub.dtype) is None:
            obj = (f"{class_name(getattr(sub.dtype, 'name', ''))}"
                   f"(self.{IMPORTS_ATTR})")
            return (f"[{obj} for _ in range({n})]" if n is not None else obj)
        return f"[None] * {n}" if n is not None else "None"

    @staticmethod
    def _check_channel(f) -> None:
        dt = f.datatype
        depth = int(getattr(dt, "depth", 1) or 1)
        if depth != 1:
            raise ValueError(
                f"channel '{f.name}' has depth {depth}; this target implements "
                f"only depth-1 channels (share/py/pssc_rt.py). A deeper channel "
                f"is a ring buffer, which is a different type per capacity and "
                f"which nothing in scope declares.")

    # -- disk ----------------------------------------------------------------

    def generate(self, model) -> List[Path]:
        """Write the module. The ONE method here that touches disk."""
        model.out_dir.mkdir(parents=True, exist_ok=True)
        path = model.out_dir / self.file_name(model)
        path.write_text(self.emit_module(model))
        return [path]
