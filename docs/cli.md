# pssc CLI reference

```
pssc [--version] <command> ...
```

Every command and option, generated from `pssc help --json`, is listed in
[cli-reference.rst](cli-reference.rst); this page is the guide.

Exit codes: `0` success · `1` user error (unknown target, parse/translate
failure) · `2` internal error. `pssc test` has its own codes — see
[`pssc test`](#pssc-test).

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
| `--keep-detached-comments` | also carry the comments a blank line detaches from a statement in a procedural body (see below) |
| `--line-directives` | op-model targets: precede each generated statement with a line directive naming its PSS source line (see below) |

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

### `--keep-detached-comments` — keeping the notes inside a body

Firmware sources often keep reasoning inside a function body as detached
blocks: a note on why a reset value stays, a call commented out, a remark after
the last statement. Reviewers who read the generated code against the source
want those too. `--keep-detached-comments` keeps them, **in procedural bodies
only** (function and exec bodies, and every block inside one):

- a detached block above a statement is emitted above it, after the comment
  before it and ahead of the statement's own comment. Each block the source
  kept apart with a blank line stays a comment of its own, separated by a
  blank line (in C, a separate `/* */`);
- a block after the last statement of a block (a function body, an `if` or
  `else` clause, a loop body, a `match` arm) is emitted at the end of that
  block. Python adds `pass` if that leaves the block otherwise empty.

```pss
target function void run() {
    regs.CTL.write_val(1);

    // The reset value of STS stays.
    // kick(0);

    regs.STS.write_val(2);

    // Nothing to wait for here.
}
```

Comments are never behaviour: the generated code is the same with the option as
without, comments aside. File-scope and declaration comments a blank line
detaches are still dropped, so a file note above the `import`s stays out
either way. With `--no-comments` there is nothing to keep.

A target can turn the option on for itself with the class attribute
`keep_detached_comments = True` (`OpModelTarget`; default `False`); the flag,
or `driver.compile(..., keep_detached_comments=...)`, overrides it per run.

### `--line-directives` — debugging generated code at the PSS source

With `--line-directives`, the op-model targets precede each generated statement
with the language's way of saying "this came from `file.pss`, line N":

| Target | What is emitted |
|---|---|
| `op-model-c`, `op-model-cpp` | `#line N "/abs/path/file.pss"` (column 0) |
| `op-model-sv` | `` `line N "/abs/path/file.pss" 0 `` (column 0) |
| `op-model-py` | `# /abs/path/file.pss:N`, a comment at the statement's indent |

gdb, gcov and a SystemVerilog simulator then report breakpoints, coverage and
`$display` positions at the PSS lines. Python has no line directive, so the
comment is for a reader (or a tool mapping a traceback back). Statements the
generator synthesizes, and those from the preludes, get none. The option is off
by default, and without it the output is unchanged.

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
| `op-model-c` | `--prefix NAME` | symbol/file prefix (default root sans `_c`); types keep their PSS names |
| `op-model-c` | `--link-style {vtable,direct,mmio}` | memory-access seam (default `mmio`) |
| `op-model-c` | `--reg-style {bitfields,accessors}` | register value layout (default `bitfields`) |
| `op-model-c` | `--header-only` | emit a single `.h` (forced for `mmio`) |
| `op-model-c` | `--fold-constants` | write every named PSS constant (`static const`, package `const`) as its value. By default a constant a body names is a macro the header defines under `#ifndef`, so a `-D` or a project header included first may change it; one pssc also folded elsewhere (an array size, a register offset) is asserted against its value (`PSSC_PINNED_CONSTANT`). See [custom-generator-styles.md](custom-generator-styles.md#named-constants) |
| `op-model-cpp` | `--namespace NAME` | namespace and `<ns>_import_if` (default root sans `_c`); classes keep their PSS names |
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

## `pssc test`

Discover and run pssunit tests.

```
pssc test [SRC-SPEC...] [-k EXPR] [--fixture NAME] [--group NAME]
          [--noskip] [--runxfail] [--collect-only] [--seed N] [-x]
          [--strict] [--no-debug] [--engine py|c|gen-py|gen-sv]
          [--ctrf PATH | --no-ctrf] [--junit PATH]
          [--json] [-v | -q] [--changed [--base REV]] [--watch]
          [--mutate [--mutate-kinds K,K] [--mutate-max N] [-j N]]
```

Unlike `compile` and `parse`, which take a list of files, a **src-spec** is a
file, a directory (searched recursively for `*.pss`), a glob, or `@filelist` —
and with no spec at all, the current directory. A suite is a directory that
grows files, not a list maintained by hand.

| Option | Description |
|---|---|
| `SRC-SPEC...` | file, directory, glob or `@filelist`; default `.` |
| `-k EXPR` | run only tests whose name matches `EXPR` (glob or substring) |
| `--fixture NAME` | run only tests rooted in component `NAME` |
| `--group NAME` | run only tests in annotation group `NAME` (exact match) |
| `--noskip` | run tests annotated `.skip = true` anyway |
| `--runxfail` | report `.xfail` tests by what they actually did |
| `--collect-only` | list the tests that would run; do not run them |
| `--seed N` | solver seed; default random, and always reported |
| `-x`, `--exitfirst` | stop after the first failure |
| `--strict` | fail a test that records no checks |
| `--no-debug` | lower the release image (default: debug lowering — statement markers, calls kept as calls, named locals) |
| `--engine py\|c\|gen-py\|gen-sv` | the engine the tests run on: `py`, the Python VM, or `c`, the C engine (zuspec-rt-eng). On `c` mocks run in place of the PSS function as on `py`, interrupts start the handler as on `py`, the sanitizers and spies see the run's trace when it ends (the same findings and calls), and a test the C engine cannot run yet (an activity with a stream pool, a resource pool over 64 instances, or an action in no known instance) is an ERROR that says why; a `.cdb` records the engine (`native`). `gen-py` / `gen-sv` run each exportable action test as the `op-model-py` / `op-model-sv` generated code (gen-sv under Verilator) on a fresh fake, after a `py` reference run; a bus access that differs fails it ("diverges from pyvm at bus access N"), a test that cannot be exported is a SKIP that says why. See `docs/writing-tests.rst` "Generated code". Default `$PSSC_ENGINE`, else `py` |
| `--platform replay:TRACE` | replay a run `--trace` recorded: reads and user imports are answered from `TRACE` (a trace file, or the directory of one per test, e.g. `.pssunit/traces`), and the first access or import that is not the recorded one fails the test ("first divergent access", expected and actual, with both source lines); so does a run that ends before the recording does. See `docs/test-command.rst` for the other platforms |
| `--ctrf PATH` | write the CTRF report to `PATH` (default `pssunit-ctrf.json`) |
| `--no-ctrf` | write no CTRF report |
| `--report PATH` | same as `--ctrf PATH` |
| `--junit PATH` | also write a JUnit XML report to `PATH` |
| `--changed` | run only tests whose slice (the code they reach) changed since they last passed (record: `.pssunit/results.json`) |
| `--base REV` | with `--changed`: run tests whose slice differs at git revision `REV` (instead of the record) |
| `--watch` | re-run `--changed` each time a source is saved; Ctrl-C ends it |
| `--json` | print NDJSON events (schema `pssunit.events/1`) instead of the human report; with `--collect-only`, the catalog as JSON |
| `--mutate` | after a green run, run each mutant of the model (flipped operator, constant off by one, moved register offset, swapped field) on the tests that reach it and list the ones no test kills (`SURVIVED`) or reaches (`NOT COVERED`), with a mutation score; `-j N` runs N at once; exit 1 if any survive, 2 if the run was not green; with `--json`, `mutant.end` and `mutate.end` events |
| `--mutate-kinds K,K` | with `--mutate`: only these kinds (`operator`, `constant`, `offset`, `field`) |
| `--mutate-max N` | with `--mutate`: at most N mutants, a sample picked by `--seed` |
| `-v`, `--verbose` | per-test detail: qualified name, traceback |
| `-q`, `--quiet` | summary line only |

Exit codes: `0` every selected test passed · `1` the suite ran and a test
failed (a report exists) · `2` the model did not compile (a src-spec naming
nothing, a parse or elaboration error, an unresolvable test base; no report) ·
`3` internal error · `5` nothing selected — no tests found, or none matched.
Full reference, with worked output: [`docs/test-command.rst`](test-command.rst).

## `pssc model`

```
pssc model api     [SRC-SPEC...] --root COMP [--json]
pssc model regs    [SRC-SPEC...] --root COMP [--base ADDR] [--json]
pssc model explain ADDR|PATH [SRC-SPEC...] --root COMP [--base ADDR] [--json]
pssc model tests   [SRC-SPEC...] [--json]
```

Questions about the elaborated model, answered from the op-model manifest
([`docs/op-model-manifest.md`](op-model-manifest.md)) — the registers and
offsets the generators emit. `api` lists components, constructors and
operations; `regs` every register instance with its address (`--base`, default
0, is the root group's); `explain` the register at an address (any byte of it)
or a path from the root (`regs.bank[3].adr0`, `ch[3].regs.adr0`; without
indices, every element), with its fields and the operations that read or write
it; `tests` the pssunit catalog. A sub-component group whose layout matches an
array of groups in the root (WB DMA's `ch[i].regs` and `regs.bank[i]`) is
taken to be bound to element *i* and shown as an alias ("bound by layout"):
the binding itself happens at run time. `--json` prints one document
(schema `pssc.model/1`). Exit `1` when `explain` finds no register, `2` for a
bad src-spec or `--root`.

```
$ pssc model explain 0x88 <wb_dma sources> --root wb_dma_c
0x00000088  regs.bank[3].adr0  (wb_dma_addr_s, 32 bits, READWRITE)
  alias       ch[3].regs.adr0  (bound by layout)
  fields      addr[31:0]
  read by     -
  written by  ch[3].transfer_single, ch[3].configure_channel, ch[3].transfer_single_start
```

## `pssc daemon`

`pssc test` and `pssc model` are served by a per-workspace daemon (`pssd`)
that keeps the compiler imported, so a warm command starts in tens of
milliseconds instead of a few hundred. The workspace is the nearest directory
above the current one holding `pyproject.toml`, `.git`, `flow.dv` or
`flow.yaml`. The first command starts the daemon; it exits after 15 minutes
without a request. Output and exit codes are the same as without it.

```
pssc daemon status [--json]   # running? pid, requests served, uptime
pssc daemon stop
```

| Variable | Effect |
|---|---|
| `PSSC_NO_DAEMON=1` | run every command in the calling process |
| `PSSC_DAEMON_IDLE=<s>` | idle seconds before a newly started daemon exits |
| `PSSC_DAEMON_DIR=<dir>` | where sockets and logs live (default `$XDG_RUNTIME_DIR/pssd-<uid>`) |

A daemon running a different pssc version, or started before the package's
sources were edited, is stopped and replaced by the next command; so is one
that died. If no daemon can be started, the command runs in-process.

The daemon keeps what `pssc test` builds -- the translated model, its test
catalog, each component's lowered image and the op-model gate's verdict --
keyed on the bytes of every source file. A command with no edit since the last
rebuilds nothing; any edit rebuilds the model whole. The gate runs again only
when a file it checks changed: editing a test file does not re-run it.
`pssc daemon status` counts the builds, images, gate runs and cache hits.

## `pssc help`

```
pssc help [COMMAND...]          # the same as pssc COMMAND... --help
pssc help [COMMAND...] --json   # the CLI described as one JSON document
pssc help --rst                 # the same, as docs/cli-reference.rst
```

`--json` prints every command and sub-command (`path`, e.g. `["model",
"regs"]`), each argument (`name`, `flags`, `positional`, `kind`, `type`,
`required`, `default`, `choices`, `metavar`, `help`) and each machine-readable
output it can print: which options select it, its schema id and, when
published, the schema file (`test --json` →
[pssunit.events-1.json](schemas/pssunit.events-1.json), `test --collect-only
--json` and `model tests --json` →
[pssunit.catalog-1.json](schemas/pssunit.catalog-1.json), `model
api|regs|explain --json` → [pssc.model-1.json](schemas/pssc.model-1.json)). It
is read from the real parser, so it lists exactly the commands `pssc` accepts.
Schema: [pssc.help-1.json](schemas/pssc.help-1.json).

**Schema versions.** Each JSON document (or NDJSON event) names its schema in
`schema`, as `name/N`. Within one `N`, pssc may add members and enum values,
and the file under `docs/schemas/` is updated with them; a reader should ignore
members it does not know. Removing or renaming a member, changing its type or
meaning, or making an optional member required is a new `N`, with a new file
beside the old one. The `pssc daemon status --json` document is not versioned.

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

## `pssc sv-pssvm-path`

Print the install path of the bundled SystemVerilog engine package
(`pssvm_pkg`), which runs a `pssc build --image` image inside a simulation:
the testbench extends `pssvm_platform_if` (bus accesses, imports, time) and
`pssvm_ctxt::call` drives the engine. Compile with `+define+PSSVM_DPI` for the
native (C) engine over DPI-C, link the library `--lib` prints, and create the
engine with `pssvm_engine_dpi::new("x.zbc")`; no Python runs in the simulator.

For pyvm (pssc's Python VM) inside the simulator, through pyhdl-if, compile
pyhdl-if's `pyhdl_if.sv`, then `pssvm_pkg.sv`, `pssvm_hif_api_pkg.sv` and
`pssvm_hif_pkg.sv` from this directory, create the engine with
`pssvm_hif_pkg::pssvm_engine_hif::create("x.zbc")`, and run the simulation
with `PYTHONPATH` set to the output of `--py`.

To run the image's tests, give the context to a `pssvm_runner` and call its
`run` task. It runs the tests `+pssunit=SEL` selects (comma-separated globs
on `comp::fn` or `fn`, e.g. `+pssunit=*prio*,t_csr_*`), or every function
test of the image, each between the image's setup and teardown functions.
It prints `PSSVM-TEST: PASS|FAIL <test> @<start>..<end>` per test (the sim
time it started and ended at) and
`PSSVM-SUMMARY: <n> passed, <m> failed`, and returns the number that failed.
The model and the design keep their state from one test to the next.
With `+pssvm_dbg_on_fail` a failing test holds the simulation
(`PSSVM-DBG: held <test> @<time>`) until `pssc sim resume [--rerun]`.

```systemverilog
pssvm_ctxt   ctxt   = new(pssvm_engine_hif::create("x.zbc"), my_platform);
pssvm_runner runner = new(ctxt);
int failed;
runner.run(failed);
```

```
pssc sv-pssvm-path [--file | --py | --lib]
```

| Option | Description |
|---|---|
| (none) | print the directory (`.../pssc/share/sv/pssvm`) |
| `--file` | print the path to `pssvm_pkg.sv` |
| `--py` | print the `PYTHONPATH` the pyvm engine needs (pssc and `pssvm_dpi_shim.py`) |
| `--lib` | print the path to the C engine's shared library (`libpssvm.so`, built on first use) |

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
