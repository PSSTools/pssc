``pssc test``
=============

Discover and run `pssunit` tests.

``pssunit`` is a unit-test framework written in PSS, for PSS; ``pssc test`` is
*a* runner of it, not the definition of it. What makes an action a test is that
it derives from ``pssunit_pkg::test_a`` — nothing on this page is required to
write one, and a second runner implementing the same discovery rule, report
format and exit codes is a conforming one.

.. code-block:: text

   pssc test [SRC-SPEC...] [selection] [execution] [reporting]

The framework package is injected ahead of your sources automatically, so a
suite needs nothing installed and names no framework file.

Naming what to run
------------------

A **src-spec** is any of four things, and a bare ``pssc test`` means the current
directory — running it inside a suite should mean the suite.

============================ ==================================================
spec                         expands to
============================ ==================================================
``tests/smoke_a.pss``        that file
``tests/``                   every ``*.pss`` under it, recursively
``tests/*_a.pss``            the glob's matches
``@files.f``                 one spec per line, ``#`` comments stripped,
                             relative to the filelist's own directory
============================ ==================================================

This is deliberately looser than ``pssc compile``, which takes an explicit list
of files. A suite is a directory that grows files, not a list somebody maintains
by hand.

Three properties of the expansion are contract rather than implementation:

**Order is stable.** Every expansion sorts explicitly rather than inheriting
whatever order the filesystem returns, because ``pssc`` has been bitten by file
order before. Two runs over the same tree translate the same way.

**Overlap is fine, duplicates are not.** ``pssc test tests/ tests/smoke_a.pss``
is a reasonable thing to type; handing the same file to the parser twice is not.
Duplicates are dropped, not rejected.

**A spec that matches nothing is an error**, not an empty contribution::

   pssc: error: source spec matches nothing: 'tset/'

A mistyped path that quietly contributes no files ends as a green run over a
suite that was never loaded — which is the single worst thing a test runner can
do, so it is an error: exit 2, like PSS that will not compile.

Selecting tests
---------------

===================== =========================================================
option                selects
===================== =========================================================
``SUITE::TEST``        tests whose ID matches the glob (any src-spec with ``::``)
``-k EXPR``           tests whose name matches ``EXPR``
``-m TAG``            tests tagged ``TAG`` (``.tags`` or ``@pssunit_pkg::tag``)
``--lf``              tests that failed last time
``--changed``         tests whose slice changed since they last passed
``--base REV``        with ``--changed``: tests whose slice differs at git ``REV``
``--watch``           all, then ``--changed`` again on each save, until Ctrl-C
``--fixture NAME``    tests rooted in component ``NAME``
``--group NAME``      tests annotated ``@pssunit_pkg::test{.group = "NAME"}``
===================== =========================================================

Different kinds combine with AND; several ID globs, or several ``-m``, select
any of them. ``-k`` tries a glob against the short name and the
qualified name, then falls back to a substring test, so ``-k smoke`` and
``-k 'smoke_*'`` both do what they look like.

A src-spec containing ``::`` that is not an existing path is a test-ID glob,
matched against the whole ID exactly as a report prints it (``wb_dma_suite::*``
is every test under that fixture). With only IDs given, the sources are the
current directory:

.. code-block:: text

   $ pssc test 'smoke_f::t_*' -m slow

``--lf`` (``--last-failed``) runs the tests that failed in the last run, read
from ``.pssunit/lastfailed`` in the current directory. Every run updates that
record for the tests it ran and keeps it for the ones it did not, so
``--lf -k a`` does not forget a failure outside ``a``. When none of the selected
tests is recorded as failing, the whole selection runs, and a line says so.

``--changed`` skips each test that last passed with the same *slice*, the same
verdict-changing options (``--platform``, ``--targets``, ``--strict``, ...)
and, when ``--seed`` is given, the same seed; a line says how many it skipped.
A test's slice is its own source, the source of every action and function it
reaches by name (and its fixture's set-up and tear-down), the source outside
all actions and functions, and the pssc and engine versions. So an edit inside
one operation re-runs the tests that call it, and an edit to a type, a register
or a constant re-runs them all; moving a declaration re-runs nothing.
``--collect-only --json`` shows each test's slice as ``calls``. The record is
``.pssunit/results.json``; the first ``--changed`` writes it, and from then on
every run keeps it up to date. A failed test is never skipped.

``--changed --base REV`` compares with a git revision instead of the record:
it runs each test whose slice differs from its slice in the sources as they
are at ``REV`` (``git show REV:FILE``), so ``pssc test --changed --base
origin/main`` runs what a branch can have affected. A test absent at ``REV``
runs; a source absent at ``REV`` counts as empty; when the sources at ``REV``
do not translate, every selected test runs. The record is neither read nor
started. Outside a git work tree, or with no such ``REV``, it exits 5.

``--watch`` runs the selection, then waits; each time a source is saved (the
src-specs are resolved again, so a new file in a suite directory counts) it
runs again with ``--changed``, so only the tests the edit reaches run. Each
round goes to the daemon, which keeps the pipeline warm; its output (with
``--json``, its events, ``run.start`` to ``run.end``) appears as it ends.
Ctrl-C stops watching; the exit code is the last round's.

``--image FILE`` runs the tests of an image file ``pssc build SRC --image
FILE`` wrote, with no sources and no lowering: positionals are test IDs only,
and ``-k``, ``-m``, ``--fixture``, ``--group``, ``--lf``, ``--collect-only``,
``-j`` and ``--trace`` work as usual. What needs the sources (``--changed``,
``--base``, ``--watch``, ``--cov``, ``--targets``, a src-spec) exits 2. The
replay line carries ``--image FILE``. The file's contents are in
:doc:`test-internals`.

``--group`` matches **exactly**, not as a glob. A group is a name the author
chose, and a fuzzy match would silently pull in a neighbouring group; selecting
several groups at once is what ``-k`` is for.

**A selection that matches nothing exits 5.**

.. code-block:: text

   $ pssc test demo.pss -k nope
   pssc: error: no tests matched -k nope

Not a pass with zero tests. A filter that has gone stale — the test it named was
renamed — otherwise reports green forever.

Seeing what would run
~~~~~~~~~~~~~~~~~~~~~

``--collect-only`` lists the tests and does not run them. It answers "did
discovery find what I meant?" separately from "does it pass?", which are the two
questions a test runner gets wrong in different ways.

.. code-block:: text

   $ pssc test demo.pss --collect-only
   alu_tests_c
     add_a
     overflow_a  [arith]
     mul_wide_a  [skip: needs the wide multiplier]
     div_by_zero_a  [xfail: issue #412]

   4 tests collected

Annotation markers are shown because this listing is what the reader uses to
answer "will this actually run?" — and a test marked ``skip`` that lists exactly
like one that runs answers it wrongly. ``-v`` adds the qualified name.

``--collect-only --json`` prints the whole catalog as one JSON document with
``"schema": "pssunit.catalog/1"`` (``docs/schemas/pssunit.catalog-1.json``):
for each test its ID, kind (``action`` or ``function``), fixture, declaring
type, ``file``/``line``/``end_line``, group, tags, skip/xfail, property runs,
``expect_error``, ``platform_min`` and base chain.

Fixtures and test IDs
~~~~~~~~~~~~~~~~~~~~~

A test's ID is ``<fixture>::<test>``. A component derived from another one
inherits its tests, and runs each under its own root: if ``base_f`` declares
``t1`` and ``derived_f : base_f`` declares ``t2``, the catalog holds
``base_f::t1``, ``derived_f::t1`` and ``derived_f::t2``. A component function
marked ``@pssunit_pkg::test`` is a test too. The file is not part of the ID,
so moving a file keeps every ID.

Selecting by line
~~~~~~~~~~~~~~~~~

``FILE:LINE`` as a src-spec compiles ``FILE`` and selects the tests whose
body spans ``LINE`` — the line an editor's cursor is on. A line outside every
test selects nothing, and ``pssc test`` exits 5:

.. code-block:: text

   $ pssc test fixtures.pss:7 --collect-only
   base_f
     t1
   derived_f
     t1

   2 tests collected

Running
-------

.. code-block:: text

   $ pssc test demo.pss --seed 7
   alu_tests_c
     add_a ....................................... PASS    0.00s  1/1 checks
     overflow_a .................................. FAIL    0.00s  0/1 checks
     mul_wide_a .................................. SKIP    0.00s  (needs the wide multiplier)
     div_by_zero_a ............................... XFAIL   0.00s  0/1 checks

   FAIL alu_tests_c::overflow_a
     1 of 1 check(s) failed
     --- model output ---
     not ok overflow_a :: 8-bit wrap
     replay: pssc test demo.pss alu_tests_c::overflow_a --seed 7

   1 passed, 1 failed, 1 skipped, 1 xfailed    seed 7    0.00s

Every failure block ends with a **replay line**: the command that re-runs that
one test exactly as it just ran, seed included. It is the highest-value line in
the report and it costs nothing to emit. The alternative is the user
reconstructing the seed and the filter by hand, which is how a reproducible
failure turns into an intermittent one.

The replay line names the test by its ID (``alu_tests_c::overflow_a``), not by
``-k``, so a same-named test in another suite is not run with it. It keeps the
run's sources and the options that change a verdict -- ``--platform``,
``--reg-semantics``, ``--targets``, ``--no-debug``, ``--strict``, ``--runxfail``, ``--noskip`` -- and
drops the rest (selection, output, ``-j``). Run from the same directory, it
gives the same failure. The same command is the ``repro`` field of a
``--json`` ``test.end`` event and ends a JUnit ``<failure>``.

The seed
~~~~~~~~

``--seed N`` fixes the solver seed. The default is random **and always
reported**, on the summary line and in the replay line of every failure, so a
run that failed once can always be run again.

``--seeds N`` runs every test ``N`` times, re-solving each time: the first run
uses the run's seed, the rest seeds drawn from it and the test's name (so the
same command draws the same seeds). The first run that does not pass is the
test's result, and its line names that run's seed. A
``@pssunit_pkg::property`` test runs the larger of ``N`` and its own count, and
only property tests record failing seeds under ``.pssunit/regressions/``.

Parallel runs
~~~~~~~~~~~~~

``-j N`` (``--jobs N``) runs the tests in ``N`` forked processes, each building
the images it needs. The results come back in collection order, and ``-x``
stops at the same failure, so a ``-j`` run reports exactly what a
one-process run does.

Statuses
~~~~~~~~

========= ==================================================================
status    meaning
========= ==================================================================
``PASS``  ran, and every check it recorded held
``FAIL``  ran, and said something untrue
``ERROR`` never got far enough to say anything — a solver or runtime failure
``SKIP``  annotated ``.skip``; not run. The reason is shown on the line
``XFAIL`` annotated ``.xfail``, and duly failed
``XPASS`` annotated ``.xfail``, but passed
========= ==================================================================

``ERROR`` is distinct from ``FAIL`` on purpose: "the design produced the wrong
value" and "the test never ran" are different problems with different fixes.

A run that stops — a divide by zero, or a PSS ``error()`` or ``fatal()`` —
says where, in front of the message: the ``file:line:col`` of the statement
that stopped it, in the debug image and in the release one::

    suite.pss:13:9: error: stopped at 3

``XPASS`` is a **failure**, and the report says why::

   XPASS xp_tests_c::fixed_a
     expected to fail, but passed (issue #99)

An unexpected pass means a stale annotation. Accepting it silently would defeat
the point of the mark — the bug it tracks may well be fixed, but nobody has said
so yet.

Escape hatches
~~~~~~~~~~~~~~

An annotation you cannot turn off from the command line is a place for a real
failure to hide. Two flags answer the two questions you actually have, without
editing the source to find out:

``--noskip``
   Run tests annotated ``.skip = true`` anyway — *is this still broken?*

``--runxfail``
   Report tests annotated ``.xfail = true`` by what they actually did — *what is
   it doing?* An ``XFAIL`` becomes a plain ``FAIL``, and the exit code follows.

Under ``--runxfail`` the annotation and the verdict disagree by design. The
runner settles the verdict, and both the human report and the CTRF file read
what the runner settled on rather than re-deriving it from the annotation —
otherwise the report would print ``xfail`` while the process exited on the raw
outcome, which is two answers to one question.

Other execution flags
~~~~~~~~~~~~~~~~~~~~~

``-x``, ``--exitfirst``
   Stop after the first failure. The report covers what ran.

``--strict``
   Fail a test that recorded no checks at all::

      FAIL alu_tests_c::mul_wide_a
        no checks recorded (--strict)

   A test that asserts nothing passes for the same reason an empty one does.
   Off by default, because a suite is often written outside-in and a test body
   that merely *runs* is a real intermediate state; on in CI, where it isn't.

``--no-debug``
   Lower the release image instead of the debug one. By default ``pssc test``
   lowers for debugging: a marker at the start of every PSS statement, function
   calls kept as calls (so each has a named frame), and named locals. The
   debug image also carries the text of the PSS sources it was built from, so
   a debugger can show lines with only the image at hand. A test behaves the
   same either way; ``--no-debug`` exists to run exactly the image
   a release build would, when a difference is suspected.

``--targets T1,T2``
   The operation-model targets the code under test must be legal for. Before
   any test runs, ``pssc test`` runs each target's gate — the checks
   ``pssc compile`` runs before it writes a file — and a refusal is exit 2,
   with its location::

      pssc: error: op-model-c refuses dut_c: 1 array parameter(s) or result(s) cannot be lowered to C yet
        src/dut.pss:2:5: dut_c::sum: an array parameter 'a' is not supported yet

   The default is ``[tool.pssunit] targets = [...]`` in ``./pyproject.toml``,
   else ``op-model-c``; ``--targets none`` gates nothing. Only the code under
   test is gated: the component instances the test roots hold, not the test
   roots. A file that declares a test, and ``pssunit_pkg``, are exempt — a test
   may use what no target renders. A device component with no operations is
   not gated.

``--platform fake``
   Each test runs on a new register fake (``pv.RegisterFake``): every register
   keeps its value from reset, field kinds (``RC``, ``W1C``, ``W1S``, ``RO``,
   ``WO``) from ``@pssunit_pkg::field_access`` apply, and a failure card shows
   the decoded register. Without it a test runs on plain sparse memory. A user
   import nothing implements returns 0 either way. It needs the debug image
   (not ``--no-debug``).

``--platform py:MOD[:CLASS]``
   A Python platform for the run. ``pssc test`` imports module ``MOD`` (the
   current directory is searched first) and applies the doubles its
   ``@pv.implements``, ``@pv.mock`` and ``@pv.spy`` decorators declared to
   every test; with ``:CLASS``, each test also runs on a new ``CLASS()`` — a
   Python device model with the methods of the op-model-py import API
   (``read32``, ``write32``, ``message``, one per user import). Bindings are
   scoped to each test: nothing one test's platform holds is seen by the
   next. The PSS source never names the platform; the run binds it::

      pssc test tests/ --platform py:board_model:Board

   The default is ``[tool.pssunit] platform = "py:..."`` in
   ``./pyproject.toml``, else none. A module that fails to import (or a
   ``CLASS`` it lacks) is exit 3: one line on stdout, the traceback in
   ``.pssunit/platform.log``.

``--platform replay:TRACE``
   Replay a run ``--trace`` recorded. ``TRACE`` is a trace file (one test) or
   the directory ``--trace`` wrote (``.pssunit/traces``; each test replays its
   own file). Each bus read returns the recorded data and each user import
   the recorded return value; each access and import must be the one recorded,
   in order. The first that is not fails the test (FAIL, kind ``diverged``)::

      pssc test tests/ --trace all --platform py:board_model:Board
      pssc test tests/ --platform replay:.pssunit/traces
      FAIL r_f::t_rw
        t.pss:31:9: first divergent access (step 2 of 3 recorded): expected
        write32 0x1000 = 0x5a (t.pss:31), got write32 0x1004 = 0x5a

   A test that ends before the recording does diverges too ("got the end of
   the test"). No doubles or device model are needed: the recording stands in
   for them. Without ``--seed`` the replay runs with the recorded seed, so
   random choices are the recorded ones. ``pssc dbg --platform replay:TRACE``
   and the pytest plugin's ``pssunit_platform = replay:TRACE`` replay the same
   way, so a recorded run can be stepped in the debugger. Interrupts the
   recorded run raised are raised again while the replay serves the access or
   import that raised them, so the interrupt handler runs at the same point.
   A binary trace (``.pstrace``, the C engine's) replays too; in a
   directory, a test's ``.ndjson`` is taken before its ``.pstrace``.

``--reg-semantics FILE``
   Field kinds from a register description: an IP-XACT ``.xml`` (``readAction``
   ``clear`` gives ``RC``, ``modifiedWriteValue`` ``oneToClear``/``oneToSet``
   ``W1C``/``W1S``, ``read-only``/``write-only`` ``RO``/``WO``, a volatile
   read-write field ``VOLATILE``), a SystemRDL ``.rdl`` (``rclr`` gives ``RC``,
   ``woclr``/``woset`` ``W1C``/``W1S``, ``sw = r``/``w`` ``RO``/``WO``, a
   read-write field the hardware writes, clears or sets ``VOLATILE``; needs
   ``systemrdl-compiler``, ``pip install pssc[rdl]``; a file without an
   ``addrmap`` names its registers from each ``regfile``) or a JSON table ``{"REG": {"FIELD":
   "KIND"}}``. A register's entry is the longest ``REG`` (its path in the
   description, array indices left out) that ends the register's PSS instance
   path, so ``ch.csr`` names ``regs.ch[1].csr``. The fake and the register
   checks use them; a ``@pssunit_pkg::field_access`` annotation wins over the
   file, and a field the PSS register does not declare is ignored. A file that
   cannot be read is an error before any test runs (exit 5). ``pssc dbg start --reg-semantics FILE``, the
   VS Code launch's ``regSemantics`` and the pytest plugin's
   ``pssunit_reg_semantics`` take the same file::

      pssc test tests/ --platform fake --reg-semantics regs.xml

``--platform sim:SESSION``
   Run the tests in a server-mode simulation (``+pssvm_serve=SESSION``, see
   :doc:`simulation`) instead of pyvm. ``pssc test`` builds the image of the
   sources' root (as ``pssc build --image``) to ``.pssunit/sim/SESSION.zbc``,
   hot reloads it into the session when it differs from the image last sent
   there, runs the selected tests in the session and prints the failures and
   the counts; exit 1 when one fails, 3 when the session cannot be reached or
   refuses the reload. ``sim:`` (no name) is the only session. With
   ``--watch`` each save rebuilds, reloads and reruns, and the simulation keeps
   running. Every selected test runs each round (``--changed`` selects
   nothing); ``--json``, ``--cov``, ``--trace``, ``--image``, ``--junit`` and
   ``--seeds`` are refused. Given on the command line only.

   Both platforms, the doubles and the Python API are in
   :doc:`platform-doubles`.

Under pytest
------------

pssc installs a pytest plugin (``pssc.testing.pytest_plugin``, entry point
``pytest11``). It does nothing until the pytest configuration names PSS
sources, so a pytest run in an environment with pssc installed collects
nothing new::

   [tool.pytest.ini_options]
   pssunit_paths = ["pss"]                # files or directories, as for pssc test
   pssunit_platform = "py:board:Board"    # optional; as --platform

Each ``.pss`` file under ``pssunit_paths`` that pytest visits becomes a
collector of the tests declared in it, with node IDs
``pss/suite.pss::SUITE::TEST``. They run as ``pssc test`` runs them: one
translation for the session, the same seed for every test (``pytest --pss-seed N``,
else a fresh one), pass, skip and xfail as pytest's own, and fail, error and
xpass as a failed item whose text ends with the ``pssc test`` replay line.
Tags are markers, so ``-m TAG`` selects as it does here.

The ``pss`` fixture opens sessions (``pssc.testing.vm.Session``) on the same
translation, for a Python test that calls operations itself:

.. code-block:: python

   def test_twice(pss):
       s = pss.load("calc_c")
       assert s.call(s.construct(), "twice", 21) == 42

``pss.load(root, platform=None, seed=0, functions=("*",), use=())`` takes
``pv.load``'s arguments after the sources. Without ``pssunit_paths`` the
fixture fails the test that asks for it.

Exit codes
----------

===== ====================================================================
code  meaning
===== ====================================================================
``0`` every test that ran passed (or was skipped, or xfailed as expected)
``1`` the suite ran and a test failed. **A report exists.**
``2`` the model did not get as far as running — a src-spec that names
      nothing, PSS that will not parse or elaborate, a test whose base type
      does not resolve, code under test that a declared target refuses
      (``--targets``). **No report is written.**
``3`` internal error in ``pssc`` itself; a traceback is printed. Also a
      ``--platform`` that does not load (traceback in
      ``.pssunit/platform.log``)
``5`` nothing selected — no tests found, or none matched ``-k``,
      ``--fixture`` or ``--group``. **No report is written.**
===== ====================================================================

Each outcome has its own code so that a caller — CI, or an agent — can tell
"my test failed" from "my PSS does not compile" without parsing text. ``1``
means the suite executed, so a report is guaranteed to be sitting next to it
and CI should go read it; that guarantee is only worth anything if ``1`` cannot
also mean "the model never ran" (``2``) or "``pssc`` crashed" (``3``), neither
of which produces a report. ``5`` is not a pass: a filter that has gone stale
must not report green.

These codes are ``pssc test``'s. The other subcommands keep theirs (``1``
user error, ``2`` internal error).

Reports
-------

**Human**, on stdout, as above. ``-v`` adds the qualified name to each line and
the traceback to each failure; ``-q`` prints the summary line only:

.. code-block:: text

   $ pssc test demo.pss -q --seed 7
   1 passed, 1 failed, 1 skipped, 1 xfailed    seed 7    0.00s

**CTRF**, on disk. Every run that executes tests writes one, to
``pssunit-ctrf.json`` unless ``--ctrf PATH`` (or its alias ``--report PATH``)
says otherwise; ``--no-ctrf`` suppresses it.

.. code-block:: json

   {
     "reportFormat": "CTRF",
     "specVersion": "0.0.0",
     "generatedBy": "pssc test",
     "results": {
       "tool": {"name": "pssc"},
       "summary": {"tests": 4, "passed": 2, "failed": 1, "skipped": 1,
                   "pending": 0, "other": 0, "start": 0, "stop": 0},
       "tests": [
         {"name": "add_a", "status": "passed", "duration": 0,
          "suite": ["alu_tests_c"], "filePath": "demo.pss", "line": 4,
          "extra": {"qname": "alu_tests_c::add_a", "seed": 7,
                    "checks": {"passed": 1, "failed": 0, "skipped": 0}}}
       ]
     }
   }

**JUnit XML**, on disk, when ``--junit PATH`` asks for it: one ``<testsuite>``
per root component, one ``<testcase>`` per test. A failure's text ends with its
replay command; an error is ``<error>``, an unexpected pass a ``<failure>``, and
a skipped or expected failure ``<skipped>``. The test's output is its
``<system-out>``.

**Events**, on stdout, with ``--json``: one JSON object per line (NDJSON),
schema ``pssunit.events/1`` (``docs/schemas/pssunit.events-1.json``), and nothing
else on stdout, so every line parses. A run is ``run.start`` (seed, test count),
then ``test.start`` and ``test.end`` for each test as it runs, then ``run.end``
(the summary counts and the exit code). ``test.end`` carries the runner's own
status (``pass``, ``fail``, ``error``, ``skip``, ``xfail``, ``xpass``), the
message, the output and the check counts, and on every failing verdict
``repro``, the command that re-runs it. A failed test's ``failures`` lists
where it failed, for an editor to mark: ``file``, ``line``, ``col`` (0 when
not known), ``kind`` and ``message`` of each failed ``pssunit_pkg`` check
(``kind`` is the check, ``message`` its ``what``) and each sanitizer finding
with a source location (``kind`` ``sanitizer <name>``). In parallel runs a test's ``test.start``
comes just before its ``test.end``, in collection order.

.. code-block:: text

   $ pssc test demo.pss --json --seed 7 -k add_a
   {"event":"run.start","schema":"pssunit.events/1","tests":1,"seed":7}
   {"event":"test.start","id":"alu_tests_c::add_a","name":"add_a","suite":"alu_tests_c","file":"demo.pss","line":4}
   {"event":"test.end","id":"alu_tests_c::add_a","status":"pass","duration_ms":0,"seed":7}
   {"event":"run.end","summary":{"passed":1,"failed":0,"error":0,"skipped":0,"xfailed":0,"xpassed":0},"exit":0,"duration_ms":3}

With ``--mutate``, a green run's ``run.end`` is followed by one ``mutant.end``
per mutant (``file``, ``line``, ``col``, ``kind``, ``old``, ``new``, ``status``
-- ``killed``, ``survived``, ``invalid`` or ``not covered`` -- ``by``, the test
that killed it, and ``tests``, how many ran on it), then ``mutate.end`` with
``killed``, ``survived``, ``uncovered``, ``invalid``, ``score`` (percent, or
null) and ``exit``.

With ``--collect-only``, ``--json`` prints the catalog instead (above).

All of these renderings are built from the same ``list[Result]``, and neither is derived
from the other's text — which is what stops the summary a human reads and the
report a machine reads from drifting apart.

CTRF allows five statuses and ``pssunit`` distinguishes six, so the collapse is
lossy and ``rawStatus`` carries the original. The full field and status mapping,
and a complete worked document, are in :doc:`ctrf-report`.

Option reference
----------------

======================= =======================================================
option                  effect
======================= =======================================================
``SRC-SPEC...``         file, directory, glob or ``@filelist``; default ``.``
``SUITE::TEST``          run only tests whose ID matches the glob
``-k EXPR``             run only tests whose name matches ``EXPR``
``-m TAG``              run only tests tagged ``TAG``; repeatable
``--lf``                run only last run's failures (``--last-failed``)
``--changed``           skip tests that passed with nothing in their slice changed
``--base REV``          with ``--changed``: run tests whose slice differs at git ``REV``
``--watch``             run again with ``--changed`` on each save, until Ctrl-C
``--image FILE``        run the tests of image ``FILE`` (``pssc build --image``)
``--fixture NAME``      run only tests rooted in component ``NAME``
``--group NAME``        run only tests in annotation group ``NAME``
``--noskip``            run tests annotated ``.skip = true`` anyway
``--runxfail``          report ``.xfail`` tests by what they actually did
``--collect-only``      list the tests that would run; do not run them
``--json``              print NDJSON events instead of the human report;
                        with ``--collect-only``: the catalog as JSON
``--seed N``            solver seed; default random, and always reported
``--seeds N``           run every test ``N`` times with ``N`` seeds
``-j N``, ``--jobs N``  run the tests in ``N`` processes (same results)
``-x``, ``--exitfirst`` stop after the first failure
``--strict``            fail a test that records no checks
``--no-debug``          lower the release image, not the debug one
``--targets T1,T2``     gate the code under test for these targets first
                        (default ``op-model-c``; ``none``: no gate)
``--platform SPEC``     ``fake``: a register fake per test;
                        ``py:MOD[:CLASS]``: every test gets ``MOD``'s doubles
                        and a new ``CLASS()`` platform
                        ``replay:TRACE``: reads and imports from a recorded
                        ``--trace``; fails at the first divergent access
``--reg-semantics F``   register field kinds from IP-XACT ``.xml``, SystemRDL
                        ``.rdl`` or JSON file ``F`` (``field_access`` annotations win)
``--no-doctest``        do not run the ``pss-example`` blocks of doc comments
                        as tests (see "Examples in doc comments" in
                        :doc:`writing-tests`)
``--mutate``            after a green run, run each mutant of the model (a
                        flipped operator, a constant off by one, a register
                        offset moved by a word, a swapped field) on the tests
                        that reach it, in a child ``pssc test`` with a
                        timeout; list the mutants no test kills (``SURVIVED``)
                        or reaches (``NOT COVERED``) and the mutation score;
                        ``-j N`` runs N mutants at once; exit 1 if any
                        survive, 2 if the run was not green; with
                        ``--json``, one ``mutant.end`` event per mutant
                        and a ``mutate.end`` with the counts and score
``--mutate-kinds K,K``  with ``--mutate``, only these kinds of mutant:
                        ``operator``, ``constant``, ``offset``, ``field``
``--mutate-max N``      with ``--mutate``, run at most N mutants, a sample
                        picked by ``--seed`` (kept in source order)
``--engine ENGINE``     the engine the tests run on: ``py`` (the Python VM)
                        or ``c`` (the C engine; a test it cannot run
                        yet is an ERROR that says why, see
                        "What the C engine runs"); ``gen-py`` or
                        ``gen-sv`` (the generated code, checked against a
                        ``py`` run; see "Generated code" in
                        :doc:`writing-tests`);
                        default ``$PSSC_ENGINE``, else ``py``
``--ctrf PATH``         write the CTRF report to ``PATH``
                        (default ``pssunit-ctrf.json``)
``--no-ctrf``           write no CTRF report
``--report PATH``       same as ``--ctrf PATH``
``--junit PATH``        also write a JUnit XML report to ``PATH``
``--cov PATH``          write statement and branch-arm coverage (JSON) to
                        ``PATH``; needs the debug image
``--cov-scope COMP``    count coverage only in component type ``COMP``, its
                        sub-components, bases and derived types (repeatable;
                        default: the fixtures' ``@pssunit_pkg::under_test``,
                        else all but tests, mocks and libraries)
``--trace MODE``        write each test's events, with source lines, as
                        NDJSON: ``all`` for every test, ``fail`` for failing
                        ones (default ``off``); needs the debug image
``--trace-dir DIR``     where ``--trace`` writes ``<test-id>.ndjson``
                        (default ``.pssunit/traces``)
``-v``, ``--verbose``   per-test detail: qualified name, traceback
``-q``, ``--quiet``     summary line only
======================= =======================================================

What ``--cov`` measures, the boundary and the report: :doc:`coverage`.

What the C engine runs
----------------------

``--engine c`` runs every operation the Python VM does, and the same
values come out for the same seed: function tests, and action tests whose
activities have sequences, ``parallel`` (including ``join_first``),
``repeat``, ``if``, ``select``, recursive calls and solve scopes. Random
fields and ``with`` constraints, including constraints on a loop's index,
are solved as the Python VM solves them; a choice of component instance,
state pools, buffer pools and resource pools of up to 64 instances
(``lock`` and ``share``, also across parallel branches) pick the same
objects and instances.

It refuses, as an ERROR that names the action and says to use
``--engine py``, an activity with a stream pool, a resource pool of more
than 64 instances, or an action in no instance the model knows. An
unsatisfiable solve, pick or claim fails on both engines, but on C the
message does not carry the Python VM's text.

See also
--------

:doc:`annotations`
   How ``@pssunit_pkg::test`` reaches the runner, and what each attribute does.
:doc:`ctrf-report`
   The CTRF document: field mapping, status collapse, and a worked example.
