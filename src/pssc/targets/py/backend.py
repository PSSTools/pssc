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

from ..progseq_model import (_dt_name, channel_fields, field_is_reg_group,
                             sub_components)
from . import lower_api_types as api
from . import lower_import_api as impapi
from . import lower_progseq as lp
from . import lower_reg_model as reg
from .naming import (IMPORTS_ATTR, IMPORTS_PARAM, class_name, def_kw, mangle,
                     module_name)

_DT_STRUCT = "DataTypeStruct"
_DT_ARRAY = "DataTypeArray"


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
        used = impapi.imports_used(self._components_text(model))
        return "\n".join(impapi.lower_import_api(
            model, await_style=self.await_style, used=used))

    def emit_api_types(self, model) -> str:
        return "\n".join(api.lower_api_types(
            model.comp_dtypes_root_first, model.value_structs,
            model.ctor_names)).rstrip()

    def emit_value_types(self, model) -> str:
        return "\n\n\n".join(reg.lower_value_classes(model)).rstrip()

    def emit_components(self, model) -> str:
        """One class per component, CHILDREN FIRST.

        Python resolves a name at call time, so the order is not a requirement
        the way C's is. It is `model.components` anyway, because that is the
        order every other backend emits in and a reader comparing two generated
        languages should not have to account for a difference that means
        nothing.
        """
        return self._components_text(model)

    def _components_text(self, model) -> str:
        key = id(model)
        if key not in self._components_memo:
            self._components_memo[key] = "\n\n\n".join(
                self.emit_component(node, model) for node in model.components)
        return self._components_memo[key]

    # -- one component -------------------------------------------------------

    def emit_component(self, node, model) -> str:
        """One component class: constructor, register accessors, operations."""
        comp = node.dtype
        name = class_name(getattr(comp, "name", ""))
        lines = [f"class {name}(object):"]
        lines += reg._docstring(getattr(comp, "doc", None), "    ") or [
            f'    """Programming API for `{getattr(comp, "name", name)}`."""']
        lines.append("")
        lines += self.emit_constructor(node, model)
        accs = reg.lower_accessors(comp, self.await_style)
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
        return "\n".join(lines)

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
        addr = self._addr_arg(ctor)
        if addr is not None:
            lines.append(f"        self._base = {addr}")
        else:
            # Nothing to bind. 0 is the honest answer: every accessor then
            # offsets from 0, which is visibly wrong in a trace rather than
            # quietly wrong.
            lines.append("        self._base = 0")
        for f in channel_fields(comp):
            self._check_channel(f)
            # The async channel is built on the PLATFORM's event, not on one
            # this module picks. That is what lets the same generated model run
            # under `asyncio.run()` and inside a cocotb simulation -- see
            # `share/py/pssc_rt_async.py`.
            arg = (f"{IMPORTS_PARAM}.event" if self.await_style == "async"
                   else "")
            lines.append(f"        self.{mangle(f.name)} = Chan1({arg})")
        for f in self._data_members(comp):
            lines.append(f"        self.{mangle(f.name)} = "
                         f"{self._initial_value(f)}")
        for sub in sub_components(comp):
            lines.append(f"        self.{mangle(sub.name)} = "
                         f"{self._sub_storage(sub)}")
        if ctor is not None:
            be = self.ctor_emitter_cls(ctor, comp, model,
                                       imports=model.imports,
                                       ctor_names=model.ctor_names,
                                       await_style=self.await_style)
            lines += be.stmts(ctor.body, 2)
        return lines

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
        be = self.body_emitter_cls(fn, comp, model, imports=model.imports,
                                   ctor_names=model.ctor_names,
                                   await_style=self.await_style)
        lines += be.emit(fn.body, 2)
        return lines

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
    def _data_members(comp) -> List[Any]:
        """Component fields that become instance attributes.

        Register groups are excluded because they have no runtime
        representation -- an access folds to a constant offset from `_base`,
        which is the whole reason a generated model carries no register objects.
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

    def _initial_value(self, f) -> str:
        """A field's initial value, defaults included.

        A default is part of a field's MEANING, not a convenience:
        `wb_dma_ch_caps_s` declares every capability true, and a model that read
        back all-false would silently refuse the operations those capabilities
        gate -- a driver reporting that the device cannot do things it can.
        """
        iv = getattr(f, "initial_value", None)
        if iv is not None:
            return self._const_expr(iv)
        dt = f.datatype
        if _dt_name(dt) == _DT_ARRAY:
            n = lp._array_size(dt)
            return f"[0] * {n}" if n is not None else "[]"
        if _dt_name(dt) == _DT_STRUCT:
            nm = (getattr(dt, "name", "") or "").split("::")[-1]
            if nm == "addr_handle_t":
                return "0"
            # A struct-typed attribute carries its defaults on the STRUCT's
            # fields rather than on the instance, so they are passed as keywords
            # rather than walked out into assignments afterwards.
            kw = [f"{sf.name}={self._const_expr(sf.initial_value)}"
                  for sf in (getattr(dt, "fields", []) or [])
                  if getattr(sf, "initial_value", None) is not None]
            return f"{reg.value_class_name(dt)}({', '.join(kw)})"
        return "0"

    @staticmethod
    def _const_expr(e) -> str:
        """A field initializer, which is a compile-time constant by construction."""
        cn = _dt_name(e)
        if cn == "ExprConstant":
            v = e.value
            if isinstance(v, bool):
                return "True" if v else "False"
            if isinstance(v, str):
                return repr(v)
            return str(v)
        if cn == "ExprRefUnresolved":
            return mangle(e.name)
        if cn == "ExprAttribute":
            return mangle(e.attr)      # a package-scope constant
        raise ValueError(f"unsupported field initializer {cn}")

    @staticmethod
    def _sub_count(sub):
        """How many instances of a sub-component member there are, or ``None``.

        ``None`` means a SCALAR instance, not an unknown count: `SubComp.size`
        is None exactly when the field is not an array.
        """
        return sub.size

    def _sub_storage(self, sub) -> str:
        """Storage for a sub-component member: a list for an array, else None.

        `None` rather than an object, because the CONSTRUCTOR is what builds a
        sub-component and it runs from the model's own body below. A member the
        model never constructs stays `None`, which fails loudly at the first use
        -- unlike C, where the embedded storage is simply zeroed and an
        unconstructed child reads as base 0.
        """
        n = self._sub_count(sub)
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
