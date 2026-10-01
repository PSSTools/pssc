# bc procedural gaps: plan

Status: **reviewed** (2026-10-01): B-D1 to B-D6 accepted as recommended. B-D4's YIELD
spin is temporary; blocking `get`/`put` will move to an event form. Priority set 2026-10-01: bc's
procedural gaps first, then the operation-model gaps; activities P2 onward wait.
Tick items as they land.

**Goal.** Every corpus test bc lists today as UNSUPPORTED for a *procedural*
reason runs on bc, or moves to its next, named gap. Each fix keeps the house rule
that a construct bc cannot lower is a located error, never a wrong answer.

**Gate.**

1. The 12 entries in `tests/compliance/expected/bc.toml` leave the list, or are
   re-listed with the next gap they reach (the list is strict).
2. Models that use none of the new constructs produce the same bytecode as
   before: golden snapshots stay identical, and no existing bc test changes its
   trace.
3. The native engine (rt-eng) either implements each new opcode or refuses it
   by name before running anything, as it does for P1 (P1-D6).
4. The silent paths the survey found (§1.2) are fixed or refused.

---

## 1. Baseline (surveyed 2026-10-01)

### 1.1 The 12 entries

| Gap | Tests | First failure |
|---|---|---|
| G1 string comparison | `types.string.match.001` | `name == "sz"` → "string comparison is not supported by bc" |
| G2 component-array element with a computed index, and `foreach` | `comp.array.001` | `comp.ch[j]`; `foreach (ch[i])` has no procedural lowering at all |
| G3 channels | `sync.channel.{try,guard,depth}.001` | `channel_c` built-ins have no callee (`_no_callee`) |
| G4 recursion | `L0.func.recursive.001`, `proc.func.recursion.001` | bc inlines every call, so it refuses recursion |
| G5 address handles, memory, executors | `L0P.tap.rw{8,16,32,64}.001` | `read32(h)` → "not a known function or import"; no `addr_handle_t` |

### 1.2 What the survey found besides

- **S1 — procedural `foreach` is not lowered.** `StmtForeach` reaches
  `_lower_stmt` and is refused ("unsupported statement"). The refusal is located,
  so this is not silent, but any model iterating a collection in an exec block
  is UNSUPPORTED on bc.
- **S2 — a string `match` already compiles, by index.** `_match_cmp` skips the
  string check in `_compare`, and `merge` treats a string as a 64-bit integer.
  Equality is right by accident: strings are interned and deduplicated, and bc
  has no operation that makes a new string at run time. A **range** pattern on a
  string compiles to an ordered comparison of table indices, which is a silent
  miscompile.
- **S3 — a run in which every thread is blocked ends as a success.**
  `VM._drain` returns when the ready queue is empty; `run_model` never checks
  that the root finished. Nothing reaches this today. It becomes reachable the
  moment a channel `get` can block (G3).
- **S4 — a string-returning import is typed `bit[32]`** (ir-core
  `lower.py:214`, `DataTypeString` has no `bits`). Refuse it until strings cross
  the import boundary.
- **Data arrays.** bc supports none in procedural code: an array attribute or
  local is one opaque slot, and `a[i]` is refused. No corpus entry is listed for
  it, because the corpus has no procedural test using an array yet. It is the
  largest procedural gap outside this list (§3, G6).

### 1.3 Facts the design rests on

- A component array's elements are contiguous with a fixed stride (ir-core
  `comp_tree._place`). `CompLayout.arrays` gives the count; `subs["ch[k]"]`
  gives each element's slot and instance number.
- No opcode addresses a slot computed at run time. Every load and store takes a
  static slot.
- Each invocation gets a fresh frame in both engines, so a call mechanism would
  be re-entrant. But INVOKE has no way to pass arguments, and lowering never
  uses `HAS_RET` on it.
- The only suspends are WAIT, JOIN, blocking INVOKE and SELECT (all waiting on a
  child or on time) and YIELD. There is no event primitive (D2 schedules
  `EV_WAIT`/`EV_SET` for P3).
- A channel is `ir.DataTypeChannel(element_type, depth)` and today occupies one
  opaque component slot.
- `addr_handle_t` is `chandle` (`ir.DataTypeChandle`). op-model-py models it as
  an integer address: `make_handle_from_handle(h, off)` is `h + off`,
  `add_region` on a transparent space returns `r.addr`, and executors are
  resolved per component.

---

## 2. Decisions for review

**B-D1 — Strings compare by interned index; ordering is refused.** `==`, `!=`
and an equality `match` pattern compare table indices. This is sound for as
long as bc has no operation that creates a string at run time; the first such
operation (concatenation, `format`, a string from an import) must bring a real
comparison with it. `<`, `<=`, a range pattern, and a string-returning import
are located errors. *No ISA change.*

**B-D2 — A computed component-array index is a dispatch, not an opcode.**
`comp.ch[j].x` and `ch[j].f()` lower to a chain of `j == k` branches, one per
element, each with the element's static slots and its own inlined call, plus a
run-time error when `j` is out of bounds. A `foreach` over a component array
unrolls, since the count is static and each copy's element is a constant. The
alternative, an indexed `LD_COMP_X`/`ST_COMP_X`, handles data access but not an
inlined call, whose whole body uses static slots of one instance. The dispatch
handles both and changes no opcode. Its cost is code size proportional to the
element count, which is small for component arrays. *Recommended: dispatch.*

**B-D3 — A channel is component state of `2 + depth` slots.** ir-core lays a
`channel_c<T, D>` out as `count`, `head` and `D` element slots, in place of one
opaque slot. The built-ins are inlined:
- `try_put` and `try_get` dispatch on `head`/`count` over the static depth, as
  in B-D2;
- `try_get(t)` writes its argument as an lvalue (the parameter is `output`);
- elements are scalars only for now (a struct element is a located error).

**B-D4 — A blocking channel `get`/`put` spins on YIELD, and deadlock is an
error.** `get` on an empty channel lowers to "while empty: YIELD", which is
correct under the cooperative scheduler. To keep a deadlock from spinning
forever, a channel spin's YIELD carries a new flag, `INSTR_F_SPIN`. The oracle
raises a run-time error when a full pass of the ready queue runs only spinning
frames with no state change. S3 is fixed in the same item: a run whose root
did not finish is a run-time error. When P3 adds `EV_WAIT`, the spin is
replaced. *Alternative: bring `EV_WAIT`/`EV_SET` forward from P3 now. That is
more work, but it is the final form.*

**B-D5 — Recursion: only a recursive function is called; all others stay
inlined.** Functions in a recursive cycle of the call graph (found at lowering)
become function descriptors. A new opcode family calls them:
- `ARG rs, i` stages argument `i`;
- `CALL fn, rd` invokes, suspending like a blocking INVOKE, so a called
  function may block;
- `LD_ARG rd, i` reads an argument in the callee;
- `RET` already returns a value.

A struct parameter or return value on a called function is a located error at
first. Every other function keeps its inlined bytecode (gate 2). rt-eng
implements the three opcodes: its frames are already per call, and package
functions such as `fact` use no component state. *Alternative: call every
function. That gives one mechanism, but changes all bytecode and costs a frame
per call.*

**B-D6 — Address handles are integer addresses; executors bind statically.**
- `addr_handle_t` is a `bit[64]` address. `make_handle_from_handle` is
  addition, `addr_value` is the identity, and `add_region` on a
  `transparent_addr_space_c` returns `r.addr`. A non-transparent region is a
  located error.
- `readN`/`writeN` (and the `_bytes`/`_struct` forms later) go to the executor
  in force for the calling component (LRM 21.13.9.5). That is the one
  `set_executor` names in its own or an ancestor's init block, resolved **at
  lowering** when the argument is an instance path and the call is
  unconditional, and refused otherwise.
- The executor's override is a component function, inlined in that executor's
  instance. With no executor, or for a primitive the executor does not
  override, the access is a new builtin IMPORT (`BUILTIN_READ`/`BUILTIN_WRITE`,
  width in the args) answered by the platform. The bc adapter gives it a sparse
  memory like op-model-py's `MemoryBus`.
- rt-eng refuses the builtins by name until P8.

*Alternative: dynamic executor binding (an executor slot per component and a
dispatch over the candidates). Deferred until a model needs it.*

---

## 3. Work items (in order)

Each item fixes its gap, adds focused tests under `tests/unit/integration/`
(bc, construct-level), and updates `expected/bc.toml`. An entry that reaches a
new gap is re-listed with it.

### G1 Strings (B-D1)
- [x] `_compare` and `_match_cmp`: string `==`/`!=` compare indices; ordering
  and range patterns are located errors (S2).
- [x] A string-typed import (argument or result) is a located error (S4): ir-core's `ScImportDecl.string_at` names the positions.
- [x] Tests (`test_bc_strings.py`): equality and inequality on literals, parameters and attributes;
  `match` on strings with a default arm; refusals.

### G2 `foreach` and component arrays (B-D2)
- [x] Procedural `foreach` over a component array unrolls; over a data array it
  stays a located error until G6 (S1).
- [x] A computed index on a component array dispatches, with a run-time
  bounds error.
- [x] be-bc `docs/spec/components.md`: replace "a computed index is refused".
- [x] Tests (`test_bc_comp_array.py`): `comp.array.001`; out of bounds; a call through `ch[j]` in a
  component function.

### G3 Channels (B-D3, B-D4)
- [x] ir-core layout: a channel's `2 + depth` slots (`comp_tree.channel_leaves`).
- [x] The four built-ins; `try_get`'s output argument.
- [x] `INSTR_F_SPIN`, deadlock detection (time advances first if a timed frame is waiting), and the unfinished-root error (S3). be-bc `docs/spec/components.md` §Channels.
- [x] rt-eng: channel code uses `LD_COMP`, which it already refuses; refuse
  `INSTR_F_SPIN` too.
- [x] Tests (`test_bc_channels.py`): the three corpus entries; a producer and a consumer in `parallel`
  through a depth-1 channel; deadlock reported.

### G4 Recursion (B-D5)
- [x] A call to a function already being inlined is a CALL of its called form (one per function and instance); every other call stays inlined. (Simpler than a call-graph pass, same result.)
- [x] `ARG`/`CALL`/`LD_ARG` in model.py, the oracle and rt-eng; be-bc `docs/spec/calls.md`, `determinism.md` (a CALL forks no seed).
- [x] Tests (`test_bc_recursion.py`, rt-eng `test_engine_call.py`): the two corpus entries; mutual recursion; a call is no scheduling point; the depth limit (1024) in both engines.
- [x] Found and fixed on the way: rt-eng lost an error raised in any nested callee (INVOKE, SELECT branch, CALL): the callee had no result to write, so the run reported success. Every frame now shares the run's result; the first error wins.

### G5 Address handles, memory and executors (B-D6)
- [x] `addr_handle_t` as a 64-bit `chandle` kind in bc types; `add_region` (transparent), `make_handle_from_handle`, `addr_value`.
- [x] Static executor resolution (`_executors`: top-level `set_executor` in init blocks, inherited); primitives inlined to the override, else `BUILTIN_READ`/`BUILTIN_WRITE`; the oracle's sparse `extern.Memory` (the adapter needs nothing). be-bc `docs/spec/memory.md`. rt-eng refuses the memory builtins and now halts on `BUILTIN_ERROR` (it ran on past it).
- [x] Tests (`test_bc_memory.py`, rt-eng `test_engine_call.py`): the four `L0P.tap` entries; the platform memory; an override and a fallthrough; inheritance into a sub-component; four located refusals.

### Follow-ups found on the way

- **Struct templates are not specialized by ast2ir.** `addr_region_s<TRAIT>`'s
  `TRAIT trait` field reaches the IR as an unresolved `DataTypeRef("TRAIT")`.
  bc holds such a field as one opaque slot and refuses any read or write of it
  (located). The fix is in ast2ir: a specialization per template instance.
- **An executor outside the frame's subtree** (an action in a sub-component
  whose executor is set by the root) is refused: `LD_COMP` addresses only
  below the frame's instance. So is a memory access from an action whose
  component type has more than one instance.
- **Blocking channel `get`/`put` should wait on an event** (user, 2026-10-01);
  the SPIN yield is temporary.
- **rt-eng** refuses channels, memory builtins and component state until P8.

### G6 Data arrays (not in the list; proposed)
Fixed-size arrays of scalars in attributes and locals: one slot per element, an
indexed `LD_*_X`/`ST_*_X` family, and `foreach` over them. This needs its own
design (struct elements, arrays in the solve, `size()`), so it is listed here
only to be scheduled: before or after the operation-model gaps?

---

## 4. Progress log

| Date | Item | Note |
|---|---|---|
| 2026-10-01 | plan written | baseline: bc 98/110 corpus PASS, 12 strict entries |
| 2026-10-01 | G1 | `types.string.match.001` passes on bc (11 entries left). Unit 2079 passed / 4 (docs); compliance 547 passed / 67 xfailed; be-bc 310, ir-core 89 |
| 2026-10-01 | G2 | `comp.array.001` passes on bc (10 entries left). `foreach` over a data array is still refused (G6) |
| 2026-10-01 | G3 | the three `sync.channel.*` entries pass on bc (6 left). rt-eng refuses a SPIN yield (106 passed) |
| 2026-10-01 | G4 | both recursion entries pass on bc (4 left, all G5). rt-eng 113 passed, incl. the nested-error fix |
| 2026-10-01 | G5 (committed: ir-core 9f83b7f, be-bc fba68b4, rt-eng ef7fc81) | the four `L0P.tap.*` entries pass on bc: **`expected/bc.toml` is empty, bc passes all 110 corpus tests**. Unit 2123 passed / 4 (docs); compliance 557 / 57 xfailed; progseq 8 pre-existing, goldens identical; be-bc 310, ir-core 89, rt-eng 115 |
