# pssc CLI reference

```
pssc [--version] <command> ...
```

Exit codes: `0` success · `1` user error (unknown target, parse/translate
failure) · `2` internal error.

## Diagnostics

A problem in the PSS source is a *user* error: it is reported and exits `1`.
Exit `2` and a traceback mean a bug in pssc — report it.

Diagnostics lead with `file:line:col:`, the form editors and log filters
already parse, and carry the source line with a caret under the offending
token:

```
model.pss:1:8: error: 'export action' is not PSS syntax -- an exported action is declared without the 'action' keyword [PSSC002]
    export action pss_top::entry_a();
           ^~~~~~
    note: drop 'action': export <action_type>(<params>);
    note: PSS 3.1 §20.10: export [target|solve] action_type_identifier (<params>);
```

`note:` lines appear where pssc recognises the specific mistake. The bracketed
code (`PSS028`, `PSSC002`, …) identifies the diagnostic; `PSSC`-prefixed codes
come from pssc's own front-end guards rather than from the parser.

## `pssc compile`

Compile PSS sources to a target.

```
pssc compile SOURCES... [-t TARGET] [-o DIR] [--dump-ir FILE] [-q] [target-opts]
```

| Option | Description |
|---|---|
| `SOURCES...` | one or more `.pss` files (parsed together) |
| `-t, --target` | target name (default `python`; see `pssc targets`) |
| `-o, --output-dir` | output directory, created if absent (default `.`) |
| `--dump-ir FILE` | also write the canonical IR `Context` as YAML to `FILE` |
| `-q, --quiet` | do not print written file paths |
| `--target-cfg NAME=VALUE` | override what the target publishes as `target_cfg_pkg` (repeatable; see below) |
| `--no-comments` | do not carry the PSS source's comments into the generated code (see below) |

### `--no-comments` — dropping the source's prose

The op-model targets emit a close transcription of their PSS source, so by
default they carry the source's comments across: a function's doc comment onto
the generated declaration, a statement's comment onto the generated statement.
The prose is the part of the model a reader cannot recover from the code.

`--no-comments` turns that off. Two reasons to use it:

- the output is consumed by something that only cares about the code, and the
  volume is unhelpful — the fw-wb-dma model's SystemVerilog goes from 519 to
  1574 lines, nearly all of it prose;
- you are checking that a change to the generator changed only comments. The
  *code* under `--no-comments` is identical to the output from before comment
  propagation existed; the only differences are blank lines, from the blank line
  now placed before every function declaration. That makes "only comments moved"
  a measured claim rather than an impression.

**Not everything propagates, by design.** A comment separated from its
construct by a blank line documents nothing and is not emitted. That is how a
file note above the `import` statements stays out of the generated code, and it
is the way to suppress any one comment without deleting it:

```pss
// Emitted: this documents the declaration below.
target function void arm() { }

// Not emitted: a blank line detaches it.

target function void stop() { }
```

### `target_cfg_pkg` — the target describes itself to the model

Every target may inject PSS source that is processed **before** the user's
sources. The built-in use is `target_cfg_pkg`, which answers questions about the
execution target that a portable model has to branch on:

```pss
package target_cfg_pkg {
    static const int  TARGET_CFG_VERSION  = 2;
    static const bool HAVE_EVENT_WAIT     = true;   // can a caller wait for an event?
    static const bool HAVE_RUNTIME_SOLVER = true;   // solver in the image, or pre-solved?
}
```

`pssc targets` prints what each target publishes. `op-model-sv` claims both
(generated operations are `task`s, and SV carries a solver); `op-model-c` and
`op-model-cpp` claim neither (plain functions or methods, no coroutine runtime).
A target that has not established its capabilities publishes nothing, and the
model takes its own defaults.

`op-model-py` answers **both ways**, and it is the only target that does. Its
`--py-await` option chooses between plain methods and `async def` throughout,
and only the second can suspend until another party posts an event — so
`HAVE_EVENT_WAIT` is a property of the form, not of the target:
`--py-await sync` (the default) publishes `false` and `--py-await async`
publishes `true`. `pssc targets` shows the default form's answer. As everywhere
else, `--target-cfg HAVE_EVENT_WAIT=false` overrides it; on the async form that
gives a model that polls, which is a legitimate thing to ask for.

`HAVE_EVENT_WAIT` asks exactly one thing: can a caller **suspend until another
party posts an event** — concretely, is `channel_c`'s blocking `get`/`put`
available. It does *not* ask whether a caller may spin; every target can spin,
so a polling wait needs no capability at all and `yield` is available
everywhere.

> **Contract v1 → v2.** v1 spelled this `HAVE_BLOCKING` and conflated the two
> questions, so a target with no scheduler deleted every operation that waits —
> including ones that could have polled. `HAVE_BLOCKING` is now **rejected**,
> not accepted as a synonym: the right migration is to re-decide the narrower
> question, not to rename the answer.

A model reads it by testing the version marker first, then the flag — **nested,
not `&&`**:

```pss
package target_cfg_pkg { }          // stub, so the name resolves

package my_cfg_pkg {
    compile if (compile has(target_cfg_pkg::TARGET_CFG_VERSION)) {
        static const bool HAS_EVENT_WAIT = target_cfg_pkg::HAVE_EVENT_WAIT;
    } else {
        static const bool HAS_EVENT_WAIT = true;    // default: the richer model
    }
}
```

`compile has(V) && target_cfg_pkg::HAVE_EVENT_WAIT` — the shape in the LRM's
Example275 — is a hard error: pssparser evaluates both operands of a
compile-time binary expression eagerly. Short-circuiting is normative (§8.4.4)
but governs evaluation, not name resolution. Nesting sidesteps it.

A provider that declares `TARGET_CFG_VERSION = N` declares **every** constant in
version N; that obligation is what lets a model reference the flags directly
instead of guarding each one, and it is enforced rather than defaulted.

Per-target options (contributed by each target's `add_args`):

| Target | Option | Description |
|---|---|---|
| `python` | `--emit {none,repr,pickle}` | on-disk artifact: none (in-memory only), `repr` (class-name manifest `pss_classes.txt`), `pickle` (`pss_classes.pkl`). Default `none`. |
| `sv` | `--projection {oo_api,harness}` | package shape: `oo_api` (export API + factory, default) or `harness` (standalone `zsp_test_top`). See [exported-actions-sv.md](exported-actions-sv.md) |
| `sv` | `--export-action NAME` | expose `NAME` on `export_api_if` (repeatable; default: auto-detected root) |
| `sv` | `--package-name NAME` | generated package name (default `zsp_gen_pkg`) |
| `sv` | `--no-rt-pkg` | do not emit the bundled `zsp_rt_pkg.sv` runtime package |
| `sv` | `--single-file` | emit one `zsp_pkg.sv` instead of one file per type |
| `sv-dpi-bridge` | `--export-action NAME` | action to expose as a DPI bridge entry point (repeatable; shared with `sv`) |
| `sv-dpi-bridge` | `--runtime-solve` | solve rand fields per-spawn from the seed (dv-solve linked into the `.so`); default bakes a constant |
| `sv-dpi-bridge` | *(imports)* | package-scope imports become a `pssc_import_if` the testbench implements + registers (`pssc_set_imports`): **solve** imports are synchronous `export "DPI-C"` functions; **target** imports are blocking SV **tasks** (may consume time) serviced via the request-mailbox + fork trampoline. Build the simulator with `-Wl,--export-dynamic` |
| `op-model-sv` | `--root COMP` | root component type to generate the API for (required) |
| `op-model-sv` | `--package NAME` | generated package name (default `<root>_pkg`) |
| *(all op-model)* | `--root COMP` | root component (shared by `op-model-sv` / `-c` / `-cpp` / `-py`) |
| *(all op-model)* | `--no-core-copy` | do not copy the core seam header(s)/package |
| *(all op-model)* | `--emit-manifest FILE` | also write the elaborated model as JSON — operations and signatures, register offsets, produced files and their roles. So a consumer never has to parse generated code |
| `op-model-c` | `--prefix NAME` | symbol/file prefix (default root sans `_c`) |
| `op-model-c` | `--link-style {vtable,direct,mmio}` | memory-access seam (default `vtable`) |
| `op-model-c` | `--reg-style {bitfields,accessors}` | register value layout (default `bitfields`) |
| `op-model-c` | `--header-only` | emit a single `.h` (forced for `mmio`) |
| `op-model-cpp` | `--namespace NAME` | namespace + class prefix (default root sans `_c`) |
| `op-model-cpp` | `--dispatch {virtual,template}` | dispatch model (default `virtual`) |
| `op-model-py` | `--py-module NAME` | generated module name (default root sans `_c`) |
| `op-model-py` | `--py-await {sync,async}` | API form (default `sync`); `async` also publishes `HAVE_EVENT_WAIT=true` |

## `pssc parse`

Run the front end only (parse → translate); optionally dump the IR.

```
pssc parse SOURCES... [--dump-ir FILE] [-q]
```

Without `--dump-ir`, prints a one-line summary (`parsed OK: N types`). With
`--dump-ir FILE`, writes the canonical `zuspec.ir.core.Context` as YAML.

## `pssc targets`

List registered targets and their descriptions.

## `pssc sv-core-path`

Print the install path of the bundled SystemVerilog core package
(`pssc_reg_pkg`), used by the `op-model-sv` target. Useful for adding the core to
a build's compile order.

```
pssc sv-core-path [--file]
```

| Option | Description |
|---|---|
| (none) | print the directory (`.../pssc/share/sv`) |
| `--file` | print the path to `pssc_reg_pkg.sv` |

## `pssc c-core-path` / `pssc cpp-core-path`

Print the install path of the bundled C / C++ core seam headers, used by the
`op-model-c` / `op-model-cpp` targets.

```
pssc c-core-path   [--file [NAME]]      # default NAME: pssc_mem.h
pssc cpp-core-path [--file [NAME]]      # default NAME: pssc_reg.hpp
```

| Option | Description |
|---|---|
| (none) | print the directory (`.../pssc/share/c` or `.../pssc/share/cpp`) |
| `--file [NAME]` | print the path to a header (e.g. `--file pssc_mem_vtable.h`) |

## `pssc --version`

Print the pssc version and exit.

## Examples

```sh
pssc compile cpu.pss bus.pss -t sv -o gen/
pssc compile model.pss -t python --emit repr -o build/
pssc parse model.pss --dump-ir model.ir.yaml

# Generate a SystemVerilog programming API from a component tree
pssc compile dma_regs.pss dma_engine.pss -t op-model-sv --root dma_engine_c -o gen/
pssc sv-core-path --file        # locate pssc_reg_pkg.sv for the compile order
```
