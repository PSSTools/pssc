.. Generated from ``pssc help --json``; do not edit. Regenerate with:
   PYTHONPATH=./src python -m pssc help --rst > docs/cli-reference.rst

CLI reference
=============

Every ``pssc`` command and option, generated from ``pssc help --json`` (schema ``pssc.help/1``, `docs/schemas/pssc.help-1.json`_), which reads the real argument parser. The guide with examples and exit codes is ``docs/cli.md``.

.. _docs/schemas/pssc.help-1.json: schemas/pssc.help-1.json

Global options:

``--version``
   show program's version number and exit

``pssc compile``
----------------

compile PSS sources to a target

``SOURCES``
   PSS source file(s) (required)

``-t TARGET, --target TARGET``
   output target (see ``pssc targets``); default: python

``-o OUTPUT_DIR, --output-dir OUTPUT_DIR``
   directory for generated files (created if absent); default: .

``--dump-ir FILE``
   also write the canonical IR Context as YAML to FILE

``-q, --quiet``
   do not print written file paths

``--reg-rmw REG_RMW``
   lowering for masked register writes: 'native' emits write_val_masked; 'expand' emits read_val + write_val, for a backend with no read-modify-write primitive; default: native (one of ``native``, ``expand``)

``--no-comments``
   do not carry PSS comments into the generated code

``--keep-detached-comments``
   also carry the comments a blank line detaches from a statement in a function or exec body; file-scope and declaration comments stay dropped

``--export-action NAME``
   action to expose as an export entry point (repeatable; default: the auto-detected single root action)

``--target-cfg NAME=VALUE``
   override a target_cfg_pkg constant, e.g. HAVE_EVENT_WAIT=false (repeatable; see ``pssc targets``)

``-X NAME=VALUE, --target-opt NAME=VALUE``
   pass a target-specific option (repeatable). NAME is defined by the target; a bare NAME means NAME=true

``--root-action NAME``
   C targets: action whose activity the generated main runs (simple or qualified name; default: auto-detect the single top action)

``--runtime-solve``
   C targets: force dv-solve runtime solving

``--presolve``
   C targets: force compile-time pre-solving (bake constant values)

``--root COMP``
   op-model: root component type to generate the API for

``--no-core-copy``
   op-model: do not copy the core seam header(s) into the output dir

``--ctor-name NAME``
   op-model: name of the ``solve function`` that is the constructor (default: ctor, init or initialize)

``--emit-manifest FILE``
   op-model: also write the elaborated model as JSON -- components, operations and signatures, register offsets, generated files and their roles. For a consumer that needs to know what the API contains without parsing generated code (see targets/manifest.py)

``--line-directives``
   op-model: precede each generated statement with a line directive naming its PSS source line (``#line`` in C and C++, SystemVerilog's own line directive in SV, a ``# file:line`` comment in Python), so a debugger or coverage tool reports the PSS source rather than the generated file

``--prefix NAME``
   c-progseq: symbol/file prefix (default: root name sans _c)

``--prefix-map TYPE=PREFIX``
   c-progseq: rename one component type: its C type and its symbol prefix. The escape hatch for two component types whose names collide, which is otherwise a generation error (may repeat)

``--link-style C_LINK_STYLE``
   c-progseq: memory-access seam (default: mmio). mmio and direct reach registers through the register-layout struct; vtable keeps per-register accessors, which carry its bus handle (one of ``vtable``, ``direct``, ``mmio``)

``--mem-access C_MEM_ACCESS``
   op-model-c: the memory-access MECHANISM, superseding the direct/mmio spelling of --link-style. 'pointer' dereferences the address; 'functions' calls extern symbols; 'selectable' defers the choice to the C compile line (-DPSSC_MEM_ACCESS_FUNCTIONS). Every one reaches registers through the register-layout struct and routes the access through the chosen mechanism. Omitted: --link-style decides (one of ``pointer``, ``functions``, ``selectable``)

``--reg-style C_REG_STYLE``
   c-progseq: register value layout (default: bitfields, LE) (one of ``bitfields``, ``accessors``)

``--lifecycle C_LIFECYCLE``
   op-model-c: how the root object is obtained. 'malloc' (default) also emits <prefix>_create()/_destroy(); 'static' emits only <prefix>_init() and links no allocator, for a target with no heap (one of ``malloc``, ``static``)

``--emit-stubs``
   op-model-c: also write <prefix>_stubs.c with a WEAK empty pssc_message(), so a link succeeds before the platform side exists. No memory primitive and no model-declared import is ever stubbed -- see the file's own banner for why

``--addr-bits C_ADDR_BITS``
   op-model-c: width of pssc_addr_t (default 64, matching PSS addr_handle_t). 32 saves 4 bytes per address held in a component, and is required on a target whose pointers are 32-bit unless --link-style is vtable: every other seam forms register addresses as pointers, and the generated code fails a static assertion if pssc_addr_t is wider. Narrow it only when the device map fits, which this generator cannot check (one of ``32``, ``64``)

``--include HEADER``
   op-model-c: extra #include in the generated header, placed BEFORE the seam includes so it can define PSSC_MEM_BARRIER, PSSC_CHAN_ENTER/EXIT or PSSC_UNREACHABLE. Repeatable; order preserved. Bare names are quoted, <angled> is passed through

``--include-impl HEADER``
   op-model-c: extra #include in the generated .c only, for something the bodies need but no caller should see. Ignored under --header-only, where there is no separate .c (may repeat)

``--omit-stdint``
   op-model-c: do not emit #include <stdint.h>/<stdbool.h>. For a platform that supplies the fixed-width types itself; pair with --include so something still declares uint32_t

``--fold-constants``
   op-model-c: write every named PSS constant (``static const``, package ``const``) as its value, as before 0.3. By default a constant a body names is a macro the header defines ``#ifndef``, so a -D or a project header may change it

``--header-only``
   c-progseq: emit a single self-contained .h -- every body, register layout and accessor ``static inline``, and no .c. The default emits both, with the implementation in the .c

``--yield C_YIELD``
   op-model-c: how ``yield`` lowers. 'none' (default) emits nothing -- a bare-metal target has no scheduler to yield to, so the surrounding loop becomes a tight poll. 'import' calls yield\_(), for a platform that wants to charge for a spin (WFI, watchdog kick, delay) (one of ``none``, ``import``)

``--chan-wait C_CHAN_WAIT``
   op-model-c: how a BLOCKING channel get()/put() lowers. 'reject' (default) refuses the model: there is no scheduler to suspend to. 'yield' lowers get() like a PSS ``yield`` -- a NOP under ``--yield none`` -- and takes any posted value; for a model whose waits are hints inside a re-check loop. 'poll' spins on the channel's volatile flag, calling the platform's yield\_() between tries, so an ISR's post ends the wait (one of ``reject``, ``poll``, ``yield``)

``--match-default C_MATCH_DEFAULT``
   op-model-c: what an unmatched ``match`` subject does when the model states no default. PSS makes it an error (3.1 §22.7.9) and a C ``switch`` with no default silently does nothing, so 'none' is available but is not the default (one of ``none``, ``message``, ``unreachable``)

``--message-style C_MESSAGE_STYLE``
   op-model-c: 'import' calls pssc_message(); 'none' drops the call AND its format string (.rodata is a real budget on this class of part) (one of ``import``, ``none``; default ``import``)

``--style NAME``
   op-model-c: the naming/layout policy to generate under, from the pssc.styles entry-point group. 'default' is pssc's own conventions; a house style is one class and one entry point, and tracks upstream C semantics automatically

``--namespace NAME``
   cpp-progseq: namespace, and the stem of the import seam (<ns>_import_if) (default: root sans _c). Classes keep their PSS type names

``--dispatch CPP_DISPATCH``
   cpp-progseq: dispatch model (default: virtual) (one of ``virtual``, ``template``)

``--no-single-header``
   cpp-progseq: (reserved) split declarations and bodies

``--py-module NAME``
   op-model-py: generated module name (default: the root component's name with a trailing _c stripped)

``--py-await PY_AWAIT``
   op-model-py: API form. ``sync`` (default) generates plain methods; ``async`` generates ``async def`` throughout and requires an async import API to match. Not a style: it changes what the generated model can do (HAVE_EVENT_WAIT) (one of ``sync``, ``async``)

``--package NAME``
   sv-progseq: generated package name (default: <root>_pkg)

``--sv-reg-fields PROGSEQ_REG_FIELDS``
   sv-progseq: spell a folded masked write as write_field(<FIELD_CONST>, v) ('named', default) or as write_val_masked(<mask>, <val>) ('folded') (one of ``named``, ``folded``)

``--sv-enum-items PROGSEQ_ENUM_ITEMS``
   sv-progseq: name an enum item <ENUM>\_<ITEM> ('prefixed', default: ``op_e { RD }`` is OP_RD) or as written ('verbatim'; two enums' items of one name are refused) (one of ``prefixed``, ``verbatim``)

``--with-gen``
   op-model-sv-vm: also hold op-model-sv's generated implementation, and <root>_root_sel::create, which +pss_impl=vm\|native\|gen (default gen) steers at run time

``--emit EMIT``
   python target: on-disk artifact to write (none: in-memory only; repr: class-name manifest; pickle: pickled class-name manifest). Default: none. (one of ``none``, ``repr``, ``pickle``)

``--no-rt-pkg``
   sv target: do not emit the bundled zsp_rt_pkg.sv runtime package

``--single-file``
   sv target: emit one zsp_pkg.sv instead of one file per type

``--projection SV_PROJECTION``
   sv target: package shape -- 'oo_api' (export API + factory, default) or 'harness' (standalone zsp_test_top module) (one of ``oo_api``, ``harness``)

``--package-name SV_PACKAGE_NAME``
   sv target: name of the generated package (default: zsp_gen_pkg)

``--allow-dpi``
   sv-pure target: permit the DPI/dv-solve fallback for over-capacity problems instead of refusing them (default: off)

``pssc parse``
--------------

run the front end only; optionally dump IR

``SOURCES``
   PSS source file(s) (required)

``--dump-ir FILE``
   write the canonical IR Context as YAML to FILE

``-q, --quiet``
   (no description)

``pssc test``
-------------

discover and run pssunit tests

``SRC-SPEC``
   PSS file, directory (searched for \*.pss), glob, or @filelist; FILE:LINE (the tests spanning that line); or a test-ID glob SUITE::TEST (contains '::'); default: the current directory (optional)

``-k EXPR``
   run only tests whose name matches EXPR (glob or substring)

``-m TAG``
   run only tests tagged TAG (.tags or @pssunit_pkg::tag); repeat for any of several

``--lf, --last-failed``
   run only the tests that failed last time (.pssunit/lastfailed); all of them if none did

``--changed``
   skip the tests that last passed with the same slice (their source and what it reaches by name), options and --seed (.pssunit/results.json, kept by every run once it exists)

``--base REV``
   with --changed: run the tests whose slice differs from their slice in the sources at git REV (a test new since REV runs); the result record is not consulted

``--watch``
   run, then run again with --changed each time a source is saved, until Ctrl-C; each round goes to the daemon

``--image FILE``
   run the tests of image FILE (pssc build --image) instead of compiling sources; positionals are test IDs only

``--fixture NAME``
   run only tests rooted in component NAME

``--group NAME``
   run only tests annotated @pssunit_pkg::test{.group=NAME}

``--noskip``
   run tests annotated .skip=true anyway

``--runxfail``
   report tests annotated .xfail=true by what they actually did

``--collect-only``
   list the tests that would run; do not run them

``--json``
   print NDJSON events, one per line (schema pssunit.events/1) instead of the human report; with --collect-only: the catalog as JSON (schema pssunit.catalog/1)

``--seed SEED``
   solver seed; default: random, and always reported

``--seeds N``
   run every test N times with N seeds (the first is --seed); a failure reports the seed of the run that failed

``-j N, --jobs N``
   run the tests in N processes; the results equal -j1's (default ``1``)

``-x, --exitfirst``
   stop after the first failure

``--strict``
   fail a test that records no checks (it asserted nothing)

``--targets T1,T2``
   op-model targets whose gate the code under test must pass before tests run; default: [tool.pssunit].targets, else op-model-c; 'none' gates nothing

``--engine ENGINE``
   the engine the tests run on: py (the Python VM), c (the C engine; a test it cannot run yet is an ERROR that says why) or gen-py (the op-model-py generated code, on the fake, checked against a py run: a different bus access FAILs; a test that cannot be exported is SKIPped with the reason) or gen-sv (the op-model-sv generated code under Verilator, served the py run's reads and checked the same way); default: $PSSC_ENGINE, else py (one of ``py``, ``c``, ``gen-py``, ``gen-sv``)

``--platform fake|py:MOD[:CLASS]|sim:S``
   the platform for every test: fake (a register fake per test), or MOD's @pv.\* doubles and a new CLASS() per test, or sim:S to build, hot reload and run the tests in server-mode simulation S; default: [tool.pssunit].platform

``--reg-semantics FILE``
   register field kinds (clear on read, write 1 to clear or set, read-only, ...) from an IP-XACT .xml, a SystemRDL .rdl (needs systemrdl-compiler) or a JSON {register: {field: kind}} table, for the fake and the register checks; a field_access annotation wins over it

``--no-debug``
   lower the release image (no statement markers, calls inlined)

``--cov PATH``
   write statement, branch and register coverage to PATH: a covsight database (UCIS, with per-test history) if PATH ends in .cdb, else JSON; needs the debug image

``--trace TRACE``
   write each test's events (statements, calls, register accesses), with source lines, as NDJSON: 'all' for every test, 'fail' for failing ones; needs the debug image; default: off (one of ``off``, ``fail``, ``all``)

``--trace-dir DIR``
   where --trace writes <test-id>.ndjson; default: .pssunit/traces

``--cov-scope COMP``
   count coverage only in component type COMP, its sub-components and its base and derived types (repeatable); default: the tests' @pssunit_pkg::under_test, else all but tests, mocks and libraries

``--ctrf PATH``
   write a CTRF JSON report to PATH; default: pssunit-ctrf.json

``--report PATH``
   same as --ctrf PATH

``--junit PATH``
   also write a JUnit XML report to PATH

``--no-ctrf``
   do not write a CTRF report

``--no-doctest``
   do not run the pss-example blocks of doc comments as tests

``--mutate``
   after a green run, run each mutant of the model (a flipped operator, a constant off by one, a moved offset, a swapped field) on the tests that reach it and list the ones no test kills; -j N runs N mutants at once; exit 1 if any survive

``--mutate-kinds K,K``
   with --mutate, only these kinds of mutant: operator, constant, offset, field (default: all)

``--mutate-max N``
   with --mutate, run at most N mutants, a sample picked by --seed

``-v, --verbose``
   per-test detail (qualified name, seed, timing)

``-q, --quiet``
   summary line only

Machine-readable output:

- ``--json``: ``pssunit.events/1`` (ndjson), schema ``docs/schemas/pssunit.events-1.json``
- ``--collect-only --json``: ``pssunit.catalog/1`` (json), schema ``docs/schemas/pssunit.catalog-1.json``
- ``--report``: ``ctrf`` (json-file)
- ``--junit``: ``junit-xml`` (xml-file)
- ``--cov``: ``pssunit.coverage/1`` (json-file)
- ``--cov``: ``ucis-ncdb`` (cdb-file)
- ``--trace``: ``zbc-trace/2`` (ndjson-file)

``pssc model``
--------------

query the elaborated model (from the op-model manifest)

``pssc model api``
~~~~~~~~~~~~~~~~~~

components, constructors, operations (with the registers each reads and writes), imports

``SRC-SPEC``
   PSS file, directory, glob or @filelist (default: the current directory) (optional)

``--root COMP``
   the root component (required)

``--base ADDR``
   the root register group's address (default 0)

``--json``
   print one JSON document (schema pssc.model/1)

Machine-readable output:

- ``--json``: ``pssc.model/1`` (json), schema ``docs/schemas/pssc.model-1.json``

``pssc model regs``
~~~~~~~~~~~~~~~~~~~

every register instance and its address

``SRC-SPEC``
   PSS file, directory, glob or @filelist (default: the current directory) (optional)

``--root COMP``
   the root component (required)

``--base ADDR``
   the root register group's address (default 0)

``--json``
   print one JSON document (schema pssc.model/1)

Machine-readable output:

- ``--json``: ``pssc.model/1`` (json), schema ``docs/schemas/pssc.model-1.json``

``pssc model explain``
~~~~~~~~~~~~~~~~~~~~~~

the register at an address or path: its fields and the operations that touch it

``ADDR|PATH``
   (required)

``SRC-SPEC``
   PSS file, directory, glob or @filelist (default: the current directory) (optional)

``--root COMP``
   the root component (required)

``--base ADDR``
   the root register group's address (default 0)

``--json``
   print one JSON document (schema pssc.model/1)

Machine-readable output:

- ``--json``: ``pssc.model/1`` (json), schema ``docs/schemas/pssc.model-1.json``

``pssc model tests``
~~~~~~~~~~~~~~~~~~~~

the pssunit test catalog

``SRC-SPEC``
   PSS file, directory, glob or @filelist (default: the current directory) (optional)

``--json``
   print one JSON document (schema pssc.model/1)

Machine-readable output:

- ``--json``: ``pssunit.catalog/1`` (json), schema ``docs/schemas/pssunit.catalog-1.json``

``pssc build``
--------------

write a model image (.zbc) a test run or a simulation can load

``SRC-SPEC``
   PSS file, directory, glob or @filelist, as for pssc test; default: the current directory (optional)

``--image OUT``
   the image file to write (e.g. out/model.zbc) (required)

``--root COMP``
   the root component (suite or fixture) to build; needed when the sources have several

``--api``
   give every function under --root an entry, so a generated facade (target op-model-sv-vm) can call it; --root need not hold tests

``--generation N``
   the build number recorded in the image (default 0 = unset, so the image is reproducible)

``--no-sources``
   leave the PSS sources out of the image

``--native``
   the image is for the C engine in a simulation, which has no Python: refuse it when the platform's Python doubles (@pv.mock/spy/implements) name a function or import in it, with their locations

``--platform SPEC``
   with --native: the Python platform whose doubles to check (default: [tool.pssunit] platform)

``--json``
   print one JSON document (schema pssc.image/1)

Machine-readable output:

- ``--image``: ``zbc-debug`` (zbc-file)
- ``--json``: ``pssc.image/1`` (json)

``pssc dbg``
------------

debug one test from the command line (sessions kept by the workspace daemon)

``pssc dbg start``
~~~~~~~~~~~~~~~~~~

start a session on one test, stopped before its first instruction

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

``SRC-SPEC|TEST-ID``
   PSS file, directory, glob or @filelist (default: the current directory), and the test's ID (COMP::NAME; globs allowed) (optional)

``-k EXPR``
   select the test by name expression instead of ID

``--seed N``
   (no description)

``--platform SPEC``
   as ``pssc test --platform`` (default: the test's)

``--trace FILE|DIR``
   replay a recorded run: the same as --platform replay:FILE\|DIR

``--reg-semantics FILE``
   as ``pssc test --reg-semantics``

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg break``
~~~~~~~~~~~~~~~~~~

breakpoints

``pssc dbg break add``
^^^^^^^^^^^^^^^^^^^^^^

add a breakpoint at FILE:LINE

``FILE:LINE``
   (required)

``--condition EXPR``
   stop only when EXPR is non-zero

``--hit N|>=N|%N``
   stop only on these hits

``--log TEXT``
   a logpoint: write TEXT ({expr} interpolated) and go on

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg break list``
^^^^^^^^^^^^^^^^^^^^^^^

the session's breakpoints

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg watch``
~~~~~~~~~~~~~~~~~~

stop when a register (or field) is accessed

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

``PATH``
   register or register.field (required)

``--rw RW``
   the accesses that stop (default: w) (one of ``r``, ``w``, ``rw``)

``--value N``
   only when the value written (or read) is N

``--condition EXPR``
   (no description)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg run``
~~~~~~~~~~~~~~~~

run to the next stop or the end

``--session ID``
   the session (default: the latest)

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg continue``
~~~~~~~~~~~~~~~~~~~~~

the same as run

``--session ID``
   the session (default: the latest)

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg step``
~~~~~~~~~~~~~~~~~

step into the next statement

``--session ID``
   the session (default: the latest)

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg next``
~~~~~~~~~~~~~~~~~

step over to the next statement

``--session ID``
   the session (default: the latest)

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg out``
~~~~~~~~~~~~~~~~

run until the current function returns

``--session ID``
   the session (default: the latest)

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg back``
~~~~~~~~~~~~~~~~~

step back to the previous statement (Python engine; the run is run again to it)

``--session ID``
   the session (default: the latest)

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg rc``
~~~~~~~~~~~~~~~

reverse-continue: back to the last breakpoint or watch stop before here

``--session ID``
   the session (default: the latest)

``--timeout SECONDS``
   wait at most this long for the run to stop (default: 30); then it is left running

``--json``
   print one JSON document

``--watch PATH``
   back to the last write of this register or field only (through a watch that is gone afterwards)

Machine-readable output:

- ``--json``: ``pssunit.stop/1`` (json), schema ``docs/schemas/pssunit.stop-1.json``

``pssc dbg pause``
~~~~~~~~~~~~~~~~~~

ask a running run to stop (the next run command returns the stop)

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg bt``
~~~~~~~~~~~~~~~

the stack of the stopped thread

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

``--thread ID``
   (no description)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg locals``
~~~~~~~~~~~~~~~~~~~

the variables of a frame

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

``--frame ID``
   a frame from bt (default: the stopped one)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg regs``
~~~~~~~~~~~~~~~~~

register values (through the back door)

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

``PATH``
   only registers under this path (optional)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg bus``
~~~~~~~~~~~~~~~~

the last bus accesses, newest first

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

``-n N``
   how many (default: 16)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg eval``
~~~~~~~~~~~~~~~~~

evaluate a PSS expression where the run stopped

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

``EXPR``
   (required)

``--frame ID``
   (no description)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg threads``
~~~~~~~~~~~~~~~~~~~~

the run's threads

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg reload``
~~~~~~~~~~~~~~~~~~~

hot reload: lower the session's sources again and make them its next generation (staged until the run ends)

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

``--cancel``
   end the running call first (refused while one runs)

``--strict``
   refuse a change to the component tree

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg end``
~~~~~~~~~~~~~~~~

end the session

``--session ID``
   the session (default: the latest)

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dbg list``
~~~~~~~~~~~~~~~~~

the sessions

``--json``
   print one JSON document

Machine-readable output:

- ``--json``: unversioned (json)

``pssc sim``
------------

server-mode simulations (``+pssvm_serve``) registered with the workspace daemon

``pssc sim list``
~~~~~~~~~~~~~~~~~

list the simulations waiting for work

``--json``
   print one JSON object

Machine-readable output:

- ``--json``: unversioned (json)

``pssc sim run``
~~~~~~~~~~~~~~~~

run the selected tests in a simulation and wait for them

``--session ID``
   the session, by id or name (default: the only one)

``--json``
   print one JSON object

``SEL``
   test name or glob, as for ``+pssunit=`` (none: every test) (optional)

``--timeout SECONDS``
   give up waiting after this long (default: wait)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc sim log``
~~~~~~~~~~~~~~~~

show a simulation's events (oldest kept first)

``--session ID``
   the session, by id or name (default: the only one)

``--json``
   print one JSON object

``--since N``
   only events numbered above N (default ``0``)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc sim reload``
~~~~~~~~~~~~~~~~~~~

hot reload: make a rebuilt image (``pssc build --image``) the simulation's next generation, between runs

``--session ID``
   the session, by id or name (default: the only one)

``--json``
   print one JSON object

``IMAGE``
   the image (default: the one the simulation loaded, rebuilt in place) (optional)

``--strict``
   refuse a change to the component tree

``--cancel``
   end a running call first

``--timeout SECONDS``
   give up waiting after this long (default: 600; the simulation answers once a run ends)

Machine-readable output:

- ``--json``: unversioned (json)

``pssc sim attach``
~~~~~~~~~~~~~~~~~~~

debug a simulation: a ``pssc dbg`` session on its VM (not stopped; set breakpoints, then ``pssc dbg continue`` waits for a stop)

``--session ID``
   the session, by id or name (default: the only one)

``--json``
   print one JSON object

``pssc sim resume``
~~~~~~~~~~~~~~~~~~~

go on from a test held by ``+pssvm_dbg_on_fail`` (the simulation is held after it failed; ``held`` in ``pssc sim log``)

``--session ID``
   the session, by id or name (default: the only one)

``--json``
   print one JSON object

``--rerun``
   run the failed test again first (after a ``pssc sim reload``, say)

``pssc sim end``
~~~~~~~~~~~~~~~~

end a simulation (its testbench finishes)

``--session ID``
   the session, by id or name (default: the only one)

``--json``
   print one JSON object

Machine-readable output:

- ``--json``: unversioned (json)

``pssc dap``
------------

serve the Debug Adapter Protocol on stdin/stdout (an editor's debug adapter; sessions kept by the workspace daemon)

``pssc cov``
------------

read a coverage report (pssc test --cov)

``pssc cov gaps``
~~~~~~~~~~~~~~~~~

every statement, branch arm, register and field bin the tests never reached, with source context

``REPORT``
   a coverage report: pssunit.coverage/1 JSON (pssc test --cov x.json) or a covsight database (--cov x.cdb) (required)

``--kind KIND``
   only gaps of this kind (repeatable) (one of ``statement``, ``arm``, ``register``, ``bin``)

``--context N``
   source lines shown before and after a gap (default 2)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the report's directory)

``--json``
   print one JSON document (schema pssc.cov-gaps/1)

Machine-readable output:

- ``--json``: ``pssc.cov-gaps/1`` (json), schema ``docs/schemas/pssc.cov-gaps-1.json``

``pssc cov diff``
~~~~~~~~~~~~~~~~~

the statements and branch arms never reached on lines added or changed since a git revision (the working tree against --base)

``REPORT``
   a coverage report: pssunit.coverage/1 JSON (pssc test --cov x.json) or a covsight database (--cov x.cdb) (required)

``--base REF``
   the git revision to compare with (a branch, tag or commit) (required)

``--kind KIND``
   only gaps of this kind (repeatable) (one of ``statement``, ``arm``, ``register``, ``bin``)

``--context N``
   source lines shown before and after a gap (default 2)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the report's directory)

``--json``
   print one JSON document (schema pssc.cov-diff/1)

Machine-readable output:

- ``--json``: ``pssc.cov-diff/1`` (json), schema ``docs/schemas/pssc.cov-diff-1.json``

``pssc cov report``
~~~~~~~~~~~~~~~~~~~

statements, branch arms, registers and field bins hit, in all and per scope and register

``REPORT``
   a coverage report: pssunit.coverage/1 JSON (pssc test --cov x.json) or a covsight database (--cov x.cdb) (required)

``--json``
   print one JSON document (schema pssc.cov-report/1)

Machine-readable output:

- ``--json``: ``pssc.cov-report/1`` (json), schema ``docs/schemas/pssc.cov-report-1.json``

``pssc cov lcov``
~~~~~~~~~~~~~~~~~

statement and branch-arm coverage as an LCOV tracefile (genhtml, editors)

``REPORT``
   a coverage report: pssunit.coverage/1 JSON (pssc test --cov x.json) or a covsight database (--cov x.cdb) (required)

``-o FILE, --output FILE``
   the file to write (required)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the report's directory); a file found there is written relative to the cwd

Machine-readable output:

- ``-o``: ``lcov`` (lcov-file)

``pssc cov cobertura``
~~~~~~~~~~~~~~~~~~~~~~

statement and branch-arm coverage as Cobertura XML (CI coverage views)

``REPORT``
   a coverage report: pssunit.coverage/1 JSON (pssc test --cov x.json) or a covsight database (--cov x.cdb) (required)

``-o FILE, --output FILE``
   the file to write (required)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the report's directory); a file found there is written relative to the cwd

Machine-readable output:

- ``-o``: ``cobertura`` (xml-file)

``pssc cov merge``
~~~~~~~~~~~~~~~~~~

sum several coverage reports into one (the runs of a CI matrix, say)

``REPORT``
   a coverage report: pssunit.coverage/1 JSON (pssc test --cov x.json) or a covsight database (--cov x.cdb) (required)

``-o FILE, --output FILE``
   the report to write: a covsight database if it ends in .cdb (with each run's tests when every REPORT is a .cdb), else pssunit.coverage/1 JSON (required)

Machine-readable output:

- ``-o``: ``pssunit.coverage/1`` (json-file)
- ``-o``: ``ucis-ncdb`` (cdb-file)

``pssc trace``
--------------

read a test trace (pssc test --trace)

``pssc trace show``
~~~~~~~~~~~~~~~~~~~

the trace's events, one per line with its source line; or one event's line in context (--at); or the events that ran a line (--line)

``TRACE``
   a trace file, NDJSON or binary (.pstrace) (pssc test --trace writes .pssunit/traces/<test-id>.ndjson) (required)

``--kind K``
   only events of this kind: stmt, call, return, check, access, branch, ... (repeatable)

``--coro N``
   only events of coroutine N

``--at IDX|TIME``
   event IDX and its source line, with the lines around it; in a simulation trace also a time with a unit (1.234us): the last event at or before it

``--line FILE:N``
   every event that ran line N of FILE (a path or its trailing part)

``--context N``
   source lines shown before and after --at's line (default 2)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the run's directory)

``--image ZBC``
   the model image (.zbc) the run loaded: source lines of events that have only a src_ref (the C engine's); default: the trace header's 'image', if any

``--json``
   print one JSON document (schema pssc.trace-show/1)

Machine-readable output:

- ``--json``: ``pssc.trace-show/1`` (json), schema ``docs/schemas/pssc.trace-show-1.json``

``pssc trace why``
~~~~~~~~~~~~~~~~~~

where a register's last written value came from: the write, its statement, and each value it read back to the operation's inputs (needs a --trace all\|fail trace of a debug image)

``TRACE``
   a trace file (as for show) (required)

``REG[.FIELD]``
   a register path as the trace names it (ch[1].regs.csr), or one of its fields (ch[1].regs.csr.mode: the last write that changed it) (required)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the run's directory)

``--image ZBC``
   the model image (.zbc) the run loaded: source lines of events that have only a src_ref (the C engine's); default: the trace header's 'image', if any

``--json``
   print one JSON document (schema pssc.trace-why/1)

Machine-readable output:

- ``--json``: ``pssc.trace-why/1`` (json), schema ``docs/schemas/pssc.trace-why-1.json``

``pssc trace slice``
~~~~~~~~~~~~~~~~~~~~

every access to a register, in trace order, each with the statement that made it

``TRACE``
   a trace file (as for show) (required)

``--reg REG``
   a register path as the trace names it (ch[1].regs.csr) (required)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the run's directory)

``--image ZBC``
   the model image (.zbc) the run loaded: source lines of events that have only a src_ref (the C engine's); default: the trace header's 'image', if any

``--json``
   print one JSON document (schema pssc.trace-slice/1)

Machine-readable output:

- ``--json``: ``pssc.trace-slice/1`` (json), schema ``docs/schemas/pssc.trace-slice-1.json``

``pssc trace diff``
~~~~~~~~~~~~~~~~~~~

the first bus access or import where two runs' traces differ (exit 1 when they do)

``A``
   a trace file (as for show) (required)

``B``
   the trace to compare it with (required)

``--source-root DIR``
   a directory relative source paths are read from (repeatable; then the cwd, then the run's directory)

``--image ZBC``
   the model image (.zbc) the run loaded: source lines of events that have only a src_ref (the C engine's); default: the trace header's 'image', if any

``--json``
   print one JSON document (schema pssc.trace-diff/1)

Machine-readable output:

- ``--json``: ``pssc.trace-diff/1`` (json), schema ``docs/schemas/pssc.trace-diff-1.json``

``pssc daemon``
---------------

the workspace daemon (pssd) that serves ``pssc test`` and ``pssc model``

``ACTION``
   (required; one of ``start``, ``status``, ``stop``)

``--json``
   print one JSON object

Machine-readable output:

- ``--json``: unversioned (json)

``pssc help``
-------------

describe the commands (--json: every command, its arguments and output schemas)

``COMMAND``
   the command to describe (default: all) (optional)

``--json``
   print one JSON document (schema pssc.help/1)

``--rst``
   print the CLI reference page (docs/cli-reference.rst)

Machine-readable output:

- ``--json``: ``pssc.help/1`` (json), schema ``docs/schemas/pssc.help-1.json``

``pssc targets``
----------------

list available targets

``--overrides TARGET``
   print TARGET's published override surface -- the methods a mid-weight extension may subclass, and what each promises. With no TARGET, every target that publishes one

``pssc sv-core-path``
---------------------

print the path to the bundled SV core package (pssc_reg_pkg)

``--file``
   print the path to pssc_reg_pkg.sv instead of its directory

``pssc sv-pssvm-path``
----------------------

print the directory of the bundled SV engine package (pssvm_pkg), which runs a ``pssc build --image`` image in a simulation

``--file``
   print the path to pssvm_pkg.sv instead of its directory

``--py``
   print the PYTHONPATH the simulation needs for the pyvm engine (pssvm_hif_pkg over pyhdl-if: pssc and pssvm_dpi_shim)

``--lib``
   print the path to the C engine's shared library (built on first use) that a simulation links for the native engine (+define+PSSVM_DPI, class pssvm_engine_dpi)

``pssc c-core-path``
--------------------

print the directory of the bundled C core seam headers (pssc_mem\*.h)

``--file NAME``
   print the path to a specific header (default: pssc_mem.h)

``pssc cpp-core-path``
----------------------

print the directory of the bundled C++ core header (pssc_reg.hpp)

``--file NAME``
   print the path to a specific header (default: pssc_reg.hpp)
