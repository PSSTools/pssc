# pssc release notes

## 0.3.0

pssc now needs pssparser 3.6. 0.2.0 was never tagged or published, so
0.3.0 is the first release after 0.1.0 on PyPI and also carries the 0.2.0
changes below.

### Output changes

- **op-model-c: named constants are macros.** A body's reference to a
  `static const` or package `const` is written by name, as a macro, instead
  of its value. The macros are defined `#ifndef N` / `#define N (v)` in a new
  `constants` header section, so a `-D` or a header included first can change
  the value. pssc decides nothing from that value: each use is cast to its
  declared type. pssc also uses some values at generation time (an array
  size, a width, a register offset, a template argument, another constant's
  initializer). Such a "pinned" constant is checked with
  `PSSC_PINNED_CONSTANT`, so overriding it is a compile error, never a silent
  half-change. Generated C changes wherever a body names a constant.
  `--fold-constants` restores the old output. C++ still folds; so do `bool`
  constants.
- **A package `const` without `static` folds to its own value.** pssparser
  links an unqualified reference to the constant, but pssc folded only a
  `static const` that way. It then found an enum item of the same name by
  name and folded the item's ordinal: wrong code, with no diagnostic. A
  reference the linker resolved is now folded from its declaration, never
  by name. A non-static package constant now also keeps its name, so SV
  writes it as a `localparam`.

### Additions

- **Detached comments in bodies, opt-in.** Turn it on with
  `--keep-detached-comments`, `driver.compile(keep_detached_comments=True)`,
  or `keep_detached_comments = True` on an `OpModelTarget` subclass. Comments
  that a blank line detaches from a statement in a function or exec body are
  kept: above the statement, each separate block as its own comment, and at
  the end of a block as a comment-only statement. File-scope and declaration
  comments are still dropped. Off by default, so the default output is
  unchanged.
- **A target's prelude sees the sources.** `compile()` sets `opts.sources`
  (the compile's source paths, a list of strings) before it calls
  `Target.prelude(opts)`. The driver drops any prelude unit whose text is
  byte-identical to a source file or to an earlier unit. Passing a package
  explicitly that a prelude also supplies therefore no longer gives a
  duplicate declaration. A modified copy is still a clash: units are matched
  by content, never by name.
- **`pssc.preludes` entry points.** An installed plug-in can put PSS source
  in scope on every target with a callable `(target_name, opts) ->
  list[(name, text)]`. A typical use is a package of annotation types, so an
  annotated model compiles on `op-model-c` and `op-model-sv` without passing
  the library by hand. Contributions follow the target's own prelude, in
  entry-point name order, and are deduplicated the same way.
  - A failing contributor is skipped. `pssc targets` reports it, and
    `pssc compile` warns about it.
  - `pssc parse` takes the contributions too.
  - `PSSC_NO_PLUGINS=1` skips the group.
- **New published members (provisional):** `CStylePolicy.constant_name`,
  `COpModelBackend.emit_constants`, and the manifest's `constants` list and
  `settings.fold_constants`.

### Fixes

- **op-model-cpp and op-model-sv: a parent binding a child's register
  group.** `ch0.regs.set_handle(b)` in the root raised an internal error in
  C++, and SV printed a call that Verilator rejects. Both now bind the
  child's group, as C and Python already did.
- **Packaging:** the pssparser floor is 3.6. 3.5 lacks `FunctionAccess`,
  which the op-model API reads.

## 0.2.0 (not published)

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
