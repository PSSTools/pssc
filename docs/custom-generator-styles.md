# Adding a custom generator style

`pssc` discovers output styles and targets from two Python entry-point groups,
**`pssc.styles`** and **`pssc.targets`**. Your code lives in your own package,
installs alongside `pssc`, and appears in `pssc targets` and in dv-flow exactly
the way the built-ins do. You do not fork `pssc`, and you do not patch its
source.

Pick the smallest level that does what you need.

| Level | You want | You write | You inherit |
| --- | --- | --- | --- |
| **1** | what `op-model-c` generates, spelled your way | a `StylePolicy` subclass (~10–60 lines) | every semantic fix the C backend gets |
| **2** | a section added, an operation wrapped, an extra artifact | a backend subclass + a target (~40–120 lines) | the whole C emitter, its options, its legality |
| **3** | your own output shape over pssc's model | an `OpModelTarget` subclass with an `emit` | parse, translate, walk, offset fold, legality gate |
| **4** | a language pssc does not emit | a target plus a `BodyWalker` subclass | the statement walk, comment attachment, call dispatch |

Every code block below whose first line is a `#` path is **quoted verbatim from
a file in this repository**, and every one of those files is exercised by
pssc's own test suite. `tests/unit/test_doc_examples.py` fails if a block here
and its source drift apart, because an extension guide whose examples are not
run rots within two releases — and a rotted guide costs a reader a day before
they conclude the docs are wrong.

---

## Level 1 — restyle an existing language

Use this when you want what `op-model-c` already generates, spelled your way:
your symbol convention, your banner, your include order — or your organisation's
mandated register macros.

A house style is a class and an entry point, in a package that does not own the
backend. Here is a complete one:

```python
# tests/plugins/pssc_fixture_plugin/pssc_fixture_plugin/style.py
from pssc.targets.c.style import CStylePolicy


class AcmeStyle(CStylePolicy):
    name = "acme"
    description = "ACME house style: ACME_ prefixes and ACME_RD/WR macros"

    def symbol(self, comp_prefix, name):
        return f"acme_{comp_prefix}_{name}"

    def reg_accessor_form(self):
        return "macro"

    def render_reg_read(self, acc, handle, idx_args, raw):
        return f"ACME_RD{acc.prim}({acc.base}_addr({handle}{idx_args}))"

    def render_reg_write(self, acc, handle, idx_args, value, raw):
        return (f"ACME_WR{acc.prim}({acc.base}_addr({handle}{idx_args}), "
                f"{value})")

    def render_reg_masked_write(self, acc, handle, idx_args, mask, val):
        addr = f"{acc.base}_addr({handle}{idx_args})"
        cur = f"ACME_RD{acc.prim}({addr})"
        return f"ACME_WR{acc.prim}({addr}, ({cur} & ~{mask}) | ({val} & {mask}))"

    def render_mem_read(self, width, bus, addr):
        return f"ACME_RD{width}({addr})"

    def render_mem_write(self, width, bus, addr, value):
        return f"ACME_WR{width}({addr}, {value})"

    def seam_headers(self, link_style):
        return ()               # ACME supplies its own; copy none of pssc's

    def include_order(self, model, s):
        return ["#include <stdint.h>", '#include "acme_regs.h"']
```

Advertise it:

```toml
[project.entry-points."pssc.styles"]
"op-model-c:acme" = "acme_pssc.style:AcmeStyle"
```

What binds the style is the **class**: the registry is keyed
`(style.target, style.name)`, and `CStylePolicy` sets `target = "op-model-c"`
for you. The entry-point name is not read — spelling it `"<target>:<style>"` is
a convention that makes the metadata legible, and nothing more. The registry is
keyed by target because two packages may both want a style called `acme`, and
an SV policy and a C policy share no method contract: handing one to the wrong
backend fails as an `AttributeError` several hundred lines into a generator,
which is the least diagnosable shape a configuration error takes.

Then:

```bash
pssc compile --target op-model-c --style acme --root wb_dma_c -o gen/ *.pss
```

Anything the policy does not override keeps pssc's spelling, and you get every
semantic fix the C backend receives, because this is not a fork.

### What a policy may decide, and what it may not

A backend has two kinds of decision in it. What the generated code **means** —
which registers exist, what address each sits at, what a masked write does — is
the model's. What it **looks like** is a convention, and that is the whole of
what a policy controls. Three rules are enforced rather than documented:

* **Addresses stay pssc's.** They come from the folded PSS offset functions
  (`targets/reg_layout.py`). A policy renders the *access*; it never computes
  the address. A policy that returns a rendering with a different address is
  caught by `test_policy_cannot_alter_address`.
* **Access direction is honoured.** A `READONLY` register is never handed to
  `render_reg_write`. The direction is the model's statement about the device,
  and a rendering for a direction the register does not have is worse than no
  rendering at all (`test_readonly_register_never_written`).
* **A masked write keeps its read.** `write_field`/`write_masked` are defined by
  PSS 3.1 §21.14.1 as read-modify-write, and on a status CSR that read has side
  effects. A `render_reg_masked_write` that drops it is refused
  (`test_masked_write_read_cannot_be_dropped`) — because what it silently
  produces is a write that clears every bit outside the mask.

`reg_accessor_form() == "macro"` means no accessor block is emitted at all and
every access in a body renders through your `render_reg_*` hooks. That is the
point: a house macro mandate is not satisfied by generated code that calls a
generated inline that calls the macro, because what a lint rule and a reviewer
read is the body.

Returning `()` from `seam_headers` says "this style supplies the memory
*mechanism*, not just its spelling". Everything downstream reads that one
answer: no seam headers are copied, no `pssc_bus` macro is emitted, and the
combination with `--link-style vtable` is refused at start-up — that seam
reaches the bus through a per-instance function-pointer struct, which is a
third mechanism.

`enum_item(enum_name, item)` names an enumerator. PSS scopes an item by its
enum; C has one file-scope namespace, so the default prefixes each item with
its enum's name (`op_e { RD }` → `OP_RD`). A style whose models keep their
items unique by convention may return `item` unchanged. Two items that then
share a name are refused before any file is written, naming both enums, not
left to the C compiler.

---

## Level 2 — override parts of an existing backend

Use this when a policy hook cannot express what you need, but you still want
everything else `op-model-c` does — and every fix it gets in future. You
subclass the backend, override a few **published** methods, and register the
result as your own target.

The four override kinds, one each:

```python
# tests/plugins/pssc_fixture_plugin/pssc_fixture_plugin/backend.py
class AcmeBackend(COpModelBackend):
    """The four override kinds, one each."""

    style_cls = AcmeHouseStyle

    #: 1. An inserted section. `insert_after` raises if `banner` is ever
    #: renamed upstream, so this cannot silently stop taking effect.
    def header_sections(self, model, s):
        return insert_after(
            super().header_sections(model, s), "banner",
            Section("acme_compliance", self.emit_compliance))

    def emit_compliance(self, model, s):
        return ["/* ACME-INTERNAL. Generated; see PLM-4417 before editing. */",
                ""]

    #: 2. A wrapped operation: a trace call inside every generated function,
    #: which a style policy cannot add and a body emitter never sees (it is
    #: not a statement of the model's).
    def emit_operation(self, fn, ctx):
        lines = super().emit_operation(fn, ctx)
        opened = next(i for i, ln in enumerate(lines) if ln.endswith(" {"))
        trace = f'    ACME_TRACE("{ctx.prefix}", "{fn.name}");'
        return lines[:opened + 1] + [trace] + lines[opened + 1:]

    #: 3. An extra file, written by the same call that writes the API and
    #: reported to the build system in the same list.
    def emit_extra_files(self, model, s):
        lines = [f"/* ACME register map for {s.prefix}. Generated. */",
                 "#pragma once"]
        for comp in self.comps:
            for group in getattr(comp, "fields", None) or []:
                name = getattr(group, "name", None)
                if name:
                    lines.append(f"/*   {self.prefixes[comp]}.{name} */")
        return {f"{s.prefix}_acme_map.h": "\n".join(lines) + "\n"}
```

The fourth kind is `style_cls` above: a tier-A policy named by the backend
rather than published as an entry point. That is what a house backend wants —
the policy is not one of several the user picks between, it is what this target
*is*.

Every emitting method **returns text and writes nothing**, which is what lets
you call `super()` and wrap the result. `emit_extra_files` returns its files as
a `{name: text}` mapping; only `generate`/`write_files` touch disk.

The target is four lines:

```python
# tests/plugins/pssc_fixture_plugin/pssc_fixture_plugin/backend.py
class AcmeCTarget(CProgSeqTarget):
    """The target. Everything not stated here -- every option, every legality
    entry, every capability flag, every style -- comes from `op-model-c`."""

    name = "op-model-acme-c"
    description = "ACME house C API (tier-B extension of op-model-c)"
    derives_from = "op-model-c"
    backend_cls = AcmeBackend
```

`derives_from` is doing real work, and it is three separate mechanisms wired
separately so that two of them cannot quietly fail together:

* **call legality**, snapshotted at construction — without it a legal `print`
  in a model becomes "no such function";
* **CLI options**, delegated in `add_args` — without it you lose every
  `op-model-c` flag, including `--prefix` and `--link-style`;
* **`target_cfg`**, merged in `resolved_target_cfg` — without it the model sees
  no capability package and takes its own defaults.

Note that `derives_from` names a target **by name, not by class**: the ancestor
may live in another distribution, and deriving must not require importing it.

### What you may override

Only methods marked `@overridable` are contract. Ask:

```bash
pssc targets --overrides op-model-c
```

which lists each published member with its stability (`stable` or
`provisional`), its `since`, and any member it pairs with. Anything not on that
list is private and will change without notice; see
[`extension-stability.md`](extension-stability.md) for what the two stability
levels promise and how a method gets added to the list.

Some members are two halves of one decision — `emit_guard_open` and
`emit_guard_close` agree on a macro name; `emit_api_types` and
`emit_value_unions` partition one set of type declarations. Overriding one and
inheriting the other is refused **at target registration**, naming both, rather
than producing a header that is subtly wrong somewhere the override does not
appear.

If you find yourself reaching for a private name, that is evidence the surface
has a hole in it. Report it — that is a finding, not a style violation.

### Prove what you changed

A tier-B extension has no stable output to freeze in a golden file: upstream
changes it. So the checkable claim is not "my output is this" but "my output is
my baseline's, differing **here** and nowhere else":

```python
# tests/unit/test_plugin_integration.py
    diffs = testing.assert_differs_from_baseline(
        "op-model-acme-c", "op-model-c",
        expect_changed=["acme_compliance", "decls", "impl", "extra_files"])
    assert diffs["dma_engine.h:acme_compliance"] == "added"
    assert diffs["dma_engine_acme_map.h:extra_files"] == "added"
```

`expect_changed` names **sections**, not files. The assertion fails in both
directions, which is the point:

* a difference in a section nobody declared — the extension changed something
  it did not mean to;
* a declared section that did **not** differ — the override stopped taking
  effect (an upstream rename, a signature change, a typo in a section name) and
  the extension is silently generating its baseline. A stale expectation is
  exactly as misleading as a missing one, and only this half catches it.

### A different API shape: a stateless driver layer

The overrides above decorate the object API `op-model-c` generates. Some
platforms want a different SHAPE: no component object, just functions that
each take the register-window bases they use, with every register access in
the platform's own macros. `tests/plugins/pssc_hal_fixture/` is that target,
and every line of it is a published hook. Its model,
`tests/plugins/pssc_hal_fixture/model/dev.pss`, is a device with two windows,
and one of its functions comes out as:

```c
/* Enable the device in `mode`. */
status_e dev_set_mode(uintptr_t ctl_base, uint8_t mode) {
    uint32_t data;
    DEV_READ32_INTO(ctl_base, CTL_MODE, 0, data);
    DEV_FIELD_SET(CTL_MODE, en, (1), data);
    DEV_FIELD_SET(CTL_MODE, mode, (mode), data);
    DEV_WRITE32(ctl_base, CTL_MODE, 0, data);
    return STATUS_OK;
}
```

**The model says what it wants, in annotations** (LRM 7.13). The target
defines what they mean:

```pss
# tests/plugins/pssc_hal_fixture/model/dev.pss
package hal_fx_pkg {
    /** The C prefix of a stateless API's functions. */
    annotation api_s { string prefix = ""; }
    /** The C type of a register-window base parameter. */
    annotation bases_s { string c_type = "uintptr_t"; }
    /** A type the platform declares; the API uses it and never defines it. */
    annotation extern_s { }
}
```

and the model uses them on the root and its constructor:

```pss
# tests/plugins/pssc_hal_fixture/model/dev.pss
@hal_fx_pkg::api_s {.prefix = "dev"}
component dev_c {
    dev_pkg::ctl_c ctl;
    dev_pkg::sts_c sts;

    @hal_fx_pkg::bases_s {.c_type = "uintptr_t"}
    solve function void initialize(addr_handle_t ctl_base,
                                   addr_handle_t sts_base) {
        ctl.set_handle(ctl_base);
        sts.set_handle(sts_base);
    }
```

A target reads one with `pssc.annotations.of(node, "hal_fx_pkg::api_s")`,
which returns the annotation's values (defaults included) or `None`. It
takes only the qualified name the front end resolved, never a spelling, so a
model that imports the package under another name still matches. The
manifest (`--emit-manifest`) reports the same values.

**An operation's C API is three answers**, which the backend gives once and
pssc uses everywhere the operation is declared, defined, called or checked
for a name clash:

```python
# tests/plugins/pssc_hal_fixture/pssc_hal_fixture/backend.py
    def bases(self, fn):
        """`[(group path, parameter)]`: the windows ``fn`` takes."""
        return self.model.unpacked_signature(fn)

    def handle_param(self, fn, ctx):
        return None

    def signature(self, fn, ctx):
        ret, _, params = super().signature(fn, ctx)
        m = self.model
        prefix = (annotations.of(m.root, API) or {}).get("prefix") or ctx.prefix
        base_t = (annotations.of(m.ctor(m.root), BASES) or {}).get(
            "c_type", "uintptr_t")
        return (ret, f"{prefix}_{fn.name}",
                [(base_t, p) for _, p in self.bases(fn)] + params)

    def linkage(self, fn, ctx):
        return "extern" if any(fn is e for e in self.model.exports) \
            else "static"
```

`handle_param` returning `None` drops `_self` from the signature and from
every call. A body that still reaches the component's state (a field, a
channel, a sub-component) is then refused at its PSS line, rather than
generating C that names an undeclared `_self`. `OpModel.unpacked_signature(fn)`
is what such an API takes instead: the register groups `fn` uses, directly
or through what it calls, each with the root-constructor parameter that binds
it. `linkage` returning `"static"` keeps an operation out of the header.

**A call passes what the callee's signature now asks for.** `model_call` on
the body emitter renders every call to an operation:

```python
# tests/plugins/pssc_hal_fixture/pssc_hal_fixture/backend.py
    def model_call(self, callee, args, ctx, handle):
        name = ctx.op_api.signature(callee, ctx)[1]
        bases = [p for _, p in ctx.op_api.bases(callee)]
        return f"{name}({', '.join(bases + args)})"
```

**Register accesses** reach the emitter resolved, as a
`reg_layout.RegAccess` (`ra`: the register's `RegAccessor`, the group, the
method, and for a masked write the fields the source named) and its C side
(`lw`: indices and arguments already rendered):

```python
# tests/plugins/pssc_hal_fixture/pssc_hal_fixture/backend.py
    def reg_access(self, ra, lw):
        if ra.method in ("read", "read_val"):
            # Never printed: every read is a statement (`reg_read_into`).
            # pssc still asks for this spelling to find the read in a
            # conversion, so it must be unique to this read.
            return f"DEV_READ32({self._where(ra, lw)})"
        if ra.method == "write_val":
            return f"DEV_WRITE32({self._where(ra, lw)}, {lw.args[0]})"
        raise Refusal(f"'{ra.method}' has no spelling in this API")

    def reg_read_into(self, ra, lw, local):
        return f"DEV_READ32_INTO({self._where(ra, lw)}, {local})"

    def reg_masked_write_inline(self, ra, lw):
        return None         # read, set each field, write: pssc's shape

    partial_field_writes = False    # a part of a field: pssc merges the mask

    def reg_value_local(self, ra, lw):
        return "uint32_t", "data"

    def reg_value_field_set(self, local, fs, value, ra, lw, mask):
        return f"DEV_FIELD_SET({reg_name(ra.accessor)}, {fs.name}, " \
               f"({value}), {local})"

    def reg_value_field_get(self, value, fs, ra, lw):
        return f"DEV_FIELD_GET({reg_name(ra.accessor)}, {fs.name}, {value})"
```

The masked write's shape is pssc's: returning `None` from
`reg_masked_write_inline` composes it as read into a function-top local
(`reg_value_local`), set each named field (`reg_value_field_set`), write the
local back. The read and the write are `reg_read_into`/`reg_write_from`; the
write's default goes through your `reg_access`. You spell the steps; you
cannot drop the read or reorder the bus operations.

**A read that is a statement.** The platform's read is
`DEV_READ32_INTO(base, REG, i, out)`, which has no value, so the target class
sets `reg_read_form = "statement"`. pssc then reads every register into a
local before the statement that uses it, in C's evaluation order: a read
under `&&` only when the left side holds, a loop test's read on every pass,
an else-if's read only when the tests before it failed
(`docs/design/effect-sequencing.md`). Each of those reads is `reg_read_into`,
and a field of one is `reg_value_field_get` of its local:

```c
    while (1) {
        uint32_t _pss_rd0;
        DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd0);
        if (!(DEV_FIELD_GET(STS_STAT, busy, _pss_rd0) == 1)) {
            break;
        }
```

`reg_access` must still spell a read: pssc uses it to find the read inside a
conversion, and never prints it.

**Leaving the object out** is the lifecycle pair, returned empty, and three
sections removed by name (`sections.remove` raises on a name that does not
exist, so an upstream rename cannot silently undo it):

```python
# tests/plugins/pssc_hal_fixture/pssc_hal_fixture/backend.py
    # No object: no lifecycle, no handle struct, no register-map layout.
    def emit_lifecycle_decls(self, node, ctx):
        return []

    def emit_lifecycle(self, node, ctx):
        return []

    def header_sections(self, model, s):
        return remove(remove(super().header_sections(model, s), "reg_map"),
                      "handles")

    def impl_sections(self, model, s):
        return remove(super().impl_sections(model, s), "accessors")

    def regs_text(self, model, s):
        return ""
```

**External types and the includes** are the style's. A type the platform
declares is used and never defined, and its enum items keep their PSS names:

```python
# tests/plugins/pssc_hal_fixture/pssc_hal_fixture/backend.py
    def is_extern_type(self, dtype):
        return annotations.of(dtype, EXTERN) is not None

    def seam_includes(self, model, s):
        # The platform's macros, then the map they read; pssc's seam is
        # not used.
        return ['#include "dev_access.h"', f'#include "{s.prefix}_map.h"']

    def seam_headers(self, link_style):
        return ()           # ...so none is copied
```

The proof is differential, as above, and also DRIVEN: `tests/unit/test_hal_fixture.py`
runs every exported function through `op-model-c`'s output and through the
fixture's against one tracing memory, and requires the same bus trace. A
wrong offset, a lost field, an extra access or a read moved out of its
place fails there, and a snapshot can see none of them. The check is run on broken output too, so it is known
to fail when it should:

```python
# tests/unit/test_hal_fixture.py
@pytest.mark.parametrize("mutate", [
    _edit("dev_map.h", "#define CTL_LEN_STRIDE 0x8u", "#define CTL_LEN_STRIDE 0x4u"),
    _edit("dev.c", "    DEV_FIELD_SET(CTL_MODE, en, (1), data);\n", ""),
    # A loop test read once, before the loop, instead of on each pass.
    _edit("dev.c", "    while (1) {\n"
                   "        uint32_t _pss_rd0;\n"
                   "        DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd0);\n",
          "    uint32_t _pss_rd0;\n"
          "    DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd0);\n"
          "    while (1) {\n"),
    # `a && b` with b read whether or not a holds.
    _edit("dev.c", "    if (_pss_and1) {\n        uint32_t _pss_rd2;",
          "    if (1) {\n        uint32_t _pss_rd2;"),
    # The else-if testing the first read's value instead of reading again.
    _edit("dev.c", "        uint32_t _pss_rd3;\n"
                   "        DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd3);\n"
                   "        if (DEV_FIELD_GET(STS_STAT, count, _pss_rd3) == 0)",
          "        if (DEV_FIELD_GET(STS_STAT, count, _pss_rd0) == 0)"),
], ids=["wrong-stride", "lost-field", "loop-read-once", "and-not-short",
        "else-if-no-read"])
def test_the_trace_sees_what_a_snapshot_cannot(hal, tmp_path, mutate):
    """The check above, run on broken output: it must fail, or it is not
    checking."""
    want, got = _traces(tmp_path, mutate)
    assert got != want
```

---

## Level 3 — a new emitter over pssc's model

Use this when the output shape is your own, so there is nothing to override —
but everything *before* emission is still pssc's. By the time `emit` is called,
parsing, translation, `--root` resolution, the component walk, the offset fold,
the register-group collection and the call-legality gate have all run, and their
results are on the `OpModel` you are handed.

```python
# tests/plugins/pssc_fixture_plugin/pssc_fixture_plugin/listing.py
class ApiListingTarget(OpModelTarget):
    """One line per operation: `component  name(params) -> returns`."""

    name = "api-listing"
    description = "flat listing of the export API (pssc-fixture-plugin)"

    #: Everything a target needs and this file does not state -- the CLI
    #: options, what calls may be lowered, what the execution target can do --
    #: comes from the ancestor. Without it a legal `print` in a model would be
    #: reported as an unknown function by a target that never emits code.
    derives_from = "op-model-c"

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        super().add_args(parser)        # --root, --ctor-name, --emit-manifest
        parser.add_argument(
            "--api-listing-sep", dest="api_listing_sep", default="  ",
            help="api-listing: column separator")

    def emit(self, model: OpModel, opts: argparse.Namespace) -> List[Path]:
        sep = getattr(opts, "api_listing_sep", "  ")
        lines = []
        for node in model.components:           # children first, then parents
            comp = node.dtype
            name = (getattr(comp, "name", "") or "").split("::")[-1]
            for fn in model.operations(comp):
                params = ", ".join(a.arg for a in fn.args.args)
                lines.append(f"{name}{sep}{fn.name}({params})")

        out = model.out_dir / "api_listing.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n")
        return [out]                            # in COMPILATION order
```

Return paths in **compilation order**, not creation order: the list is what a
build system hands the compiler, and dv-flow preserves it.

What the `OpModel` gives you, and why you should not recompute any of it:

| Member | What it is |
| --- | --- |
| `root`, `tree` | the resolved `--root` component and the walked tree |
| `components` | regular components, **children before parents**, siblings in declaration order — the emission order |
| `components_root_first` | the same set, root first, for prefix assignment |
| `operations(comp)`, `ctor(comp)` | the export API, with `--ctor-name` already applied |
| `reg_groups`, `value_structs` | de-duplicated register groups and packed value structs |
| `offset_of`, `base_stride_of` | the **folded** address arithmetic — see below |
| `channels(comp)`, `sub_components(comp)` | the rest of the component's shape |
| `imports` | declared `import target/solve function`s, by PSS name |
| `exports` | the root's exported functions (`export target function f;`) |
| `bindings`, `binding(comp)`, `handle_params()` | which constructor parameter gives each register group its handle, computed once over the whole tree (provisional) |
| `state_uses(fn)`, `unpacked_signature(fn)` | what an operation touches, transitively, and the groups a stateless API takes in place of the handle (provisional) |
| `out_dir` | where to write |

For register addresses specifically, use `pssc.targets.reg_layout` rather than
walking the register tree yourself:

```python
from pssc.targets.reg_layout import collect_accessors

for acc in collect_accessors(comp_dtype):
    ...   # acc.path, acc.const_off, acc.strides, acc.access, acc.prim_bits
```

An address is the one thing in a generated programming API that a golden
snapshot cannot check — it looks equally plausible either way — so two backends
computing offsets two ways is the worst kind of duplication available here.

---

## Level 4 — a new output language

Use this when nothing existing applies. The worked in-tree example is
`op-model-py` (`src/pssc/targets/py_progseq_tgt.py` and `src/pssc/targets/py/`),
which was written *after* the shared layer existed and is the proof that the
layer is sufficient: the walk, the call dispatch, the elaborated model, the
folded register layout and the API-type collection are all consumed rather than
reimplemented, and what is left is the part that is genuinely about Python.

The target itself states what the language can do and what it can lower:

```python
# src/pssc/targets/py_progseq_tgt.py
class PyProgSeqTarget(OpModelTarget):
    name = "op-model-py"
    description = "Python operation-model API generated from a component tree"
    language = "Python"
```

Operation bodies lower through the shared walk. Subclass `BodyWalker` (and, if
you want calls classified for you, `CallDispatch`) and declare the two facts the
walk needs:

```python
# src/pssc/targets/py/lower_progseq.py
class _BodyEmitter(IntSemantics, CallDispatch, BodyWalker):
```

`BodyWalker` is **not** a published surface
([`extension-stability.md`](extension-stability.md) §1): printing is to move
to the be-* emitters, so a language written on it today follows pssc's tree
rather than a promise. The walker owns statement dispatch, nesting, comment
propagation, the write-only-local scan and the channel-output-local scan. You own rendering:
define a hook named after each IR node class (`StmtAnnAssign` →
`stmt_ann_assign`, `ExprRefBottomUp` → `expr_ref_bottom_up`) and the walk finds
it. A node with no hook is a loud error, never text copied through — which is
the defect the legality work exists to eliminate.

`IntSemantics` (`targets/int_semantics.py`) is the third mixin, and the one to
take if your language's integers do not carry PSS widths -- few do. PSS carries
every integer operation out at a width and signedness (LRM 8.7); the mixin
decides where a value must be widened, wrapped or sign-extended, from what it
can prove about each value's range, and asks you only how to spell each of
those (`int_mask_text`, `int_sint_text`, `int_const`, ...). Python and C share
it, which is why `(~p) == q` over `bit[4]` means the same in both.

`CallDispatch` routes each call by its `Disposition` from the legality registry
(`call_reg`, `call_mem`, `call_channel`, `call_model_op`, `call_utility`, …), so
the table that *decides* a call is legal and the table that *renders* it are the
same table and cannot fall out of step.

Three things worth copying from `op-model-py` rather than rediscovering:

* **Choose your comment style** (`comments.HASH` for `#`, `LINE` for `//`,
  `BLOCK` for `/* */`) and the walk carries a model's prose into the output,
  including for a statement that lowers to no lines.
* **Say what you cannot lower, and why.** `op-model-py` marks `format` and
  `urandom` unsupported with a sentence each explaining that the obstacle is a
  deliberate choice, not a missing feature. A model author reading a refusal
  needs to know which it is.
* **Know when something is an option and not a style.** `--py-await
  {sync,async}` looks like a style — it changes how every method is spelled —
  and is deliberately a target OPTION. A style may not change meaning, and this
  does: the async form publishes `HAVE_EVENT_WAIT=true`, so a model with a
  `compile if` on it compiles a different body, and blocking `channel_c.get()`
  moves from refused to rendered. Anything that moves a capability, an address
  or a signature is an option. A style renames and re-lays-out.

---

## Registering

```toml
[project.entry-points."pssc.targets"]
acme = "acme_pssc.targets:register_all"
```

```python
def register_all():
    return [AcmeCppTarget(), AcmePyTarget()]
```

An entry point may resolve to a `Target` subclass, an instance, a callable
returning either, or an iterable of those. Registering several targets from one
entry point means they cannot half-load.

`pip install -e .` your package, then:

```bash
pssc targets
pssc compile -t api-listing --root wb_dma_c -o gen/ $(cat files.f)
```

If your target does not appear, `pssc targets` prints **why**: a plugin that
fails to import is reported on stderr with its distribution name, never silently
dropped, and one broken plugin costs you that plugin rather than the compiler. A
plugin that tries to take a name already registered is refused unless it names
that name in `replaces` — otherwise the failure hands a user generated code from
a backend they never selected.

`PSSC_NO_PLUGINS=1` disables all discovery, for pssc and for every plugin at
once. That is the first thing to try when pssc misbehaves on a machine with
plugins installed: it separates "pssc's bug" from "a plugin's bug" in one
command. (It is an environment variable only; the `--no-plugins` flag the design
sketched was never added.)

---

## Command-line options

All targets share one `compile` parser, so **a plugin's options must be
namespaced** with its target name (`--api-listing-sep`, `dest="api_listing_sep"`).
A collision with an existing option is raised at start-up rather than silently
dropping one of them.

For anything not worth an argparse entry, use the generic passthrough:

```bash
pssc compile -t fixture -X fixture-style=loud --root wb_dma_c -o gen/ *.pss
```

```python
# tests/plugins/pssc_fixture_plugin/pssc_fixture_plugin/__init__.py
        style = self.opt(opts, "fixture-style", default="plain",
                         choices=("plain", "loud"))
```

`Target.opt` validates against `choices` and reports an unknown value with the
list, so a typo in a `-X` is a diagnostic rather than a silent default.

Every op-model target also gets `--emit-manifest FILE` for free: the elaborated
model as JSON, so a consumer never has to parse generated code. The schema is
declared in `pssc.targets.manifest`, which carries its version.

---

## Declaring capabilities to the model

A target publishes what the **execution target** can do; the model reads it with
a plain `compile if` and never learns which tool answered:

```python
# src/pssc/targets/py_progseq_tgt.py
    target_cfg = {
        "HAVE_EVENT_WAIT": False,
        "HAVE_RUNTIME_SOLVER": False,
    }
```

You must supply **every** constant in the current contract — a partial mapping
is an error, because a model that sees the version marker is entitled to
reference all of them. `None` (the default) is the right value for a target
whose capabilities have not been established: publishing a guess is worse than
publishing nothing.

`HAVE_EVENT_WAIT` asks one narrow question: *can a caller suspend until another
party posts an event?* It does not ask whether a caller may spin — every target
can spin. Answer the question that is asked; `pssc targets` prints what each
target claims. Private flags go in `target_cfg_ext` and are `X_`-prefixed by
rule.

---

## Declaring what calls you can lower

If your target can lower something beyond the common set — or explicitly cannot
— register it. The registry is what the pre-emission gate checks *and* what
`CallDispatch` dispatches on, so this is not documentation:

```python
# tests/plugins/pssc_fixture_plugin/pssc_fixture_plugin/__init__.py
        register_extension(self.name, [
            Entry("print", Disposition.UTILITY, BOTH, lrm="21.1.2"),
        ], replace=True)
```

Register from the target's `__init__` rather than at module scope: import
happens once per process, construction happens once per registration, and it is
the second that must stay in step with what the target can render.

You may **add** to the common tier; you may never shrink it. A target that
cannot render a common-tier call is not a style — it is a different backend, and
`assert_common_tier` will say so. To declare a call unsupported, give the reason
in the entry; it is printed to the model author, so make it a sentence they can
act on. `pssc.targets.call_legality` is where the tiers and the dispositions
are declared.

---

## Shipping runtime source

Bundle your own headers or modules and declare them; the base class copies them
beside the generated output and honours `--no-core-copy`:

```python
# src/pssc/targets/py_progseq_tgt.py
    core_lang = "py"

    def core_file_names(self, model, opts) -> List[str]:
```

Set `core_package` to your own distribution's package name and ship
`share/<core_lang>/` as package data — the lookup takes a package rather than
hard-coding pssc's precisely so that a plugin can ship its own runtime.

---

## dv-flow tasks

Ship a `flow.yaml` and a `dv_flow.mgr` entry point; your task bodies reuse
`pssc.dvflow.common.run_build`, which gives you source gathering, the
incremental-build memento and output classification:

```python
from pssc.dvflow.common import run_build

async def AcmePy(ctxt, input):
    return await run_build(ctxt, input, target="acme-py",
                           overrides_from_params=lambda p: {
                               "progseq_root": getattr(p, "root", ""),
                           })
```

If you emit a file extension `pssc` does not know, register it or it will not
appear in any fileset:

```python
from pssc.dvflow.common import register_filetype

register_filetype(".pyi", "pythonSource", is_incdir=False)
```

---

## Testing your style

`pssc.testing` is public and supported. It exists so you do not have to reach
into pssc's own test tree and take a dependency on its private fixtures — which
is what actually happens otherwise, and which turns pssc's internal refactors
into your broken build.

```python
from pssc.testing import (assert_common_tier, assert_deterministic,
                          compile_op_model)


def test_my_target_generates():
    with compile_op_model("api-listing") as out:
        assert out.names == ["api_listing.txt"]


def test_my_target_keeps_the_common_tier():
    assert_common_tier("api-listing")


def test_my_target_is_deterministic():
    assert_deterministic("api-listing")
```

`compile_op_model` runs against a small PSS model shipped as package data:
register groups, a nested register array with an affine offset fold, packed
value structs, read-modify-write, a do-while poll, and a constructor taking an
`addr_handle_t`. It does **not** have a component tree, a channel, an enum or a
declared import — pssc's own suite covers those against the WB DMA model, which
is not shipped. Point `compile_op_model(sources=...)` at a model of your own if
your backend handles those.

The four tests worth having, in the order they catch things:

1. **`assert_common_tier`** — the contract every target is held to. Skipping it
   means a model author sees a bogus "unsupported call" in code that is correct.
2. **`assert_deterministic`** — two compiles, byte-compared. Nondeterminism here
   is not cosmetic: it defeats every incremental build downstream, and it is
   almost always a set or a dict iterated without an order, which means the
   output is also unstable across Python versions.
3. **`conformance.run(target)`** — the behavioural suite pssc's own targets
   pass. It returns a report rather than raising, so assert on it:
   `report = conformance.run("acme-c"); assert report.ok, report`. The pairing
   rule catches mechanical mismatches; only conformance catches an override that
   addresses the wrong register. Note that its `addresses-match-fold` check
   looks for the folded offsets *in the generated text*, so a target that does
   not emit addresses at all — a listing, a manifest-like artifact — will fail
   it legitimately. Assert on the checks that apply to what you emit.
4. **`assert_differs_from_baseline`** — for a tier-B extension only, as above.

A behavioural test matters most for anything that touches an address. A style
that generates plausible but wrong register access is the failure mode with the
worst ratio of "looks fine in review" to "costs a day on hardware", and no
snapshot comparison can see it.

---

## Where to look next

* [`extension-stability.md`](extension-stability.md) — what `stable` and
  `provisional` promise, and how a method joins the override surface
* [`cli.md`](cli.md) — every option of every built-in target
* `docs/design/generator-style-extensions-design.md` — why the seams are where they are
