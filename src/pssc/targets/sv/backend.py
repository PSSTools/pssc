"""Assemble a PSS operation model into a SystemVerilog package: one file per
class.

A class of a package has to be inside it textually -- SV cannot reopen a
package -- so the package file `` `include ``s one `.svh` per class, as UVM
does: the constants, the register model (all of it, one file), the types,
the package functions, each interface class, the component base, each
component class and the factory. `<pkg>.sv` is the only COMPILATION UNIT;
the `.svh` files need its directory as an include directory, and are never
compiled on their own.

Built as a class, for the reasons `c/backend.py` and `py/backend.py` are:

* **Every `emit_*` returns text; only `generate` touches disk.** A method that
  writes cannot be wrapped by a subclass calling `super()`.
* **The package is exactly its `package_sections()`**, in order, each section
  carrying its own trailing blank -- so a subclass changes one by name
  (`targets/sections.py`) rather than by re-deriving the whole file. Each
  section is one include file (`include_name`); a section with nothing in it
  is no file. The package file is exactly its `wrapper_sections()`.

SV has no forward references inside a package, so the section ORDER is part of
the output being correct, not a matter of taste: the constants, the register
model and the types first, the import and context interfaces, then the
component base that holds the import API, then one class per component type
-- each base before what extends it, each child before what contains it --
then the factory that builds the tree.

Like `PyOpModelBackend`, this carries no `@overridable` marks and is NOT a
published surface.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from ..sections import Section
from .constants import SvConstants
from .lower_api_types import collect_api_types, lower_api_types
from .lower_progseq import (_strip_pkg, component_base_name, exp_if_name,
                            emit_adapter, emit_component_base,
                            emit_component_class, emit_context_api,
                            emit_factory, emit_import_api,
                            emit_package_functions, emit_type_api,
                            factory_name, imp_if_name, exp_impl_name)
from .lower_reg_model import lower_register_model
from .reg_field_names import FieldNamer


#: The extension of an include file: text of the package, not a compilation
#: unit (dv-flow classifies it `systemVerilogInclude`).
INCLUDE_EXT = ".svh"

#: A component class's section is named `class:<its SV name>`.
CLASS_SECTION = "class:"


class SvOpModelBackend(object):
    """Turns an `OpModel` into the text of one SystemVerilog package and the
    files it includes."""

    def __init__(self, pkg_name: str = "", reg_fields: str = "named",
                 enum_items: str = "prefixed"):
        #: The package name. Empty means `<root>_pkg`.
        self.pkg_name = pkg_name
        #: `named` or `folded` -- `--sv-reg-fields`. Spelling only: both
        #: generate the same bus traffic (see `progseq_gen.generate`).
        self.reg_fields = reg_fields
        #: `prefixed` or `verbatim` -- `--sv-enum-items`. Spelling only.
        self.enum_items = enum_items
        #: `constants()`'s answer, and the model it is for.
        self._consts = None
        self._consts_model = None

    # -- naming --------------------------------------------------------------

    def package_name(self, model) -> str:
        return self.pkg_name or f"{model.tree.name}_pkg"

    def file_name(self, model) -> str:
        return self.package_name(model) + ".sv"

    # -- assembly ------------------------------------------------------------

    def package_sections(self, model) -> List[Section]:
        """What the package holds, in compilation order: one section per
        include file."""
        return ([
            Section("constants", self.emit_constants),
            Section("reg_model", self.emit_reg_model),
            Section("api_types", self.emit_api_types),
            Section("functions", self.emit_functions),
            Section("import_api", self.emit_import_api),
        ] + [Section(CLASS_SECTION + exp_if_name(a.comp),
                     lambda m, a=a: self.emit_type_api(m, a))
             for a in self._sub_apis(model)]
          + [Section("context_api", self.emit_context_api),
             Section("component_base", self.emit_component_base),
        ] + [Section(CLASS_SECTION + _strip_pkg(comp.name),
                     lambda m, c=comp: self.emit_component(m, c))
             for comp in model.classes]
          + [Section(CLASS_SECTION + exp_impl_name(a.comp),
                     lambda m, a=a: self.emit_adapter(m, a))
             for a in self._sub_apis(model)]
          + [Section("factory", self.emit_factory)])

    @staticmethod
    def _sub_apis(model):
        """The sub-component types on the API (`OpModel.apis`), children
        first: each has an interface class and an adapter."""
        return [a for a in model.apis if a.comp is not model.root]

    def wrapper_sections(self, model) -> List[Section]:
        """The package file, in order. `emit_package` joins exactly these."""
        return [
            Section("banner", self.emit_banner),
            Section("package_open", self.emit_package_open),
            Section("forward_decls", self.emit_forward_decls),
            Section("includes", self.emit_includes),
            Section("package_close", self.emit_package_close),
        ]

    def include_name(self, model, section: str) -> str:
        """The file section ``section`` is written to. A class, an interface
        class and the factory are named after themselves; the rest after the
        root (`wb_dma_c_regs.svh`)."""
        root = _strip_pkg(model.tree.name)
        named = {
            "constants": f"{root}_consts",
            "reg_model": f"{root}_regs",
            "api_types": f"{root}_types",
            "functions": f"{root}_functions",
            "import_api": imp_if_name(model.root),
            "context_api": exp_if_name(model.root),
            "component_base": component_base_name(model.root),
            "factory": factory_name(model.root),
        }
        if section.startswith(CLASS_SECTION):
            stem = section[len(CLASS_SECTION):]
        else:
            stem = named.get(section) or root + "_" + re.sub(r"\W", "_", section)
        return stem + INCLUDE_EXT

    def include_files(self, model) -> List[Tuple[str, str]]:
        """``[(file name, text)]`` of every include file, in the order the
        package includes them. A section that renders nothing is no file."""
        files: List[Tuple[str, str]] = []
        seen: Dict[str, str] = {}
        for sec in self.package_sections(model):
            lines = sec.emit(model)
            if not any(l.strip() for l in lines):
                continue
            name = self.include_name(model, sec.name)
            if name in seen:
                raise ValueError(
                    f"op-model-sv: sections '{seen[name]}' and '{sec.name}' "
                    f"would both be written to '{name}'")
            seen[name] = sec.name
            files.append((name, self.emit_include_file(model, name, lines)))
        return files

    def emit_include_file(self, model, name: str, lines: List[str]) -> str:
        """One include file: a banner, then the section's text."""
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return "\n".join(
            ["// Generated by pssc (sv-progseq) -- do not edit.",
             f"// Included by {self.file_name(model)}; not a compilation unit.",
             ""] + list(lines) + [""])

    def emit_package(self, model) -> str:
        """The package file: its sections' lines, in order."""
        lines: List[str] = []
        for _, emit in self.wrapper_sections(model):
            lines += emit(model)
        return "\n".join(lines)

    def emit_files(self, model) -> List[Tuple[str, str]]:
        """``[(file name, text)]`` of every file: the package, then what it
        includes. Nothing is written."""
        return ([(self.file_name(model), self.emit_package(model))]
                + self.include_files(model))

    def sections(self, model) -> Dict[str, str]:
        """`{"<file>:<section>": text}`. Nothing is written."""
        out = {f"{self.file_name(model)}:{s.name}": "\n".join(s.emit(model))
               for s in self.wrapper_sections(model)}
        for s in self.package_sections(model):
            out[f"{self.include_name(model, s.name)}:{s.name}"] = \
                "\n".join(s.emit(model))
        return out

    # -- shared --------------------------------------------------------------

    def namer(self, model, comp):
        """The field namer for ``comp``'s bodies, or None under `folded`.

        One per component: receiver paths resolve against that component's own
        fields. None disables the rewrite.
        """
        if self.reg_fields != "named":
            return None
        type_map = getattr(model.ctx, "type_map", {}) or {}
        return FieldNamer(comp, type_map)

    def _api_args(self, model):
        return (model.comp_dtypes, model.value_structs, model.ctor_names,
                [fn for e in model.entries for fn in e.functions]
                + list(model.functions),
                getattr(model.ctx, "type_map", None) or {})

    def constants(self, model) -> SvConstants:
        """How this package spells a folded PSS constant
        (`constants.SvConstants`), and which ones it declares.

        Which constants the code references is known by rendering it: every
        other section is rendered once with a recording table, and the table
        returned declares exactly what that rendering asked for. A section
        added by a subclass is covered the same way, as long as it spells its
        constants through this table.
        """
        if self._consts is not None and self._consts_model is model:
            return self._consts
        enums, _ = collect_api_types(*self._api_args(model))
        known = [t for t in (getattr(model.ctx, "type_map", {}) or {}).values()
                 if type(t).__name__ == "DataTypeEnum"]
        rec = SvConstants(enums, known, enum_items=self.enum_items,
                          value_structs=model.value_structs)
        clashes = rec.item_clashes()
        if clashes:
            from ...driver import CompileError
            raise CompileError(
                f"{len(clashes)} enum item name(s) would be declared twice in "
                f"the generated SV package. Rename one in the model, or drop "
                f"--sv-enum-items verbatim", clashes)
        self._consts, self._consts_model = rec, model
        try:
            for name, emit in self.package_sections(model):
                if name != "constants":
                    emit(model)
        except BaseException:
            self._consts = self._consts_model = None
            raise
        self._consts = rec.final()
        return self._consts

    # -- sections ------------------------------------------------------------

    def emit_banner(self, model) -> List[str]:
        return ["// Generated by pssc (sv-progseq) -- do not edit.",
                f"// Root component: {model.tree.name}",
                ""]

    def emit_package_open(self, model) -> List[str]:
        return [f"package {self.package_name(model)};",
                "  import pssc_reg_pkg::*;",
                ""]

    def emit_forward_decls(self, model) -> List[str]:
        """Every component class, declared ahead: a class may then hold a
        handle of another whatever order they are included in."""
        if not model.classes:
            return []
        return ([f"  typedef class {_strip_pkg(c.name)};" for c in model.classes]
                + [""])

    def emit_includes(self, model) -> List[str]:
        """The include files, in compilation order."""
        return [f'  `include "{name}"' for name, _ in self.include_files(model)
                ] + [""]

    def emit_constants(self, model) -> List[str]:
        """A `localparam` for each PSS `static const` the package spells by
        name, before anything that spells it."""
        decls = self.constants(model).declarations()
        if not decls:
            return []
        return ["  // ----- PSS constants the model references -----"] + decls + [""]

    def emit_reg_model(self, model) -> List[str]:
        """Register value structs and group classes, for the whole tree.

        The whole tree, not the root's own fields: a per-channel bank is reached
        through its channel component, so lowering only the root's registers
        emitted an empty file for any model that puts registers where the
        operations that use them are.
        """
        body, _, _ = lower_register_model(model.comp_dtypes)
        return [body]

    def emit_api_types(self, model) -> List[str]:
        text = lower_api_types(*self._api_args(model),
                               consts=self.constants(model))
        return [text] if text else []

    def emit_functions(self, model) -> List[str]:
        """The package-scope functions the model calls, before the classes
        that call them."""
        if not model.functions:
            return []
        return ["  // ----- Package functions -----",
                emit_package_functions(model.functions, model.ctx,
                                       model.ctor_names,
                                       self.constants(model)),
                ""]

    def emit_import_api(self, model) -> List[str]:
        return ["  // ----- Import API: what the platform supplies -----",
                emit_import_api(model.root, model.ctor_names, model.ctx),
                ""]

    def emit_context_api(self, model) -> List[str]:
        """What the platform holds: the root's API and the exported actions
        (`op_api.py`, design D11 as revised, D12)."""
        return ["  // ----- Context API: what the platform calls -----",
                emit_context_api(model),
                ""]

    def emit_type_api(self, model, api) -> List[str]:
        """One sub-component type's API: what the platform reaches through
        an accessor (`op_api.py`)."""
        return [f"  // ----- API: {api.comp.name} -----",
                emit_type_api(model, api),
                ""]

    def emit_adapter(self, model, api) -> List[str]:
        """The class that implements one sub-component type's API on an
        instance."""
        return [f"  // ----- API adapter: {api.comp.name} -----",
                emit_adapter(model, api),
                ""]

    def emit_component_base(self, model) -> List[str]:
        """The class every component class extends (design D8)."""
        return [f"  // ----- Component base: {model.tree.name} -----",
                emit_component_base(model.root),
                ""]

    def emit_component(self, model, comp) -> List[str]:
        """One component type's class. `package_sections` gives each its own
        section, in `model.classes` order: each base before what derives from
        it, each child before what contains it, the root last."""
        return [f"  // ----- Component: {comp.name} -----",
                emit_component_class(model, comp, self.namer(model, comp),
                                     self.constants(model)),
                ""]

    def emit_factory(self, model) -> List[str]:
        return [f"  // ----- Factory and root context: {model.tree.name} -----",
                emit_factory(model),
                ""]

    def emit_package_close(self, model) -> List[str]:
        return ["endpackage", ""]

    # -- disk ----------------------------------------------------------------

    def generate(self, model) -> List[Path]:
        """Write the package and its include files, the package first. The
        ONE method here that touches disk."""
        model.out_dir.mkdir(parents=True, exist_ok=True)
        written: List[Path] = []
        for name, text in self.emit_files(model):
            path = model.out_dir / name
            path.write_text(text)
            written.append(path)
        return written
