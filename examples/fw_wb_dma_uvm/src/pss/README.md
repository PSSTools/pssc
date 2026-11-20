# PSS implementation of the WISHBONE DMA/Bridge operation model

Source for the operation model in `docs/wb_dma_operation_model.md`, against the
register map in `docs/dma_doc.md` (Rev. 1.5) and the behaviour already modelled
in `src/spl/`.

Layout follows `skills/pss-coding-guidelines`: one element per file, components
as a top-level file plus a same-named directory, everything else attached with
`extend` under `functions/` or `actions/`.

## What is here, and what is not

This tree is the DEVICE. It is the deliverable a consumer integrates, so its
components are named for the device rather than for their role in any one
testbench: `wb_dma_c` and `wb_dma_ch_c`.

Everything environment-specific lives in `tests/pss/` instead:

| Moved to `tests/pss/` | Why it is environment |
|---|---|
| `pss_top` | system assembly -- base address, system RAM, the §5.2 scenarios |
| `wb_dma_hs_c` (+ `wb_dma_hs_pkg`) | the handshake participant is the PERIPHERAL, and its foreign-function contract is a platform requirement the environment satisfies |

`src/pss` therefore has no `pss_top`, and checks clean on its own:

```sh
pssparser $(pss-order --from-flow src/pss --task src --relative-to .)
```

## Configuration: `target_cfg_pkg` and `wb_dma_cfg_pkg`

The one knob in this tree answers a question about the EXECUTION TARGET -- can
a caller wait for an EVENT? -- so it is not device knowledge either. The
convention that carries the answer is `docs/target-cfg-contract.md`; in short:

| File | What it is |
|---|---|
| `wb_dma_cfg_pkg.pss` | the adapter, and the only file. Derives `HAS_EVENT_WAIT` from the target's answer, defaulting **true** when unconfigured. Every use site reads this and nothing else. |

**What the flag selects is the wait PRIMITIVE, not the operation surface.**
Every operation in this tree exists on both profiles. Exactly two things read
the flag:

| Site | `true` | `false` |
|---|---|---|
| `wb_dma_ch_c/functions/wait_hint.pss` (the body) | `wake.get()` — suspend until posted | `yield` — spin |
| `wb_dma_c/functions/notify_irq.pss` (whole function) | present: the event producer | absent: there is no event |

That is a deliberate narrowing. The flag used to be `HAS_BLOCKING` and gated the
whole waiting layer, so a target without a scheduler got a strictly smaller
model — a firmware author and a UVM author wrote against two different APIs for
one device. But no operation here needs to know whether the runtime can suspend
a thread; it needs a way to wait, and spinning is one, available everywhere.
See `wb_dma_ch_c/functions/wait_hint.pss`.

There is no `target_cfg_pkg` stub in this tree. `compile has` naming an
undeclared package evaluates to false rather than erroring, so the unconfigured
case reaches the `else` branch on its own.

The answer itself is not a file here. pssc injects `target_cfg_pkg` ahead of
these sources: `op-model-sv` publishes `HAVE_EVENT_WAIT=true`, and the
`check-nonblocking` task asks for the other answer with `target_cfg:` in
`flow.yaml`. There is **one fileset** and no `exclude:` — a profile is a
property of the target, so it is stated as one rather than as a different set
of files. Both profiles are checked on every `dfm run` (139 types vs 136 — the
difference is `notify_irq` and the three scenario actions).

Two properties are worth not breaking. The guard is **nested**, not a single
`compile has(V) && ...` expression -- that shape is the LRM's own Example275 and
pssparser rejects it, because short-circuiting governs evaluation and not name
resolution. And the default is the **richer** wait, because an unconfigured
build that defaulted the other way would turn every wait into a spin and
elaborate clean — which in a simulation is a hang, not a diagnostic.

`HAVE_BLOCKING` (contract v1) is **rejected**, not aliased. The two flags ask
different questions, so the migration is to re-decide rather than rename.

## Where the operations live

| Operation (model §) | Level | File |
|---|---|---|
| `configure_channel` §3.2 | configuration | `wb_dma_ch_c/functions/configure_channel.pss` |
| `transfer_single` §3.2 (was `run_transfer`) | end-to-end | `wb_dma_ch_c/functions/blocking_ops.pss` |
| `stop_channel` §3.2 | end-to-end | `wb_dma_ch_c/functions/blocking_ops.pss` |
| `set_auto_restart` §3.2 | configuration | `wb_dma_ch_c/functions/set_auto_restart.pss` |
| `set_software_pointer` §3.2 | configuration | `wb_dma_ch_c/functions/set_software_pointer.pss` |
| `transfer_list` §3.2 (was `run_descriptor_chain`) | end-to-end | `wb_dma_ch_c/functions/blocking_ops.pss` |
| `pause_engine` §3.2 | configuration | `wb_dma_c/functions/pause_engine.pss` |
| `configure_interrupt_routing` §3.2 | configuration | `wb_dma_c/functions/configure_interrupt_routing.pss` |
| `write_descriptor` §3.2 (was `write_descriptor_list`) | configuration | `wb_dma_c/functions/write_descriptor.pss` |
| `read_descriptor_residual` §3.2 | configuration | `wb_dma_c/functions/read_descriptor_residual.pss` |
| `request_chunk` §4 | end-to-end | `tests/pss/wb_dma_hs_c/functions/request_chunk.pss` |
| `skip_to_next_descriptor` §4 | end-to-end | `tests/pss/wb_dma_hs_c/functions/skip_to_next_descriptor.pss` |
| `restart_transfer` §4 | configuration | `tests/pss/wb_dma_hs_c/functions/restart_transfer.pss` |

Three functions in this tree are not operations:

`wait_completion` (`wb_dma_ch_c/functions/blocking_ops.pss`) is the shared wait
point of every end-to-end MMIO operation, and the file carries the reasoning
about which register a repeated `check_cond` may safely read.

`wait_hint` (`wb_dma_ch_c/functions/wait_hint.pss`) is the wait PRIMITIVE, and
the only function whose body differs between profiles. Contract: return when
the device may have progressed — it reports nothing and decides nothing, and
every caller re-reads CHn_CSR afterwards. That is what lets the same loop be
correct whether it suspends on an interrupt or spins.

`notify_irq` (`wb_dma_c/functions/notify_irq.pss`) is the one function here the
system does not initiate: it is the **environment's entry point**, called by the
testbench's interrupt monitor when the DMA's aggregate output asserts. No PSS
code calls it. It `try_put`s a token into every channel's depth-1 `wake`
channel, which is what `wait_hint` gets from. Keeping the notification outside
PSS is what avoids a never-retiring interrupt-service action that every scenario
would have to compose in `parallel`. It exists only on the event-wait profile —
it is the event producer, so with no event there is nothing for it to do.

One consequence is worth knowing before you debug a hang, **on the event-wait
profile**: `configure_interrupt_routing` is enforced by deadlock rather than
merely documented. A test that routes no channel raises no interrupt, so nothing
wakes and the first end-to-end operation blocks forever. On a polling profile
there is no such exposure — `wait_hint` spins, so a caller that routed nothing
still makes progress.

`bridge_access` is omitted by review decision.

## Read-modify-write register updates

Where an operation sets one control bit without disturbing the rest of a
register, it uses the LRM's field-wise write:

```pss
regs.csr.write_field("ch_en", 1);
```

**This still reads the register.** PSS 3.1 §21.14.1 defines the masked and
field-wise writes *as* a read-modify-write —
`REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)` — so replacing a
hand-written read/assign/write with one of these changes nothing on the bus. It
matters here because a CHn_CSR read clears ERR and the three interrupt-source
bits: the hazard the comments in `stop_channel_start.pss` and
`transfer_single_start.pss` describe is unchanged, and a reader who sees
`write_field` and concludes the read is gone will be wrong.

Three things about the choice of form:

- **`write_field`, not `write_masked`.** `write_masked({.ch_en=~0}, {.ch_en=1})`
  is the spec's own idiom (Example356) and is currently a trap: pssparser
  discards the operator of a unary expression (defect D5), so `~0` reaches the
  compiler as `0` and the mask selects **no bits** — a write that does nothing.
  The compiler now rejects a zero mask on a named field rather than emitting
  that, but `write_field` names no mask and cannot be bitten at all. Revisit
  when D5 is fixed.
- **The generated SystemVerilog names the field back.** `write_field("ch_en", 1)`
  emits `m_regs.csr.write_field(WB_DMA_CH_CSR_ch_en, 1)` against a `localparam`
  the backend derives from the register layout — the field name is resolved at
  compile time and never reaches the backend as a string. See §7b of
  `docs/reg-masked-access-status.md`.
- **Never `write_fields` in this model.** The plural form coalesces its fields
  into *one* bus read-modify-write — that is what it is for — and the only place
  two fields are set together (`transfer_list_start`) requires them to be two
  separate writes, in order.
- **`pause_engine` writes blind, deliberately.** `wb_dma_gcsr_s` is `pause` plus
  read-only `reserved`, so there is nothing to preserve and nothing to read.

The field name is resolved by the compiler, not carried into generated code: a
typo is a compile error, not a wrong register bit.

## Review decisions carried in from the model document

- `run_transfer` / `run_descriptor_chain` renamed to `transfer_single` /
  `transfer_list` for name symmetry.
- `write_descriptor_list` reshaped to take `at` and `prev` and return the next
  slot, so a caller can build a list in place one link at a time.
- Operations are per-channel, and a waking thread reads its own channel's
  status register. That is why `wb_dma_ch_c` is a component array rather
  than a `chan` parameter on every function -- it keeps register access
  index-free.
- `bridge_access` omitted.

## Structure

```
src/pss/                    THE DEVICE
  wb_dma_types_pkg.pss      shared declarations: the device parameters (channel
                            count, MMIO span, descriptor geometry), the enums,
                            the channel config and the descriptor struct
  (wb_dma_regs_pkg)         NOT IN THIS TREE -- the register model is generated
                            in full from src/rdl by the `reg-model-pss` task
  wb_dma_c(.pss|/)          MMIO model: engine-global operations, owner of the
                            per-channel operation components
  wb_dma_ch_c(.pss|/)       MMIO model: everything per-channel

tests/pss/                  THE ENVIRONMENT (see tests/pss/README.md)
  wb_dma_hs_pkg.pss         the handshake model's foreign-function contract
  wb_dma_hs_c(.pss|/)       hardware-handshake model, one per channel
  pss_top(.pss|/)           system assembly + the §5.2 sequences as scenarios
```

Granularity follows rule 1 of the coding guidelines: components, functions and
actions get their own files; data types and register banks are grouped, because
nothing ever includes one enum or one register without its neighbours.

**File order matters** (see defect 3 below), **and nothing here states it.**
The layout does:

```
src/pss/*_pkg(.pss|/)      packages
src/pss/*_c.pss + *_c/     a component, then everything attached to it
```

`src/pss/flow.yaml`'s `src` task is `pss.FileSet` (in `dv-flow-libpss`), which
derives the order from that layout rather than globbing it alphabetically, and
produces an ordinary `std.FileSet`. Adding a file to `wb_dma_ch_c/functions/`
is the whole change; no list is edited.

It runs with `profile: total-topological` -- the strictest front-end behaviour
measured so far (see `packages/dv-flow-libpss/docs/file-order-probes.md`): a
tool that rejects the *whole* model the moment a file names something not yet
declared, root scope included, and returns exit status 0 while doing it. The
profile does not change the order; it implies `strict:`, so a file breaking the
convention fails the build instead of falling back to a weaker heuristic, and
it checks this tree for constructs that tool cannot compile in any order
(explicit imports, package aliases, qualified package declarations, unqualified
enum items in constant initializers). This model uses none of them, which is
what makes it portable rather than merely working here.

The resulting order, for reference:

```
src/pss/wb_dma_regs_pkg/*.pss
src/pss/wb_dma_types_pkg.pss
src/pss/wb_dma_ch_c.pss    (+ its functions/ and actions/)
src/pss/wb_dma_c.pss       (+ its functions/ and actions/)
tests/pss/wb_dma_hs_pkg.pss
tests/pss/wb_dma_hs_c.pss  (+ its functions/ and actions/)
tests/pss/pss_top.pss      (+ its actions/)
```

The two register-bank files and `wb_dma_types_pkg` do not reference each other,
so their relative order is arbitrary -- `pss.FileSet` sorts what is
unconstrained by name, for stability.

For a command line, derive the order rather than keeping a filelist:

```sh
pss-order --from-flow src/pss --task src --relative-to .   # device only
pss-order . --include 'src/pss/**/*.pss' --include 'tests/pss/**/*.pss' \
            --root pss_top --profile total-topological     # + environment
```

`pss-order` ships with `dv-flow-libpss` and calls the same `derive_file_set`
the `pss.FileSet` task calls, so a command line cannot disagree with the build.
`--from-flow` goes further and reads the task's own parameters -- the root, the
profile, and the `exclude:` that drops the target profile -- so even those are
stated once.

There were two checked-in filelists here, `src/pss/files.f` and
`tests/pss/files.f`. They are gone. A filelist is a snapshot of a derivation
and nothing makes it follow the tree: both went stale when the model was
renamed, and the regression that was supposed to catch that had been failing
unnoticed. Derive it where you need it.

Do not glob. Alphabetical order of this tree leaves references unresolved with
a zero exit status, and it puts `wb_dma_cfg_pkg.pss` after the files whose
`compile if` reads it -- a forward reference, since compile-time elaboration
reads constants only from previously-processed source units (PSS 3.1 19.1.2).

The same rule binds the injected `target_cfg_pkg` ahead of
`wb_dma_cfg_pkg.pss`, and there it is worse: a target profile that lands too
late is not a diagnostic, it is a silent fallback to the default. That one is
not this tree's to get right -- pssc processes a target's prelude before any
user source (`docs/target-cfg-contract.md`), so the provider is ahead of every
file here by construction.

Each `wb_dma_ch_c` owns a top-level `wb_dma_ch_regs_c` group bound to its
own non-allocatable region at `WB_DMA_CH_BASE + chan*WB_DMA_CH_STRIDE`, rather
than the channel banks being an array nested in one device-wide group. That is
what makes every register access in the operation code index-free.

## Checking this source

```sh
dfm run op-model-sv                      # derives the order, links, generates
pssparser $(pss-order --from-flow src/pss --task src --relative-to .)
pssparser $(pss-order . --include 'src/pss/**/*.pss' \
    --include 'tests/pss/**/*.pss' \
    --root pss_top)
```

`dfm run op-model-sv` runs `pss.FileSet` -> `pssc.Check` (elaborate only) ->
`pssc.SvProgSeq`, and writes `pssc_reg_pkg.sv` + `wb_dma_c_pkg.sv`. The same
`op-model-sv` task feeds the UVM env library, so the generated package is
compiled by every simulation image in the regression.

`pssc.Check` also has an `order_check: true` mode that links the model twice --
forwards and reversed -- and compares the resulting IR, since exit status
cannot distinguish a linked model from a mostly-dropped one. It is OFF here
because this model is still order-dependent (defect 3); turn it on when that
is fixed.

The device-only run is entirely silent. Both runs end `0 errors` and exit 0. The environment run additionally prints two
`TaskResolveSymbolPathRef: Failed to get scope` lines -- internal errors that
are neither counted nor reflected in the exit status.

The three `pssparser` defects this README used to carry were re-verified on
2026-08-02 against build `b94827a`:

1. Composite field access inside an `extend component` body -- **fixed**. Every
   function and action in this tree is an `extend` file, and the device links
   clean.
2. SIGSEGV on a component-array reference with a register group present --
   **fixed**.
3. Wrong file order -- **still broken, and now silent**: no longer a segfault,
   but a globbed (alphabetical) order of this tree leaves 78 references
   unresolved while still reporting `0 errors` and exit 0. Hence `pss.FileSet`
   (and, for the command line, `pss-order`). The defect is pinned on the whole
   model by pssc's `tests/progseq/test_op_model_order.py`, a strict xfail that
   xpasses the day it is fixed.

Those, and the leaked internal errors above, are written up with minimal repros
in [`docs/pssparser-defects-2026-08-02.md`](../../docs/pssparser-defects-2026-08-02.md).

Two further environment notes:

- The VS Code PSS extension reports `Cannot resolve import 'addr_reg_pkg'` and
  `Cannot resolve base type 'reg_c'` throughout. Its language server constructs
  `WorkspaceIndex` with no stdlib directory, so `StdlibLoader` falls back to a
  bundled stub that defines only a minimal `std_pkg`. The real stdlib ships in
  the same extension at `packages/zuspec-fe-pss/src/stdlib/`; it is simply never
  wired up. `pssparser` resolves these fine.
- This model targets the `zuspec-fe-pss` stdlib, which is narrower than the PSS
  3.0 LRM. Missing pieces that changed how this source is written:

  | Missing | Consequence |
  |---|---|
  | `reg_c.write_field` / `write_masked` | read-modify-writes are spelled out as `read()` / mutate / `write()` |
  | `read_struct` / `write_struct` | `write_descriptor` serializes the packed struct onto `write32` by hand |
  | `list<>`, varargs `error()` / `format()` | no list parameters; `message()` takes a bare string |
  | enum base types (`enum e : bit[1]`) | packed structs use plain `bit` fields where an enum would read better |

  `read_struct`/`write_struct` are not merely absent from the extension's copy
  of the stdlib -- they are commented out inside `pssparser`'s own embedded
  `addr_reg_pkg`, under `/* TODO: generic type */`, alongside `read_bytes` and
  `write_bytes`. `wb_dma_desc_s` is nevertheless declared as the packed memory
  layout, so enabling them turns the serialization block in `write_descriptor`
  into a single `write_struct(at, d)` with no other change.
