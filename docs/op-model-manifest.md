# The operation-model manifest

`--emit-manifest FILE` writes the elaborated operation model as JSON, beside the
generated code and in the same run:

```bash
pssc compile -t op-model-c --root dma_engine_c --emit-manifest gen/dma.json -o gen/ *.pss
```

It answers "what operations and registers does this API have, and where does
each register sit" for a consumer that is not a compiler: a build system
deciding what to rebuild, a test generator enumerating operations, a
documentation pass, a register-map cross-check against an RDL source, or a
person.

**Why it is not derived from the output.** Every one of those consumers
otherwise parses generated C — and a parser for generated C is a second
implementation of the generator's naming rules that nobody updates. The manifest
is written from the `OpModel`, the same object the emitters render, so it cannot
describe a different API from the one that was emitted. In particular the
register offsets come from the same `reg_layout` walk the accessors are
generated from, so comparing a manifest against a register map compares against
what was *emitted*, not against a second computation that could agree while the
output does not.

It is available on **every** op-model target (`op-model-c`, `op-model-cpp`,
`op-model-sv`, `op-model-py`) because it is implemented on their shared base
class. The `settings` block is the only part that differs by target.

---

## Versioning

Two fields identify the document:

```json
"schema": "pssc.op-model-manifest",
"version": 1
```

`schema` is a name, so a consumer can tell this file from another JSON in the
same directory. `version` is bumped **when a consumer that read the previous
version would be wrong** — a field removed, renamed, or given a different
meaning. Adding a field is not a bump: a reader that ignores it is still
correct. Check both, and fail loudly on an unexpected `version` rather than
guessing.

The document is written with sorted keys, a fixed indent and a trailing newline.
Two compiles of the same input produce byte-identical manifests; a manifest that
differed run to run would defeat every incremental build that reads it, which is
most of the reason to have one.

---

## Top-level fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | string | always `"pssc.op-model-manifest"` |
| `version` | int | schema version (see above) |
| `pssc_version` | string | the pssc that wrote it |
| `target` | string | the target name, e.g. `"op-model-c"` |
| `root` | string | the resolved `--root` component, unqualified |
| `ctor_names` | [string] | which `solve function` names meant "constructor" for this run |
| `settings` | object | the target's ABI-affecting options (see below) |
| `imports` | [function] | declared `import target/solve function`s, sorted by name |
| `value_structs` | [struct] | packed register value structs, de-duplicated |
| `components` | [component] | the walked tree, **children before parents** |
| `files` | [file] | what was produced, **in emission order** |

### `settings`

The options that change the generated API's **shape** — which is what a consumer
holding a stale manifest needs to notice. What counts is the target's own answer
(`OpModelTarget.abi_settings`): a link style or a lifecycle is ABI, a comment
style is not. For `op-model-c`:

```json
"settings": {
  "addr_bits": 64,
  "lifecycle": "malloc",
  "link_style": "vtable",
  "prefix": "dma_engine",
  "reg_style": "bitfields"
}
```

(abridged — the C target reports every knob, at its default or not, so that two
manifests are comparable.) A target that publishes no ABI settings writes `{}`.

### `files`

```json
"files": [
  {"name": "dma_engine.h", "role": "generated"},
  {"name": "dma_engine.c", "role": "generated"},
  {"name": "pssc_mem.h",   "role": "runtime"}
]
```

Order is the order the target returned, which is a **compilation order** for
some targets, so it is recorded rather than sorted. `role` distinguishes what
was generated from this model from what was copied out of pssc's `share/`. The
label comes from the target's own `core_file_names()` — the list it copied
*from*. Deciding by extension would be wrong (the C target copies `pssc_mem.h`
beside a generated `dma_engine.h`), and comparing bytes against the shipped copy
would answer a different question: whether the file happens to match today,
rather than where it came from.

---

## Components

Each entry of `components`:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | component type name, unqualified |
| `is_root` | bool | true for the `--root` component |
| `doc` | string | the PSS doc comment, verbatim (`""` if none) |
| `constructor` | function or `null` | the `solve function` matched by `ctor_names` |
| `operations` | [function] | the export API, in declaration order |
| `sub_components` | [{`name`, `type`, `count`}] | instances; `count` is the array size |
| `channels` | [{`name`, `element`, `depth`}] | declared channels |
| `registers` | [register] | every register reachable from this component |

The constructor is reported **separately from the operations**, because it is
separate in the generated API: it takes the address handle and is called once.

A function is:

```json
{
  "name": "mem_to_mem_copy",
  "returns": "int",
  "params": [{"name": "channel", "type": "int"},
             {"name": "src", "type": "bit[32]"}],
  "doc": "Move `nbytes` bytes from `src` to `dst` ..."
}
```

Types are spelled the way the **model** spells them — `bit[32]` stays `bit[32]`
rather than becoming `uint32_t` in one manifest and `logic [31:0]` in another. A
consumer wanting a C type asks the C target.

One name a reader will look for and not find: `addr_handle_t` reports as
`chandle`, because it is `typedef chandle addr_handle_t` and the typedef name
does not reach the IR. Reporting the typedef would mean this file inventing a
name the model no longer carries.

---

## Registers

```json
{
  "path": ["regs", "channels", "CSR"],
  "offset": 32,
  "strides": [32],
  "bits": 32,
  "access_width": 32,
  "access": "READWRITE",
  "value_struct": "dma_ch_csr_s"
}
```

| Field | Meaning |
| --- | --- |
| `path` | the register-group path from the component to the register |
| `offset` | the **folded** constant byte offset from the component's base |
| `strides` | one byte stride per array dimension in `path`, outermost first |
| `bits` | the register's declared width |
| `access_width` | the bus transaction width it is accessed at (8/16/32/64) |
| `access` | `READWRITE`, `READONLY` or `WRITEONLY` |
| `value_struct` | the packed value struct's name, or `null` for a plain scalar |

The address of one register instance is

    base + offset + sum(index[i] * strides[i])

which is exactly what the generated accessors compute, from the same walk.
`strides` is empty for a register that is not inside an array.

**Reserved registers are absent.** A field whose name begins with `_` holds
address space open and is never surfaced in a generated API; its space is
already accounted for in the following siblings' offsets, so its absence changes
no address. A consumer reconstructing a register map from this file will see
gaps, and the gaps are correct.

---

## Value structs

```json
{
  "name": "dma_ch_sz_s",
  "bits": 32,
  "fields": [{"name": "TOT_SZ", "lsb": 0, "width": 12},
             {"name": "RSVD0", "lsb": 12, "width": 4},
             {"name": "CHK_SZ", "lsb": 16, "width": 9}]
}
```

Fields are listed **LSB-first** with an explicit `lsb`, so a consumer never has
to infer packing order — which is the one thing about a bitfield layout that two
tools reliably disagree about. Reserved *fields* are present here (unlike
reserved registers): they are part of the value's layout, and a consumer packing
a word needs to know the bits exist.

`value_structs` is de-duplicated across the whole model, in first-use order; a
register names its struct by name.

---

## What the manifest does not contain

* **Bodies.** It describes the API, not what the operations do.
* **Actions and constraints.** The operation model is the target-facing
  projection of a PSS model, and by design excludes them on every target.
* **Anything language-specific.** No C types, no generated symbol names. If you
  need the spelling of a generated accessor, the target that generated it is the
  only correct source — and if you find yourself wanting it here, that is a
  request worth making rather than a regex worth writing.
