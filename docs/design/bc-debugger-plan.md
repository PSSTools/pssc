# A general, DAP-native debugger for bc (plan, 2026-11-20)

Goal: any DAP client -- VS Code first -- debugs a bc image on either engine
(pyvm or the C engine), in process or inside a simulation, and can **stop on
anything and watch anything** the model does, with no pssc in the loop.
pssc is one client of that backend: it adds PSS-specific conveniences
(launch a pssunit test from sources, PSS expression syntax, register views),
never the mechanism.

Tests prove three claims: a third-party DAP client drives the adapter with no
pssc imported; every breakpoint and watch kind works on models with no
registers; and the same scripted session gives the same transcript on both
engines.

Source of the inventory: a survey (2026-11-20) of be-bc `interp/debug.py`
(pyvm `Stub`), be-bc `docs/spec/debug-stub.md`, `interp/stub_socket.py`,
rt-eng `debug.py` (`NativeStub`), rt-eng `share/rt/pssvm_stub.c` (`CStub`,
what DPI sim attach uses), the C interpreter's `dbg_*`, and pssc's
`devenv/debug/` (2551 lines, including `dap.py`).

Paths: be-bc = `packages/zuspec-be-bc/src/zuspec/be/bc`, rt =
`packages/zuspec-rt-eng/src/zuspec/rt/eng`, share = `rt/share`, pssc =
`src/pssc`.

## Where it stands

- **DAP lives in pssc.** `pssc/devenv/debug/dap.py` serves DAP on top of
  pssc's session registry; its `launch` takes PSS sources and a pssunit test
  selection. Nothing in be-bc or rt-eng speaks DAP, so a DAP client can debug
  bc only through pssc. Line resolution, watches, variables and evaluation
  (`breakpoints.py`, `watches.py`, `variables.py`, `eval.py`,
  `frontend.py`) are written against pssc's model objects
  (`model.coros[].locals`, `obj_layouts`, `bc_session._registers`) as much
  as against the image.
- **The engine side is already layered well.** Each engine has a debug stub
  speaking one JSON protocol (`debug-stub.md`): pyvm `Stub`, rt-eng
  `NativeStub` (in process) and `CStub` (C, in a simulation), served over a
  socket by `stub_socket.serve_stub`. be-bc's scripted cases replay on all
  three.
- **The debugger is shaped around register access.** Watches are memory
  access watches only, and DAP data breakpoints accept only a register path
  (`dap.py:345-357`); breakpoints are file:line plus three stop kinds
  (`error`, `check`, `sanitizer`; C has only `error`). A local, an argument,
  a component field, an action attribute, a struct member or an array
  element cannot be watched; a call, an import, a message, a channel
  operation cannot stop the run.
- **The C engine lags pyvm** (watches never verify, no `this`, no
  expressions, no time travel, no check stops, no output; details by item
  below).

## Layers

```
 VS Code / any DAP client          pssc dbg (CLI)     pssc test --debug
            |  DAP (stdio or TCP)        |                  |
            v                            v                  v
   +--------------------------------------------------------------+
   |  bc DAP adapter            (be-bc: zuspec.be.bc.dap)          |
   |  launch an image / attach to a stub; breakpoints of every     |
   |  kind; data watches over storage; variables; evaluate;        |
   |  stepping; reverse; all from the image's debug sections       |
   |        ^ language hooks (expression syntax, value display,    |
   |        |  extra scopes, launch from sources)                  |
   |        +-- generic default (in the adapter)                   |
   |        +-- pssc plugin: PSS syntax, registers, pssunit tests   |
   +--------------------------------------------------------------+
            |  stub protocol (debug-stub.md; in process or socket)
            v
     pyvm Stub      NativeStub (C, in process)     CStub (C, in a sim)
```

- **The adapter** is the debugger. It speaks standard DAP, needs nothing
  but an image (and a platform to run it on), and holds every mechanism:
  line and pc mapping, breakpoint and watch bookkeeping, conditions and hit
  counts, variables, evaluation, reverse execution.
- **The stub protocol** stays the engine interface: low level, in storage
  terms (coroutines, pcs, frames, storage spaces and slots, `src_ref`).
- **Language hooks** are the only place language knowledge lives. The
  generic default works from the image alone; pssc's plugin adds PSS
  expression syntax, register and field views, and launching a pssunit test
  from sources. fw-hdl (or any other Zuspec front end) would add its own.
- **The image is self-describing.** Everything the adapter needs is in the
  image's debug sections: files and source text, lines, function and
  coroutine names, local and argument names and types, component tree and
  slot layouts, action-object layouts, the register map. Python model
  objects are a convenience, never a requirement.

## Principles

1. **Standard DAP first.** Every feature maps to a standard DAP request or
   capability; a custom request is only for something DAP has no word for
   (and the feature works without it). The adapter advertises exactly what
   the engine it is connected to supports.
2. **The adapter needs no pssc.** It lives in be-bc, imports nothing from
   pssc, and a test holds that (as `test_package_layout.py` holds layering
   in pssc).
3. **Stop on anything, watch anything.** Every engine event is a possible
   stop, with a filter; every storage location is a possible watch.
   Registers are a case of the general mechanism, never the mechanism.
4. **Data in the image, not in Python objects.** What the C engine or the
   adapter lacks goes into the image's debug sections and `debug-stub.md`.
5. **Parity is measured, not claimed.** be-bc's scripted stub cases replay
   on `NativeStub` and `CStub` with C's differences in an override
   (`_c_script`) and `exec_snippet` excluded; each item ends by deleting its
   override. A DAP-level transcript harness (P0.1) compares engines the same
   way.
6. **The test models are not register models.** Plain compute, component
   state, structs and arrays, action attributes, parallel activities,
   channels, imports and messages -- and the register model as one case.

## DAP mapping

| Feature | DAP | Stub |
|---|---|---|
| Line breakpoint (+ condition, hit count, logpoint) | `setBreakpoints`, `supportsConditionalBreakpoints`, `supportsHitConditionalBreakpoints`, `supportsLogPoints`, `breakpointLocations` | `set_pc_breaks` |
| Function breakpoint | `setFunctionBreakpoints`, `supportsFunctionBreakpoints` | `set_event_breaks` `call` (D5) |
| Event stops: error, check, sanitizer, call/return, import, message, channel put/get, notify/interrupt, test start/end, action/traversal start/end, solve, time | `setExceptionBreakpoints` with `exceptionBreakpointFilters` (one filter per kind, `supportsExceptionFilterOptions` for a filter condition), `exceptionInfo` | `set_event_breaks` (D5) |
| Instruction breakpoint, disassembly | `setInstructionBreakpoints`, `disassemble` | `set_pc_breaks` |
| Watch a variable, field, attribute, element | `dataBreakpointInfo` (any variable in any scope), `setDataBreakpoints` | `set_data_watch` (D4) |
| Watch an address range | `supportsDataBreakpointBytes` | `set_data_watch` `mem` |
| Memory view | `readMemory` (+ `writeMemory` later) | `backdoor_read` |
| Step, step into/out, other threads | `next`, `stepIn`, `stepOut`, `supportsSingleThreadExecutionRequests` | `step` |
| Reverse | `stepBack`, `reverseContinue`, `supportsStepBack` | `run_to`, `history` |
| Run to a point | `goto`, `gotoTargets`, `supportsGotoTargetsRequest` (back only) | `run_to` |
| Threads, stack, scopes, variables | `threads`, `stackTrace`, `scopes`, `variables` | `threads`, `frames`, `read_frame`, `read_comp` |
| Evaluate, hover, watch expressions | `evaluate` (`watch`, `hover`, `repl`), `supportsEvaluateForHovers` | snippets (D1) |
| Set a value | `setVariable`, `setExpression` (later) | a write command (later) |
| Output (`message()`) | `output` events | `message` events |
| Sources from the image | `source`, `loadedSources` | image FILE section |
| Reload | custom `zuspec/reload` (optional) | `reload` |

## Decisions to make

- **D1 -- where `evaluate` runs.** In the adapter: it compiles an
  expression (through the language hook) to a snippet and runs it over the
  stub's `read_frame`/`read_comp`/`backdoor_read` when the stub lacks
  `exec_snippet`. One implementation for both engines, in process and in a
  simulation. A C snippet runner only if a snippet must run inside the
  engine.
- **D2 -- `CStub`'s future.** Make `NativeStub` a thin client of `CStub`
  (one C implementation, the sim's), after P2.
- **D3 -- `exec_snippet` in the protocol.** Optional capability; the
  adapter falls back (D1).
- **D4 -- data watches.** One command, `set_data_watch`, over storage
  spaces: `mem` (address), `comp` (component object slot), `obj` (an
  activation's action-object slot), `frame` (a frame's local or argument
  slot, ending with the frame). `set_access_watch` becomes its `mem` case.
- **D5 -- event stops.** One command, `set_event_breaks: [{id, kind,
  match}]`; `set_stop_on` stays as the filterless form.
- **D6 -- where the adapter lives.** be-bc, as `zuspec.be.bc.dap`, with a
  console entry point (`zuspec-bc-dap`, or `python -m zuspec.be.bc.dap`),
  stdio and `--port` transports. The engines stay stubs; rt-eng registers
  the C engine with the adapter (`"engine": "c"`) through an entry point,
  so be-bc does not import rt-eng.
- **D7 -- launch and attach configuration.** `launch`: `image` (a `.zbc`),
  `entry` (an export or root function), `engine` (`py`/`c`), `platform`
  (memory; `py:MOD:CLASS` as `pssc test` takes it), `seed`,
  `stopOnEntry`. `attach`: a stub socket (`host`/`port` or a path; a
  simulation's server mode publishes one). A language plugin may add keys
  (pssc: `sources`, `test`).
- **D8 -- the generic expression language.** Names (locals, arguments,
  `this` fields), member access, constant and variable indexing, integer
  and boolean literals, arithmetic, comparison, logic, casts to an image
  type -- resolved from the image's debug tables. Enough for hover, watch
  expressions and conditions in any client without a language plugin.
- **D9 -- the VS Code side.** A small generic `zuspec-bc` debug type
  (`package.json` `debuggers` contribution plus a `launch.json` snippet)
  that runs the adapter; the PSS extension (`vscode-pss-support`) adds the
  `pss` type on the same adapter with pssc's plugin. Where it lives is an
  open question (its own extension, or part of a Zuspec extension).

## Phases

### P0 -- ground truth

- **P0.1** DAP transcript harness: a scripted DAP session (initialize,
  launch, set breakpoints/watches, run, stack/scopes/variables, evaluate,
  step, disconnect) recorded as a normalized transcript (stop reasons,
  file:line, frame names, variables by name and value, output); ids and
  ticks dropped. Run on `py` and `c`; a strict expected-difference list
  per engine. **M**, be-bc tests.
- **P0.2** Debugger corpus (principle 6): small models with no registers,
  plus the WB DMA register model, under be-bc's tests (built by pssc as a
  test fixture until another front end exists; checked-in `.zbc` images so
  the adapter's tests need no compiler). **S-M**.
- **P0.3** Spec catch-up in `debug-stub.md` (`run_to`, `history`, `goto`,
  `tick`; one `error` detail shape); `NativeStub` docstring and
  `STOP_KINDS` corrected. **S**.

### P1 -- a self-describing image

- **1a** Inventory what pssc's debugger reads from Python model objects
  instead of the image (`model.coros[].locals`, `obj_layouts`, the register
  table, component names) and add each to a debug section: component tree
  and slot layouts with names and types (widths, signedness), action-object
  layouts, the register map (paths, offsets, fields), argument counts and
  names. be-bc writer and parser; the C reader where the C engine or `CStub`
  needs it. **M**.
- **1b** The image round-trips: a test loads only the `.zbc` and recovers
  everything the debugger needs. **S**.

### P2 -- the adapter in be-bc

- **2a** Move the mechanism from pssc to `zuspec.be.bc.dap`: DAP framing and
  dispatch, launch/attach (D7), line and pc mapping, breakpoint and watch
  bookkeeping with conditions and hit counts, variables (scopes: Locals,
  Arguments, This, Action, Memory), stepping, threads, output -- on the
  image's debug sections (P1). pssc's `dap.py` and `frontend.py` become
  thin users of it. **L**, be-bc + pssc.
- **2b** Language hooks: `compile_expression`, `format_value`,
  `extra_scopes`, `launch_from` (sources -> image + entry). The generic
  default (D8) in be-bc; pssc's plugin (PSS syntax, register and field
  scopes, pssunit test launch) registered through an entry point. **M**.
- **2c** Transports: stdio and `--port`; `attach` to a stub socket.
  Capabilities advertised from the connected stub's. **S-M**.
- **2d** Layering test: `zuspec.be.bc.dap` imports nothing from pssc; the
  adapter's tests run without pssc installed. **S**.
- **2e** Third-party client test: drive the adapter with the VS Code
  project's DAP test client (`@vscode/debugadapter-testsupport`, Node) on
  both engines, so conformance is not judged by our own client. Adds a
  Node dev dependency. **M**.
- **2f** `pssc dbg` (CLI) and `pssc dap` keep working, now as clients of
  the adapter; the pssc daemon keeps sessions alive across CLI commands.
  **M**, pssc.

### P3 -- C engine quick fixes (through the adapter)

- **3a** Register (now `mem`) watches learn component bases on C (the
  `BaseLearner` gap: `NativeStub` has no `.vm`); in sim attach too
  (`learn=False` today). Or, with P1's register map and base reports from
  the engine, no learning at all. **S**.
- **3b** Output on C: the engine's `message` events as DAP `output`. **S-M**.
- **3c** Action entries on C (pssc refuses them in `_c_debug`): pass the
  action object, bindings and irq through the launch; verify
  `threads`/`frames` under an activation. **S-M**.
- **3d** Capabilities per engine (DAP `supportsStepBack` and the rest come
  from the stub). **S**.

### P4 -- the frame, the component and the action, on C

- **4a** `this`: `comp`, `cbase` in `zbc_dbg_frame_t`/`dbg_fill` and the
  host frame; both C stubs fill `{comp, path}`. **S**.
- **4b** Argument count recorded on CALL and exported. **S**.
- **4c** Action attributes: `read_frame.attrs` (spec), object slots from C,
  names from the image (P1). **M**.
- **4d** Step another thread: `pssvm_dbg_step(h, kind, thread_id)`. **S**.

### P5 -- stop on anything (both engines; D5)

- **5a** `set_event_breaks` in the spec and both engines, matched where
  each event is raised (no cost when none is set): call/return,
  import, message, chan put/get, notify/interrupt, branch, check, error,
  sanitizer, test start/end, action/traversal start/end, solve, time.
  **M** per engine.
- **5b** Adapter: function breakpoints, one exception filter per event kind
  with filter conditions, instruction breakpoints. **M**.
- Tests: a stub case per kind (replayed on both C stubs) and a transcript
  step per kind on the corpus.

### P6 -- watch anything (both engines; D4)

- **6a** `set_data_watch` in the spec and both engines: store (and for
  reads, load) opcodes checked against a watch table only while one is set;
  the C store path keeps a cheap "any watch" test, measured against the
  C-engine benchmark (`tests/unit/test_native_long_runs.py`). A `frame`
  watch ends with its frame, reported. **M-L** per engine.
- **6b** Adapter: `dataBreakpointInfo` for any variable in any scope and
  for any lvalue expression (through the language hook); address ranges
  (`supportsDataBreakpointBytes`). **M**.
- **6c** Channels: a channel's state is its slots (`comp` watch) plus `chan`
  event breaks; documented, no third mechanism.

### P7 -- expressions everywhere (D1, D8)

- **7a** The adapter's snippet fallback over `read_frame`/`read_comp`/
  `backdoor_read`. **M**.
- **7b** The generic expression language (D8). **M**.
- Unlocks on C and without pssc: `evaluate`, hover, watch expressions,
  conditions and logpoints on every breakpoint kind.

### P8 -- time travel

- **8a** Statement ticks, a goto-tick stop with a quiet mode, history;
  `pssvm_dbg_run_to`/`history`; `tick` in `stopped`. **M**, C + **S** rt.
- **8b** Respawn in the adapter (fresh engine, fresh platform, same seed),
  for both engines. **S-M**.
- **8c** `reverseContinue` honours every breakpoint and watch kind. **S**.

### P9 -- traces

- **9a** C's `stmt` event carries the coroutine index and `cbase`; defs and
  uses attached from the image. Fixes rt-eng `tests/test_events.py` (13
  failing) and `pssc trace why/slice` on C. **S+S**.
- **9b** Check, test and sanitizer events in C traces.

### P10 -- simulation

- **10a** DPI back door (`pssvm_dpi.c` sets none): `readMemory` and memory
  watches in a simulation. **M**.
- **10b** `CStub` at parity, or `NativeStub` on top of it (D2).
- **10c** A simulation in server mode publishes its stub socket; VS Code
  attaches to it directly with the generic adapter. **S-M**.
- Tests: `test_pssvm_shim.py` sim-attach cases run the P0.1 script
  (Verilator).

### P11 -- VS Code (D9)

- **11a** The generic `zuspec-bc` debug type and `launch.json` snippets
  (launch an image, attach to a simulation).
- **11b** The `pss` type in `vscode-pss-support`, on the same adapter with
  pssc's plugin.
- **11c** A manual smoke checklist (breakpoint of each kind, data
  breakpoint from the Variables view, hover, step back, attach to a
  Verilator run), until an automated VS Code test is worth its cost.

## Done when

- VS Code debugs a `.zbc` image on either engine and a running simulation
  with only be-bc (and rt-eng for C) installed.
- `@vscode/debugadapter-testsupport` drives the adapter on both engines.
- Every breakpoint kind (line, function, instruction, each event kind,
  time) and every watch kind (each storage space and lvalue kind) has a
  stub case and a transcript step, on models with no registers, passing on
  pyvm and C.
- `_c_script` in rt-eng `test_native_stub.py` overrides nothing;
  `exec_snippet` is optional (D3) with the fallback tested.
- The P0.1 transcript is identical on `py` and `c`.
- rt-eng `test_events.py` passes.
- `docs/debugging.rst` describes one debugger, leads with a non-register
  example, and shows VS Code first.

## Status

- [x] Inventory (2026-11-20); generality (stop on/watch anything) and
      DAP-in-the-backend requirements added (2026-11-20)
- [ ] D1-D9 decided
- [ ] P0.1-P0.3
- [ ] P1 a-b
- [ ] P2 a-f
- [ ] P3 a-d
- [ ] P4 a-d
- [ ] P5 a-b
- [ ] P6 a-c
- [ ] P7 a-b
- [ ] P8 a-c
- [ ] P9 a-b
- [ ] P10 a-c
- [ ] P11 a-c
