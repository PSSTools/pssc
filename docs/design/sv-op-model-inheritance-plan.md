# SV Op-Model: Native Inheritance — Implementation Plan

Status: in progress, 2026-09-25. **P1 and P2 done**; P7's simulation harness started (`tests/progseq/test_sv_op_model_sim.py`). **The compliance tier runs on op-model-sv** (below, "Compliance first"), ahead of P3. Design: [SV Op-Model: Native Inheritance](sv-op-model-inheritance.md) (decisions D1–D14).

## Where op-model-sv starts

- `progseq_gen.generate()` assembles the package as a list of strings from free functions in `sv/lower_progseq.py`:
  - `emit_export_api`: one `<comp>_if` per component, with sub-component accessors;
  - `emit_import_api`: `<root>_import_if`;
  - `emit_subcomponent_class`: `class X implements X_if`, whose `new(bus, ctor args)` folds the PSS constructor in through `lower_init`;
  - `emit_component`: the root, `#(IMP_T)`, at once the operations, the import forwarder and `create()`.
- It is not a backend class, unlike C and Python. Package order is kept by hand in `generate()`.
- It sets none of the `OpModelTarget` capability flags, so:
  - inheritance is flattened (`comp_inherit.complete`);
  - `--export-action` is refused;
  - `exec init_down`/`init_up` are refused;
  - package functions and executor delegation are refused.
- Exported actions exist only as the `--export-action` directive. pssparser has `ExportAction`/`ExportFunction` AST nodes; ast2ir does not translate them, and no backend has export functions.
- Consumers:
  - `tests/progseq/test_op_model_sv.py` (structure, plus a Verilator lint);
  - the `sv-named`/`sv-folded` goldens;
  - `tests/progseq/data/wb_dma_tb.sv` via `test_sim_wb_dma.py` (`dma_engine_c#(...)::create(bus, base)`, calls through `dma_engine_c_if`).

## Phases

**P1: SV becomes a class backend. Output unchanged. DONE.**
- `SvOpModelBackend`, following `COpModelBackend`'s two rules: every `emit_*` returns text, and the package is exactly its `package_sections()`.
- The SV goldens stay byte-identical; that is the check.

**P2: construction (D2, D3, D4, D8; fixes SV-3). DONE.**
- A generated `<root>_component` base holds `m_imp`, the `pss_init_down`/`pss_init_subs`/`pss_init_up` hooks and `pss_do_init()`.
- `new(imp)` sets field defaults, builds channels and every sub-component, and puts register groups at address 0.
- `initialize(...)` is a non-virtual `function`, rendered by `lower_init`, which still refuses any statement it cannot lower:
  - `regs.set_handle(b)` becomes `m_regs = new(m_imp, b)`;
  - a child's `initialize(...)` is a method call on the already-built child.
- `exec init_down`/`init_up` become the hooks, and `supports_init_blocks = True`.
- WB DMA need not change: its `ch[i].initialize(...)` from the parent's `initialize` is still a legal method call, only made earlier than D3 prescribes.
- As built:
  - The root does not extend `<root>_component` yet. Its `m_imp` is the platform object (`IMP_T`), so it declares the hooks itself; P3 removes the difference.
  - A constructor with an EMPTY body keeps the flat convention the backends share: every register group is bound at its first argument. The bundled `dma_engine` model relies on it.
  - In the flattened view, `super;` in an init block calls private copies of the base's blocks (`_pss_super_<base>_init_down_<k>`). P5 replaces this with `super.pss_init_down()`.
  - An `addr_handle_t` field is now declared; it was dropped before, which only showed once a root could keep its base for `init_down`.

**P3: the model boundary (D1, D5, D9, D11).**
- The per-component `_if` classes and their accessors go.
- `_import_if` becomes `<root>_imp_if`.
- The root is an unparameterized `extends <root>_component`.
- New: `<root>_exp_if` (empty until P6), `<root>_ctxt_if`, and `<root>_root #(Timp)` with `create(imp, <root initialize args>)`.
- `assert_api_is_not_empty` counts entries plus export functions.

**P4: exported actions (D12; the refusal half of D14).**
- `supports_entries = True`.
- An entry body becomes `pss_action_<name>` on its component, with `EntryPoint.supers` as private tasks.
- `<root>_root::<name>()` matches the instances of `entry.comp`: with one it calls it directly, with several it picks one at random, per call.
- The D14 collisions are refused, since they are not yet decided.

**P5: native inheritance (D6, D7, D10; fixes SV-1).**
- `native_inheritance = True`. Classes are emitted base-first from `OpModel.classes`, each holding only what it declares (`comp_inherit.declared`), with `extends` and `super.f(...)`.
- The gate refuses:
  - a value-returning `super.f()` inside an expression, until SV-2;
  - a virtual override whose signature differs from its base's (D10).
- SV joins `test_op_model_inherit_native.py`.

**P6: export functions and executor contexts (D13). Deferred, not needed for compliance.**
- ast2ir translates `ExportFunction`/`ExportAction` for every backend.
- `<root>_exp_if` gets its methods, and `get_context` a real mapping.

**P7: tests, alongside P2–P5.**
- A Verilator simulation harness: a trace-recording platform class, and `create(plat).<entry>()`. The design document's example is its first test.
- `tests/compliance/adapters/pssc_op_model_sv.py` plus `expected/op-model-sv.toml`, once P4 lands.
- P2–P5 regenerate the SV goldens deliberately, each in its own commit with the diff reviewed.

Order: P1 → P2 → P3 → P4 (compliance runs from here) → P5, with P7 growing alongside.

## Compliance first (done, ahead of P3)

The corpus's executable tier runs on op-model-sv: `tests/compliance/adapters/pssc_op_model_sv.py`, `test_compliance_op_model_sv.py` (marked `sim`), `expected/op-model-sv.toml`. 74 of 91 tests pass; each of the 17 listed carries its first gap.
- Scope: one root action as the entry point, no randomization, procedural constructs only.
- The adapter generates with `--export-action` and `--emit-manifest`, and writes a testbench that names the root class and entry from the manifest. It builds and runs under Verilator; stdout is the log.
- Provisional entry shape, until P3/P4 settle the boundary: an entry is a no-argument task of its component, and is also on that component's `_if`. The adapter calls the root's only.
- **Function mapping (MSB): uniform by qualifier.** `target` and unqualified functions are tasks; `solve` functions are SV functions, returning their value. This holds for component and package-scope functions alike (`sv.lower_progseq.is_task`); package ones are `automatic`, because PSS functions may recurse. Later, model-internal functions that never block become SV functions too.
  - Solve context (a `solve function`, the constructor, `exec init_*`) is an SV function, and calling a task from it is refused.
  - A package function that reaches the platform (memory primitives) is refused: it is not given the import API yet.
- **Blocking calls in expressions are hoisted (MSB), fixing SV-2 for SV.** A temporary receives the value, the call is emitted in front of the statement, and the temporary is substituted. The conditional and loop cases are in [SV-2](op-model-output-defects.md#sv-2-value-returning-calls-inside-expressions-are-not-hoisted).
- **`yield` is `#0` (MSB).** The import API no longer declares `yield_`.
- **A struct parameter is `ref`** (LRM 20.3.2, Example 298: an aggregate is passed as a handle to the caller's instance). This changes WB DMA's signatures, e.g. `configure_channel(ref wb_dma_ch_cfg_s cfg)`. **Decided (MSB):** internal functions have exactly two kinds of struct parameter, by-handle (`ref`) and `const` (`input`); scalars are by value (`input`), and there are no `output`/`inout` parameters.
  - **A `const` struct parameter (20.2.3) is `input`.** The callee cannot write it, so a copy cannot be told from the handle, and `input` accepts an aggregate literal where SV's `ref` (even `const ref`) does not. pssparser now records `const` (`FunctionParamDecl.is_const`), and ast2ir carries it as `Function.metadata["const_params"]`.
  - pssparser rejects an aggregate literal passed to a non-`const` parameter of a NATIVE function (`TaskCheckCallArgs::checkConstArg`). Core-library functions are exempt: the LRM's own `write_fields({"mode", "coeff"}, ...)` passes literals to parameters it cannot mark `const`.
  - Native functions have no `output`/`inout` parameters (20.3.2); pssparser already rejects a direction on a function with a body.
  - An aggregate literal renders as a typed pattern, `cfg_s'{mode: 2, prio: 3}`, with unnamed fields at their defaults.
- Lowering added on the way: `message()` formatted per LRM 21.1.1 (unpadded, `%n`); declarations hoisted to the top of their block (`BodyWalker.enter_block`/`leave_block`; a declaration whose initializer blocks counts as a statement); `repeat`, `?:`, match ranges and wildcard, `string`, 64-bit literals, arithmetic `>>` on signed, signed widths other than 32, typedefs for types used only in bodies, struct field defaults, an enum local's default (its first item), enum `match` labels and enum arguments by mnemonic, a discarded register read (SV-5).
- The remaining gaps:
  - a sub-component's attribute read through its instance, `sub.a` (6 tests): member naming and visibility, part of the op-model structure;
  - not an entry: activity, rand, action attributes (4);
  - executor delegation, which the tap tests need (4);
  - package constants in `%n` typing (2), and `get_offset_of_instance` matched by name (1).

## Decisions pending

1. How WB DMA is driven once target functions leave the API (P3). **Decided (MSB): strict.** Target functions leave the API, and `assert_api_is_not_empty` counts entries plus export functions, so op-model-sv without `--export-action` is an error. Every SV-generating test, the bundled testing model and the WB DMA goldens gain exported actions.
2. Where P4's random pick gets its seed. **Decided (MSB): both.** `create()` captures the caller's SV random state (`process::self().get_randstate()`) and seeds the factory's own generator with it (`set_randstate`); picks use the factory's `randomize()`. Verified on Verilator 5.052: the same caller state gives the same picks, and `+verilator+seed` changes them.
3. Python still treats a child's `initialize(...)` as constructing it (D3). Out of scope here; the backends differ until it is aligned.
