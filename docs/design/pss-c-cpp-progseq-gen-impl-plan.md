# PSS → C / C++ Programming-Sequence Generation — Implementation, Test & Documentation Plan

> **Archived working note.** Restored to `docs/design/` because the shipped
> source cites it by section; the code is the authority where the two disagree.
> It was written against the tree of its own date and may reference documents
> that are not here.

Status: **Complete** (Phases 0–8 done; see Progress log §9)
Date: 2026-06-19
Companion design: `design/pss-c-cpp-progseq-gen-design.md`
Parallels: `design/pss-programming-seq-gen-impl-plan.md` (the shipped SV plan; same
phase/test/doc shape).
References: `examples/export/programming_seqs/wb_dma_sv_proto.sv` (the validated SV
gold reference + self-checking TB), `src/pssc/targets/progseq_model.py` (the
backend-neutral model), `src/pssc/targets/sv/lower_reg_model.py` +
`sv/lower_progseq.py` (the emitter siblings the C/C++ backends mirror).

This plan turns the C/C++ design into sequenced, individually-acceptable work
items, a `gcc`/`g++`-based test strategy mirroring the SV Verilator gate, and a
`docs/*.rst` documentation set.

---

## 0. Guiding principles

- **The reference is the oracle.** Just as `wb_dma_sv_proto.sv` (Verilator
  `WB_DMA PROTOTYPE PASS`) anchored the SV backend, we **hand-write** the gold
  references first (design §7.5) — `wb_dma_c_proto.c` (per link style) and
  `wb_dma_cpp_proto.cpp` — each with a ported mock bus + self-check that prints
  `WB_DMA PROTOTYPE PASS`. Every phase's acceptance is "generated output
  converges on that reference and still compiles + passes its self-check under
  `gcc`/`g++`."
- **Reuse the language-neutral model.** `progseq_model.py` (`walk_tree`,
  `func_kind`/`FuncKind`, `comp_kind`/`CompKind`, the field/array/register
  introspection) is reused **verbatim**. The C and C++ emitters are siblings of
  `sv/` (new `c/`, `cpp/` packages), structured identically; `progseq_gen.py`
  grows a per-language dispatch but keeps its tree-walk and component ordering.
- **The seam is the only thing that varies.** Every generated operation body is
  byte-identical across C link styles (`direct`/`vtable`/`mmio`) and depends only
  on the seam header selected at generation time. The body-statement walker is
  largely shared with SV's `_BodyEmitter` minus the three SV-only rewrites
  (read→output-arg, return→status, repeat→forever) — in C/C++ those special cases
  simply don't fire.
- **Test like the C-runtime work does.** The C-runtime suite gates on a bare
  `gcc` compile-and-run (`design/pss-c-runtime-impl-plan.md`). C/C++ progseq needs
  no simulator: the always-on gate is "generate → `gcc -std=c11`/`g++ -std=c++17`
  compile against the copied core header(s) + the ported TB → run → `PROTOTYPE
  PASS`." `cc`/`c++` discovery mirrors `available_sims()`.
- **Decisions to confirm (design §7) are frozen here** as the recommended
  defaults unless review says otherwise — see §7 below. Implementation assumes:
  C default `--link-style vtable`, `--reg-style bitfields` (LE); C++ default
  `--dispatch virtual`, `--single-header on`.

---

## 1. Implementation plan (phased)

Each phase lists **files**, **work**, and **acceptance**. Phases are ordered so
each ends at a compilable / runnable artifact. C lands before C++ because the C
value-union header is shared into the C++ backend (design §4.3).

### Phase 0 — Shared-model refactor (de-risk before any emitter) — design §6 — ✅ DONE

`_eval_off`/`_pattern_str`/`_array_base_stride`/`_scalar_offset` and
`_is_reserved`/`collect_reg_groups`/`collect_value_structs` now live in
`progseq_model.py`; `sv/lower_reg_model.py` re-imports them (pure move, no
behavior change). SV suite unchanged-green (Verilator `WB_DMA PROTOTYPE PASS`
still passes); new `tests/progseq/test_model.py` (4 tests) locks the hoisted
contract (channel base 0x20 / stride 0x20, scalar offsets, postorder + first-use
ordering).



Pull the language-neutral helpers out of `sv/lower_reg_model.py` into
`progseq_model.py` so all three backends share one copy. **No behavior change to
SV** — this is a pure move + re-import.

- **Files:**
  - `src/pssc/targets/progseq_model.py` — receive `_eval_off`, `_array_base_stride`,
    `_scalar_offset`, `_pattern_str` (affine offset evaluation, §6.1) and
    `collect_reg_groups`, `collect_value_structs`, `_is_reserved` (the ordered
    type-list walks, §6.2). Keep `_DT_*` name constants here too.
  - `src/pssc/targets/sv/lower_reg_model.py` — delete the moved bodies; re-import
    from `progseq_model`. `lower_register_model`, `emit_value_struct`,
    `emit_reg_group` (SV spelling) stay.
- **Acceptance:** the full SV suite is unchanged-green —
  `tests/progseq/test_generate.py`, `test_sim_wb_dma.py` (Verilator), and the 956
  unit tests. A new `tests/progseq/test_model.py` asserts the hoisted helpers
  return the same `(base, stride)`/offset values directly from the model (locks
  the contract before two more backends depend on it).

### Phase 1 — Hand-written gold references (the oracles) — design §7.5 — ✅ DONE

Shipped the core seam headers in their final location (`src/pssc/share/c/`:
`pssc_mem.h` + `pssc_mem_{vtable,direct,mmio}.h`; `src/pssc/share/cpp/pssc_reg.hpp`)
and the hand-written references under `examples/export/programming_seqs/`:
`c_proto/` (`wb_dma.h`/`.c`, `dma_mock.h`/`.c`, `wb_dma_tb.c`, `wb_dma_tb_mmio.c`)
and `cpp_proto/` (`wb_dma.hpp`, `wb_dma_tb.cpp`). The C driver compiles all three
link styles from one source via `PSSC_LINK_*` macros, proving the byte-identical
body invariant. All compile `-Wall -Wextra -Werror` clean and self-check to
`WB_DMA PROTOTYPE PASS` on gcc+clang / g+++clang++ (`tests/progseq/test_proto_refs.py`,
12 tests). mmio caveat handled honestly: no simulated-bus hook, so its TB
pre-seeds DONE/ERR and verifies register programming (no data movement).

> **Refinement vs. the original plan.** The component struct is defined in the
> header (not opaque) so the baked accessors can be `static inline` and
> zero-overhead (design §3.4); the §3.5 "opaque" note was aspirational and is
> incompatible with header-inline accessors. We also reuse the registered
> `c_toolchain` pytest marker rather than adding a new `compile` marker.



Before emitting anything, write the references the generators must converge on,
each self-checking against a ported mock bus. These are the behavioral truth.

- **Files (new, under `examples/export/programming_seqs/`):**
  - `wb_dma_c_proto.c` — the WB DMA driver hand-translated to C: value unions
    (§3.3), baked inline accessors (§3.4), `wb_dma_*` export functions (§3.5),
    `wb_dma_create/_destroy` (§3.5). Written against the `vtable` seam (the
    default); a `#if`/build-flag selects `direct`/`mmio` so one file exercises all
    three. Includes a `main()` mock-bus + self-check that mirrors the SV TB and
    prints `WB_DMA PROTOTYPE PASS`.
  - `wb_dma_cpp_proto.cpp` — the same, hand-translated to C++: `pssc::mem_if`
    subclass mock bus, `reg<T,ACC>` group classes (§4.3), `wb_dma_if` +
    `wb_dma` (§4.5), `main()` self-check → `WB_DMA PROTOTYPE PASS`.
  - `pssc_mem_direct.h`, `pssc_mem_vtable.h`, `pssc_mem_mmio.h`, `pssc_reg.hpp` —
    drafted here as the hand-written core seam headers (they become the shipped
    `src/pssc/share/c|cpp/` files in Phase 2). `mmio` is exercised by a tiny RAM
    array stand-in for "real" addresses so the self-check still runs hosted.
- **Acceptance:** `gcc -std=c11 -Wall -Wextra wb_dma_c_proto.c -o /tmp/c && /tmp/c`
  prints `WB_DMA PROTOTYPE PASS` for all three link styles; `g++ -std=c++17 -Wall
  -Wextra wb_dma_cpp_proto.cpp -o /tmp/cpp && /tmp/cpp` likewise. These commands
  become Layer-3 fixtures. (This phase also surfaces any value-union endianness or
  `bit_cast` issue before it can hide in codegen.)

### Phase 2 — Core seam headers shipped + locator

Ship the hand-written core headers and make them discoverable, mirroring SV's
`pssc_reg_pkg.sv` + `pssc sv-core-path`.

- **Files:**
  - `src/pssc/share/c/pssc_mem.h` — common address type (`pssc_addr_t`) +
    `static inline` seam prototypes (`pssc_w8/16/32/64`, `pssc_r8/16/32/64`) with
    the LE-bitfield portability banner.
  - `src/pssc/share/c/pssc_mem_direct.h`, `pssc_mem_vtable.h`, `pssc_mem_mmio.h` —
    the three seam definitions (design §3.2), promoted from the Phase-1 drafts.
  - `src/pssc/share/cpp/pssc_reg.hpp` — `pssc::addr_t`, `pssc::mem_if`,
    `pssc::access`, `pssc::reg<T,ACC>`, stock `pssc::mmio_mem` (design §4.2).
  - `src/pssc/cli.py` — add `c-core-path` and `cpp-core-path` subcommands (or one
    `core-path --lang {sv,c,cpp} [--file NAME]`), paralleling `_cmd_sv_core_path`
    / `sv_core_dir()`. Add `c_core_dir()` / `cpp_core_dir()` helpers.
  - `pyproject.toml` — glob `share/c/*.h` and `share/cpp/*.hpp` into the package
    data (verify the existing `share/sv/*.sv` glob's sibling patterns).
- **Acceptance:** `pssc c-core-path`/`cpp-core-path` print existing dirs; `--file`
  prints an existing header; each header compiles standalone (`gcc -fsyntax-only`,
  `g++ -fsyntax-only`). New `tests/progseq/test_core_headers.py` (presence +
  syntax-only + key-symbol grep: `pssc_mem_if`, `pssc::reg`, `mmio_mem`).

### Phase 3 — `c-progseq` target skeleton + CLI

Add the target, register it, parse args; emit nothing yet beyond the copied core.

- **Files:**
  - `src/pssc/targets/c_progseq_tgt.py` (new) — `class CProgSeqTarget(Target)`,
    `name = "c-progseq"`, alias `progseq-c`. `add_args`: `--root` (validated in
    `run`, not argparse-required — same global-parser constraint as SV),
    `--prefix`, `--link-style {vtable,direct,mmio}` (default `vtable`),
    `--reg-style {bitfields,accessors,tree}` (default `bitfields`),
    `--header-only` (default off; forced on for `mmio`). `_resolve_root` reused
    from a shared mixin or copied from `ProgSeqTarget`. `run()` resolves the root
    and dispatches to `c_progseq_gen.generate(...)`.
  - `src/pssc/targets/__init__.py` — register `CProgSeqTarget()` with alias.
  - `src/pssc/targets/c/__init__.py`, `src/pssc/targets/c/c_progseq_gen.py` (new) —
    `generate()` walks the tree (`walk_tree`), logs the component census, copies
    the link-style seam header + `pssc_mem.h`, and writes a (still near-empty)
    `<prefix>.h`. Returns written paths.
- **Acceptance:** `pssc targets` lists `c-progseq`; `pssc compile -t c-progseq
  --root dma_engine_c dma_regs.pss dma_engine.pss -o out/` exits 0 and copies the
  selected seam header. `tests/progseq/test_c_target.py` (registry + arg parsing
  + root-resolution error message).

### Phase 4 — C register model: value unions + baked accessors — design §3.3, §3.4

- **Files:**
  - `src/pssc/targets/c/lower_reg_model.py` (new):
    - `emit_value_union(struct)` — C11 anonymous `union{ uintN raw; struct{…} }`,
      fields in **declaration order** (opposite of SV; LE assumption). Bit-widths
      from `field.datatype.bits`; storage-unit primitive (`uint8/16/32/64_t`) from
      the register width. The `--reg-style accessors` path emits a plain
      `uintN raw` + `_set`/`_get` shift/mask inline helpers instead (design §3.3
      fallback).
    - `c_reg_prim(reg_dtype)` — resolve width → `8/16/32/64` for seam selection.
    - `emit_baked_accessors(group, prefix)` — per register, a `_addr` inline
      (gen-time offset/stride constants + runtime `base`/array index), `_read`
      and `_write` inlines calling `pssc_r*/w*(pssc_bus(s), …)`. Suppress the
      unused accessor for `READONLY`/`WRITEONLY` (design §3.4). Array indexing
      folds into `_addr`; scalar registers drop the `ch` parameter.
    - `pssc_bus(s)` shim emission: `s->bus` for `vtable`, `NULL` (cast) for
      `direct`/`mmio` — the **only** line that varies by link style.
    - Reuse `collect_reg_groups`/`collect_value_structs`/`_array_base_stride`/
      `_scalar_offset` from `progseq_model` (Phase 0).
  - `c_progseq_gen.py` — assemble the header section (banner with LE caveat →
    `#include` core seam → value unions → baked accessors).
- **Acceptance:** generated value unions + accessors match `wb_dma_c_proto.c`
  structurally (channel stride `0x20`, `READONLY` write suppressed, `_reserved`
  gap omitted, field order). The header is `-fsyntax-only` clean for all three
  link styles. `tests/progseq/test_c_generate.py` (structural/regex).

### Phase 5 — C export API, component struct, factory, bodies — design §3.5, §3.6, §3.7

- **Files:**
  - `src/pssc/targets/c/lower_progseq.py` (new):
    - `c_type(dtype)` — PSS `int`→`int`, sized ints→`uintN_t`/`intN_t`,
      `addr_handle_t`→`pssc_addr_t`, value structs→their union typename.
    - `_signature(fn, prefix)` — native C signature: `int` return stays a real
      return; first param `<prefix>_t *s`; args mapped 1:1 (no `output status`,
      no keyword mangling beyond C reserved words — small `_C_KEYWORDS` set).
    - A `_CBodyEmitter` adapted from SV's `_BodyEmitter`: same expr/stmt walk,
      but **native** value-returning reads (`csr = wb_dma_ch_CSR_read(s, ch);`),
      native `return v;`, and `do { … } while (cond);` for `StmtRepeatWhile`
      (design §3.7). Register access lowers `regs.X.read()` →
      `<prefix>_<path>_read(s, idx)`; struct-field assigns stay `v.FIELD = …;`.
    - `emit_export_decls(comp, prefix)` — opaque `<prefix>_t` typedef +
      `<prefix>_create`/`_destroy`/`_init` prototypes + one prototype per
      `EXPORT_OP`. `_create` signature varies by link style (`vtable` takes the
      `pssc_mem_if*`; `direct`/`mmio` take only `base`).
    - `emit_component_impl(root, prefix)` — `struct <prefix>_s` (holds `base`, and
      `bus` for `vtable`), `_create`/`_init`/`_destroy`, and each op body. Root
      `solve ctor(base)` body → `s->base = base;` init.
    - Non-memory imports (design §3.6): `direct`→extern `pssc_<fn>` prototypes;
      `vtable`→appended fn-ptrs / a `<root>_import_if` struct embedding
      `pssc_mem_if` first.
  - `c_progseq_gen.py` — emit `.h` (decls + `static inline` accessors) and, unless
    `--header-only`, `.c` (struct + bodies). `mmio` is header-only by default.
- **Acceptance:** generated `wb_dma.h`/`.c` compile and **the generated driver,
  swapped in for the hand-written one in the Phase-1 TB, prints `WB_DMA PROTOTYPE
  PASS`** for `vtable` and `direct` (and `mmio` against the RAM stand-in). This is
  the headline C integration test.

### Phase 6 — `cpp-progseq` target: register model — design §4.1, §4.2, §4.3

- **Files:**
  - `src/pssc/targets/cpp_progseq_tgt.py` (new) — `name = "cpp-progseq"`, alias
    `progseq-cpp`. `add_args`: `--root`, `--namespace` (default `<prefix>`),
    `--single-header` (default on), `--dispatch {virtual,template}` (default
    `virtual`). Dispatches to `cpp/cpp_progseq_gen.generate`.
  - `src/pssc/targets/__init__.py` — register it.
  - `src/pssc/targets/cpp/__init__.py`, `cpp/cpp_progseq_gen.py` (new).
  - `src/pssc/targets/cpp/lower_reg_model.py` (new):
    - Reuse the **C value-union header** (design §4.3) — emit the same
      anonymous-union structs (call into `c/lower_reg_model.emit_value_union`),
      wrapped in the namespace.
    - `emit_reg_group_class(group, ns)` — a `<group>_c` class of `pssc::reg<T,ACC>`
      members + nested-group members, `(pssc::mem_if&, pssc::addr_t base)` ctor
      folding `base+offset` in the member-init list. Arrays of groups →
      `std::array<…, N>` built via an index-generating helper with the stride
      (design §4.3). Reuse offset helpers from `progseq_model`.
- **Acceptance:** generated reg-model header matches `wb_dma_cpp_proto.cpp`'s
  group classes; `g++ -std=c++17 -fsyntax-only` clean against `pssc_reg.hpp`.
  `tests/progseq/test_cpp_generate.py` (structural).

### Phase 7 — C++ export/import APIs, component class, factory, bodies — design §4.4, §4.5

- **Files:**
  - `src/pssc/targets/cpp/lower_progseq.py` (new):
    - `cpp_type(dtype)` — `std::uintN_t`/`int`/`bool`, `pssc::addr_t`, value
      structs.
    - `emit_export_api(comp, ns)` — `struct <comp>_if` of pure-virtual ops
      (`virtual … = 0;`), virtual dtor.
    - `emit_import_api(root, ns)` — `struct <root>_import_if : pssc::mem_if` + any
      engine import pure-virtuals (design §4.4).
    - `emit_component(root, ns)` — `class <comp> : public <comp>_if`, holding
      `pssc::mem_if& imp_` + the register-model member, built in the ctor from
      `imp_` (**no redirect trick** — design §4.5). Op bodies via a
      `_CppBodyEmitter` (the C emitter's twin, with `regs_.channels[ch].CSR.read()`
      member spelling and native `do…while`). Static `create(mem_if&, base)` →
      `std::unique_ptr<<comp>_if>`.
    - `--dispatch template` path (design §4.5): emit `template <class IMP_T> class
      <comp>` + `reg<T, IMP_T>`; gated behind the flag, the more complex codegen,
      not the default. **Implement the `virtual` path fully first; template path
      is a clearly-separable sub-step that may slip to a follow-up.**
  - `cpp_progseq_gen.py` — assemble the single header (namespace open → value
    unions → reg groups → `_if`/`_import_if` → component class → namespace close).
- **Acceptance:** generated header swapped into the Phase-1 C++ TB → `g++
  -std=c++17` compiles + runs → `WB_DMA PROTOTYPE PASS` (`virtual` dispatch).
  Template dispatch, when landed, passes the same TB with a duck-typed bus.

### Phase 8 — End-to-end wiring, polish, multi-compiler

- **Files:** `c_progseq_gen.py` / `cpp_progseq_gen.py` finalize header banners,
  self-contained output dir (core headers copied unless a `--no-core-copy`
  equivalent), `--header-only`/`--single-header` behaviors, and `_init` static-
  allocation entry points (design §7.4).
- **Acceptance:** `pssc compile -t c-progseq …` and `-t cpp-progseq …` each
  produce a directory that compiles and runs the ported TB to `WB_DMA PROTOTYPE
  PASS` under `gcc`/`clang` and `g++`/`clang++` respectively (whichever are on
  `PATH`). `--reg-style accessors` (C) also passes (layout-independent path).

---

## 2. Test plan

Same three-layer pyramid as SV. Layers 1–2 are pure-Python (fast, no toolchain,
every push). Layer 3 swaps the simulator for a host C/C++ compiler.

### 2.0 Layout & markers

```
tests/progseq/
  conftest.py                 # + available_compilers(); c/cpp gen + build helpers
  test_model.py               # L1: hoisted offset/collection helpers (Phase 0)
  test_core_headers.py        # L1: c/cpp core-path subcommands + header syntax
  test_c_target.py            # L1: c-progseq registry + arg parsing + root errors
  test_cpp_target.py          # L1: cpp-progseq registry + arg parsing
  test_c_generate.py          # L2: run target, assert emitted C structure / golden
  test_cpp_generate.py        # L2: run target, assert emitted C++ structure
  test_build_wb_dma_c.py      # L3: gcc compile+run generated -> PROTOTYPE PASS
  test_build_wb_dma_cpp.py    # L3: g++ compile+run generated -> PROTOTYPE PASS
  data/
    wb_dma_tb_c.c             # mock bus + self-check, #includes generated <prefix>.h
    wb_dma_tb_cpp.cpp         # mock bus (: pssc::mem_if) + self-check
    golden/                   # optional expected emitted headers
```

- Reuse the existing `sim` marker convention with a new `compile` marker (or fold
  into `sim`) so `-m "not compile"` skips Layer 3 on a machine without a C
  toolchain. Compiler discovery mirrors `available_sims()`:

```python
# tests/progseq/conftest.py
import shutil

def available_c_compilers():
    return [cc for cc in ("gcc", "clang", "cc") if shutil.which(cc)]

def available_cpp_compilers():
    return [cxx for cxx in ("g++", "clang++", "c++") if shutil.which(cxx)]
```

- **Split the reference like SV.** The Phase-1 `wb_dma_c_proto.c` /
  `wb_dma_cpp_proto.cpp` are monolithic (driver + mock + self-check). Extract the
  mock-bus + self-check `main()` into `data/wb_dma_tb_c.c` / `wb_dma_tb_cpp.cpp`
  that `#include` the **generated** header by name, so the same self-check
  validates generator output (exactly the SV `wb_dma_tb.sv` split).

### 2.1 Layer 1 — unit (pure Python)

- `test_model.py`: the Phase-0 hoisted helpers (`_array_base_stride`,
  `_scalar_offset`, `collect_reg_groups`, `collect_value_structs`) return the WB
  DMA values (channel base `0x20`, stride `0x20`; the value-struct first-use
  order). Locks the shared contract.
- `test_core_headers.py`: `pssc c-core-path`/`cpp-core-path[ --file]` resolve to
  existing paths; the shipped headers contain `pssc_mem_if`/`pssc::reg`/`mmio_mem`;
  each is `-fsyntax-only` clean.
- `test_c_target.py` / `test_cpp_target.py`: targets listed; `--link-style`/
  `--dispatch`/`--reg-style` parse; missing/unknown `--root` gives the candidate
  list; `mmio` forces header-only.

### 2.2 Layer 2 — generation structure (pure Python)

- `test_c_generate.py`: run `driver.compile(..., target="c-progseq")` into a tmp
  dir per link style; assert (regex/structural, not full-text):
  - value union: `} dma_ch_csr_t;`, fields in declaration order (`CH_EN` before
    `INT_CHK_DONE` — opposite of SV), widths (`uint32_t PRIORITY : 3;`).
  - baked accessor: `wb_dma_ch_CSR_addr`, `+ 0x20u + (pssc_addr_t)ch * 0x20u`,
    `READONLY` write suppressed, `_reserved` absent.
  - export decls: `int wb_dma_mem_to_mem_copy(wb_dma_t *s, …)` (native return),
    `_create` signature differs by link style (`vtable` has `const pssc_mem_if *`).
  - body shape: `do {` / `} while (` present; no `output status`.
  - link-style invariant: the op-body text is **byte-identical** across the three
    styles (diff only the seam `#include` + `pssc_bus`/`bus` field). Assert this
    directly — it is the design's backbone.
- `test_cpp_generate.py`: `wb_dma_if` pure-virtual ops; `<comp> : public
  <comp>_if`; ctor builds reg model from `mem_if&` with no `*_import_if` on the
  component (no redirect); `std::unique_ptr<wb_dma_if> create(`; `std::array<
  dma_channel_regs_c, 31>`.

### 2.3 Layer 2 — golden (optional, behind a flag)

Full-text compare of emitted headers vs. `data/golden/*` with `--update-golden`.
Used sparingly (brittle); the Layer-3 behavioral gate is the real check.

### 2.4 Layer 3 — compile + run (the behavioral gate)

Mirrors SV's `WB_DMA PROTOTYPE PASS`, swapping the simulator for a host compiler.

```python
@pytest.mark.compile
@pytest.mark.parametrize("cc", available_c_compilers())
@pytest.mark.parametrize("link_style", ["vtable", "direct", "mmio"])
def test_build_wb_dma_c(tmp_path, cc, link_style):
    out = generate_c(tmp_path, root="dma_engine_c", link_style=link_style)
    tb  = copy_tb("wb_dma_tb_c.c", out)
    exe = tmp_path / "wb_dma_c"
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-I", out,
                    tb, *([out/"wb_dma.c"] if link_style != "mmio" else []),
                    "-o", exe], check=True)
    log = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
    assert "WB_DMA PROTOTYPE PASS" in log
```

```python
@pytest.mark.compile
@pytest.mark.parametrize("cxx", available_cpp_compilers())
@pytest.mark.parametrize("dispatch", ["virtual"])   # + "template" when landed
def test_build_wb_dma_cpp(tmp_path, cxx, dispatch):
    out = generate_cpp(tmp_path, root="dma_engine_c", dispatch=dispatch)
    tb  = copy_tb("wb_dma_tb_cpp.cpp", out)
    exe = tmp_path / "wb_dma_cpp"
    subprocess.run([cxx, "-std=c++17", "-Wall", "-Wextra", "-I", out, tb,
                    "-o", exe], check=True)
    log = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
    assert "WB_DMA PROTOTYPE PASS" in log
```

- Compile **`-Wall -Wextra -Werror`** once green, so the generator stays
  warning-clean (the C-runtime suite holds this bar).
- **Negative path:** a poisoned source address (as the SV TB does) asserts the
  error-path returns non-zero and the specific message appears.
- `--reg-style accessors` (C) runs as an extra parametrization to cover the
  layout-independent path.

### 2.5 CI matrix

- Pure-Python Layers 1–2 run on every push (no toolchain) — the fast gate.
- Layer 3: `gcc`/`g++` are present on every standard CI image → the **always-on
  behavioral gate** (no `ivpm` fetch needed, unlike Verilator). `clang`/`clang++`
  lanes run where installed; `available_*_compilers()` skips absent tools cleanly.
- A combined `pytest -m "not sim and not compile"` fast job; a `-m compile` job
  for Layer 3.

---

## 3. Documentation plan (`docs/*.rst`)

The Sphinx project (`docs/index.rst` toctree) already carries `progseq` (SV user
guide) and `progseq_design`. Extend that set; do **not** fork a parallel SV doc.

### 3.1 `docs/progseq.rst` — extend the existing user guide

Add a **"Target languages"** section turning the current SV-only guide into a
multi-language one:

- **C** (`c-progseq`): the `--link-style` choice table (`vtable` multi-instance /
  host; `direct` bare-metal single-DUT; `mmio` firmware against real registers),
  `--reg-style` (bitfields vs. portable accessors), `--header-only`. Quick start:
  ```
  pssc compile -t c-progseq --root wb_dma_c --link-style vtable \
        dma_regs.pss dma_engine.pss -o out/
  pssc c-core-path --file pssc_mem_vtable.h
  ```
  Supplying the bus per style (extern fns / `pssc_mem_if` struct / nothing for
  mmio); constructing + calling (`wb_dma_create`, native return values, native
  `do…while`); the value-union `csr.FIELD` access + the LE caveat.
- **C++** (`cpp-progseq`): `--dispatch {virtual,template}`, `--namespace`,
  `--single-header`. Subclass `pssc::mem_if` (or pass a duck-typed bus under
  `template`); `wb_dma::create(bus, base)` → `std::unique_ptr<wb_dma_if>`; the
  stock `pssc::mmio_mem`.
- **Choosing a backend** — a short table (SV sim TB / C firmware+host / C++ host
  models) keyed off design §2 + §5.
- Update **Limitations** — registers internal, front-door timing; the LE-bitfield
  default + the `accessors` portable fallback; `template` dispatch caveats.

### 3.2 `docs/progseq_design.rst` — extend the architecture reference

Fold in design §2 (the four-artifact mapping table), §5 (PSS construct → C/C++
artifact), and the seam-is-the-only-variable invariant. Add `automodule` autodoc
for the new modules: `c.lower_reg_model`, `c.lower_progseq`, `cpp.lower_reg_model`,
`cpp.lower_progseq`, `c_progseq_tgt`, `cpp_progseq_tgt`, and the hoisted helpers in
`progseq_model`. Source of truth stays the `design/` docs.

### 3.3 CLI docs

Document the new surface in `docs/cli.md` + `docs/targets.md` (already present and
updated for SV): the `c-progseq`/`cpp-progseq` targets with all options and the
`c-core-path`/`cpp-core-path` subcommands. If a `docs/cli.rst` is preferred for
the toctree, add the equivalent.

### 3.4 Wiring

- No new toctree entry needed (reuse `progseq`/`progseq_design`); confirm both
  build clean (`sphinx-build -W`).
- Extend `docs/api.rst` autodoc with the new modules.
- A one-line note in `docs/migration-from-zuspec-fe-pss.md` / `quickstart.rst` if
  the multi-language API is relevant there.

---

## 4. Sequencing, milestones & dependencies

| Milestone | Phases | Gate | Status |
| --- | --- | --- | --- |
| **M0 Refactor + oracles** | 0, 1 | SV suite unchanged-green; hand references compile+run → `PROTOTYPE PASS` | ✅ |
| **M1 C core + skeleton** | 2, 3 | `c-core-path` works; headers compile; `c-progseq` registered + runs | ✅ |
| **M2 C register model** | 4 | generated C unions/accessors compile, match reference; structural tests green | ✅ |
| **M3 C end-to-end** | 5 | generated C driver + TB → `WB_DMA PROTOTYPE PASS` (`vtable`+`direct`+`mmio`) | ✅ |
| **M4 C++ register model** | 6 | generated C++ reg classes compile against `pssc_reg.hpp`; structural tests green | ✅ |
| **M5 C++ end-to-end** | 7 | generated C++ (`virtual`) + TB → `WB_DMA PROTOTYPE PASS` | ✅ |
| **M6 Polish + docs** | 8, docs | warning-clean (`-Wall -Wextra -Werror`); `--reg-style accessors` compiles; docs published + Sphinx clean | ✅ (`template` dispatch deferred) |

**Dependencies:** Phase 0 (model refactor) blocks every emitter and must keep the
SV suite green. Phase 1 (oracles) blocks all Layer-3 acceptance. C precedes C++
because the C++ value-union header reuses `c/lower_reg_model.emit_value_union`
(design §4.3). The body emitters (C, C++) derive from the existing SV
`_BodyEmitter` — refactor the shared expression/statement walk into a base if the
overlap is large, but a focused copy is acceptable if reuse is awkward (same
risk-mitigation stance as the SV plan §5).

---

## 5. Risks & mitigations

- **Bitfield layout is implementation-defined.** The `bitfields` default assumes
  LE LP64/LLP64 (documented in the header banner). *Mitigation:* ship
  `--reg-style accessors` (layout-independent shift/mask) and gate it in Layer 3;
  Phase 1's hand reference surfaces any `bit_cast`/endianness issue before codegen.
- **Value-union round-trip (`T'(raw)`).** C++ uses `std::bit_cast`/`memcpy`; a
  naive `reinterpret_cast` is UB. *Mitigation:* the core `reg<T>` template owns the
  conversion (hand-written, tested in Phase 1), not the generator.
- **Three link styles × one body.** The "byte-identical body" invariant is easy to
  break by leaking a style detail into a body. *Mitigation:* a Layer-2 test diffs
  the three emitted bodies and fails on any difference outside the seam include +
  `pssc_bus` shim.
- **Body-emitter reuse vs. fork.** SV's `_BodyEmitter` has SV-only rewrites.
  *Mitigation:* extract the shared walk or fork a focused C/C++ emitter — decide by
  measured overlap, exactly as SV plan §5 handled `lower_stmts` reuse.
- **`template` dispatch complexity** (C++). *Mitigation:* it is an opt-in,
  explicitly not the default and separable from Phase 7's core; may slip to a
  follow-up without blocking M5.
- **Compiler variance** (gcc vs. clang, C11/C++17 features). *Mitigation:*
  `available_*_compilers()` skips absent tools; `-Wall -Wextra` (then `-Werror`)
  pins portability; C11 anonymous unions + C++17 are widely supported floors.

---

## 6. Definition of done

- `pssc compile -t c-progseq --root wb_dma_c …` (all three link styles) and
  `pssc compile -t cpp-progseq --root wb_dma_c …` emit self-contained, compilable
  output (header[s] + optional `.c` + copied core seam).
- The ported `wb_dma_tb_c.c` / `wb_dma_tb_cpp.cpp` self-checks print `WB_DMA
  PROTOTYPE PASS` under every available host compiler, including the negative
  (error-path) check; `--reg-style accessors` also passes.
- Generated code is `-Wall -Wextra -Werror` clean.
- Unit + generation tests green in the no-toolchain fast job; the byte-identical-
  body invariant is asserted.
- The hoisted offset/collection helpers live in `progseq_model.py` and the SV
  suite is unchanged-green after the refactor.
- `docs/progseq.rst`/`progseq_design.rst` extended for C/C++, CLI docs updated,
  Sphinx builds clean.

---

## 7. Open questions / decisions to confirm (design §7)

These mirror the design doc's open items; this plan assumes the **recommended**
default for each so work can proceed, and will revise if review disagrees:

1. **C reg-style default** → `bitfields` (LE LP64/LLP64, banner-documented), with
   `--reg-style accessors` as the portable fallback. *(Assumed; confirm.)*
2. **C link-style default** → `vtable` (multi-instance, host-friendly, matches SV
   semantics); `direct`/`mmio` are bare-metal opt-ins. *(Assumed; confirm.)*
3. **C++ dispatch default** → `virtual`; `template` is the zero-overhead opt-in
   and may land as a follow-up. *(Assumed; confirm.)*
4. **Allocation** → provide `_create` (heap) **and** `_init(self,…)` (caller
   storage) for C; stack-constructible class for C++. *(Assumed.)*
5. **Acceptance artifacts (§7.5)** → hand-write `wb_dma_c_proto.c` /
   `wb_dma_cpp_proto.cpp` **first** (Phase 1), before the emitters. *(Adopted as
   Phase 1.)*
6. **CLI shape** → one `core-path --lang {sv,c,cpp}` vs. three subcommands; and
   whether `c-progseq`/`cpp-progseq` share `--root` resolution via a `Target`
   mixin. *(Plan favors a shared mixin + per-lang `*-core-path`; confirm.)*

---

## 9. Progress log

- **2026-06-19 — Phase 0 ✅.** Hoisted offset/collection helpers into
  `progseq_model.py`; SV suite unchanged-green incl. Verilator; +4 model tests.
- **2026-06-19 — Phase 1 ✅.** Core seam headers shipped (`share/c/`, `share/cpp/`)
  and hand-written gold references written (`c_proto/`, `cpp_proto/`); all compile
  `-Wall -Wextra -Werror` clean and self-check to `WB_DMA PROTOTYPE PASS` on
  gcc/clang/g++/clang++ across all three C link styles; +12 `c_toolchain` tests.
  Decisions taken: component struct is header-visible (zero-overhead inline
  accessors); reuse the existing `c_toolchain` marker.
- **2026-06-19 — Phase 2 ✅.** `c-core-path`/`cpp-core-path` subcommands +
  `c_core_dir`/`cpp_core_dir` helpers; `pyproject` package-data globs
  `share/c/*.h`, `share/cpp/*.hpp`; `test_core_headers.py` (9 tests, incl.
  `-fsyntax-only`).
- **2026-06-19 — Phase 3 ✅.** `c-progseq` target (alias `progseq-c`) +
  `c/c_progseq_gen.py` skeleton (walk + seam copy + header). `--root`/
  `--no-core-copy` shared via `ProgSeqTarget`; C adds `--prefix`/`--link-style`/
  `--reg-style`/`--header-only` (mmio forces header-only). `test_c_target.py`.
- **2026-06-19 — Phase 4 ✅.** `c/lower_reg_model.py`: LSB-first value unions +
  baked inline `_addr`/`_read`/`_write` accessors (READONLY write suppressed,
  reserved gaps omitted, channel stride 0x20 folded). `--reg-style accessors`
  emits the layout-independent get/set helpers (bodies still TODO, Phase 8).
- **2026-06-19 — Phases 5 ✅.** `c/lower_progseq.py`: handle + `pssc_bus` shim,
  export decls, lifecycle (`_create`/`_init`/`_destroy`), and a `_BodyEmitter`
  (native value reads, native `do...while`, native return). Generated driver
  passes the staged TB (`tests/progseq/data/c/`) `WB_DMA PROTOTYPE PASS` on
  gcc+clang for **all three link styles**; `test_c_generate.py` asserts the
  byte-identical-body invariant. 67 progseq tests green (SV gate included).
- **2026-06-19 — Phases 6+7 ✅.** `cpp-progseq` target (alias `progseq-cpp`):
  `cpp/lower_reg_model.py` (std::uintN_t value unions + `pssc::reg<T,ACC>` group
  classes, `std::array` via index-sequence), `cpp/lower_progseq.py` (pure-virtual
  `_if`/`_import_if`, component class holding `mem_if&` + reg model — no redirect,
  member-call body emitter). Generated header passes `WB_DMA PROTOTYPE PASS` on
  g+++clang++ (`virtual` dispatch). `--dispatch template` raises NotImplemented
  (planned). `test_cpp_target.py` + `test_build_wb_dma_cpp.py`.
- **2026-06-19 — Phase 8 ✅.** `--reg-style accessors` bodies now emit
  `<type>_<FIELD>_set/_get` (reg-style-aware C body emitter); compiles
  `-Werror`-clean (compile-gated — same flow as the behaviorally-gated bitfields
  path). Docs extended for C/C++ (`docs/progseq.rst`, `progseq_design.rst`,
  `docs/cli.md`, `targets.md`; Sphinx builds clean — pre-existing `pss_to_sv.rst`
  warnings are unrelated). All generated code is `-Wall -Wextra -Werror` clean.
  **77 progseq tests green; 956 unit tests unchanged-green.**

  *Deviations from plan:* `api.rst` autodoc not added (the SV phase didn't add it
  either; modules are documented in prose in `progseq_design.rst`). `--reg-style
  accessors` is compile-gated rather than behaviorally-gated (the standard TB uses
  `csr.FIELD`, which is the bitfields representation). `--dispatch template`
  deferred (raises a clear NotImplementedError).
