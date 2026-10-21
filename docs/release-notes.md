# pssc release notes

## 0.2.0

pssc 0.2.0 includes the bc backend and needs pssparser 3.5 and
zuspec-ir-core 0.3. The entries below are the changes a user of 0.1.0's
op-model targets will notice first; the design notes under `docs/design/`
record the rest.

### For target plugins

- **Source annotations reach the IR.** A PSS annotation on a component,
  action, struct, enum, field, function or exec block is on the IR node as
  `annotations`, with its values computed by pssparser. A plugin reads it with
  `pssc.annotations.of(node, "pkg::ann_s")`, by the qualified name the front
  end resolved. `--emit-manifest` reports annotations on components,
  functions and enums.
- **Model queries for a different API shape.** `OpModel.bindings` gives the
  constructor parameter that binds each register group, computed once over
  the whole model. `OpModel.state_uses(fn)` gives what an operation touches,
  transitively: register groups and registers, component fields, imports,
  memory and unbounded loops. `OpModel.unpacked_signature(fn)` gives the
  group bases a stateless API takes in place of the component handle. A
  reduced masked write carries the fields its source named
  (`reg_rmw.RegWriteProvenance`).
- **op-model-c: an operation's C API is three hooks.** `signature`,
  `handle_param` and `linkage` are asked wherever an operation is declared,
  defined, called or named, so they cannot disagree. `handle_param` returning
  None gives a function with no component handle, and a body that then reaches
  component state is a located error. The lifecycle can be left out
  (`emit_lifecycle_decls`/`emit_lifecycle` returning `[]`).
- **op-model-c: register accesses and calls through `CBodyEmitter`.**
  `reg_access`, `reg_masked_write_inline`, `reg_value_local`,
  `reg_value_field_set`/`_get` and `model_call` are published hooks
  (provisional). A masked write's shape stays pssc's: read, set each field,
  write.
- **op-model-c: external types.** A type the style policy marks external
  (`CStylePolicy.is_extern_type`) is not declared in the generated header,
  and its enum items are checked for clashes with the model's own.
- **New published members:** `COpModelBackend.op_ctx`,
  `reg_layout.RegAccessor.fields`, and `pssc.diag.Refusal` for a located
  refusal raised from a plugin. All are listed in
  `docs/extension-stability.md`.
- **A second worked example.** `tests/plugins/pssc_hal_fixture` turns an
  op-model component into plain C functions that take the register-window
  bases they use. It is held to op-model-c's bus trace, and the extension
  guide's Level 2 walks through it (`docs/custom-generator-styles.md`).

### Register maps and accessors


- **Register members named `_0`, `_1`, ... are ordinary registers.** A
  register-group member is a reserved placeholder only when its name is `_`
  followed by a non-digit (`_reserved0`, `_rsvd`, `_pad`, `_`); an
  underscore before a digit is a name escape, which register generators use
  for indexed series. Such members used to be dropped from every target's
  API while bodies still named them: C on the reg-map seams lost a value
  struct's `.raw`, the vtable seam called undeclared accessors, and SV
  failed only at elaboration. They now get accessors, layout members, SV and
  C++ class members, and manifest entries. The rule has one home,
  `reg_layout.is_reserved_name`.
- **An access to a reserved register member is a compile error** on every
  op-model target, located and naming the path
  (`targets/reserved_access.py`). The C backend no longer guesses an untyped
  access for a register the register walk did not surface.
- **op-model-c: strided register arrays in the register map.** A register
  array whose stride exceeds its width (`FILT[4]` at stride 8) was declared
  `uint32_t FILT[4]`, misplacing every later member. Each element is now a
  struct spanning the stride (`struct { uint32_t v; uint32_t _rsvd[1]; }
  FILT[4];`, `const v` for a READONLY register), with an element-size
  assertion, and the access is `FILT[i].v`. A stride that is not a multiple
  of the width is refused with a location. Packed arrays are unchanged.

- **op-model-c: overlapping register views compile.** A register map whose
  members overlap -- two views of one register block, both at offset 0, two
  widths of one register, a 64-bit register and its halves -- used to fail with `RegMapError: ... a struct
  cannot place two instances at one address`. The C layout struct now
  declares each connected set of overlapping members as an anonymous union
  (`reg_layout.build_reg_map`, `UNION` members), preserving every register's
  name, PSS access path and address, with an `offsetof` assertion per alias.
  The layout walk models C alignment: a union or group that C rounds up gets
  an explicit span or tail pad, so its size is the same on every ABI, and a
  view C would move is refused with a located error naming it. A layout
  refusal is now a `CompileError`, not a traceback. Maps without overlap
  generate byte-identical output (the golden snapshots are unchanged), except
  a group holding a 64-bit register whose size C rounds up: it gains a tail
  pad, where before it failed its own `sizeof` assertion.
