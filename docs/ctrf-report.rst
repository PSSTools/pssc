The CTRF report
===============

Every ``pssc test`` run that executes tests writes a `CTRF
<https://github.com/ctrf-io/ctrf>`_ JSON report — an open, tool- and
language-neutral schema for test results. It goes to ``pssunit-ctrf.json``
unless :doc:`test-command` says otherwise, and ``--no-ctrf`` suppresses it.

This page is for two readers: the CI integrator wiring the file into a
dashboard, and whoever writes the **second** runner. ``pssunit`` is a portable
framework and ``pssc`` is one runner of it, so the result format is part of the
conformance surface — two implementations' output should be comparable
document-to-document. Choosing an existing standard over TAP or JUnit XML is
what makes that cheap; nothing here is invented.

The human summary and this document are rendered from the same in-memory
``list[Result]``. Neither is parsed out of the other's text, so the two cannot
drift into disagreeing about what happened.

.. note::

   Exit code ``1`` means the suite ran and something failed — which is exactly
   the guarantee that a report is sitting next to it. Exit ``2`` (the model did
   not compile), ``3`` (internal error) and ``5`` (nothing selected) write
   **no** report. A CI job that reads the file unconditionally should expect
   it to be absent on ``2``, ``3`` and ``5``.

A complete document
-------------------

From a six-test run producing one of every status. The traceback in the last
entry is elided at ``...``; everything else is verbatim.

.. code-block:: json

   {
     "reportFormat": "CTRF",
     "specVersion": "0.0.0",
     "generatedBy": "pssc test",
     "results": {
       "tool": {"name": "pssc"},
       "summary": {
         "tests": 6, "passed": 2, "failed": 3, "skipped": 1,
         "pending": 0, "other": 0,
         "start": 1788461525382, "stop": 1788461525382
       },
       "tests": [
         {
           "name": "add_a",
           "status": "passed",
           "duration": 0,
           "suite": ["alu_tests_c"],
           "extra": {
             "qname": "alu_tests_c::add_a",
             "seed": 7,
             "checks": {"passed": 1, "failed": 0, "skipped": 0}
           },
           "filePath": "demo.pss",
           "line": 4
         },
         {
           "name": "overflow_a",
           "status": "failed",
           "duration": 0,
           "suite": ["alu_tests_c"],
           "extra": {
             "qname": "alu_tests_c::overflow_a",
             "seed": 7,
             "checks": {"passed": 0, "failed": 1, "skipped": 0}
           },
           "filePath": "demo.pss",
           "line": 12,
           "tags": ["arith"],
           "message": "1 of 1 check(s) failed",
           "stdout": "not ok overflow_a :: 8-bit wrap\n"
         },
         {
           "name": "mul_wide_a",
           "status": "skipped",
           "duration": 0,
           "suite": ["alu_tests_c"],
           "extra": {"qname": "alu_tests_c::mul_wide_a", "seed": 7},
           "rawStatus": "skip",
           "filePath": "demo.pss",
           "line": 20,
           "message": "needs the wide multiplier"
         },
         {
           "name": "div_by_zero_a",
           "status": "passed",
           "duration": 0,
           "suite": ["alu_tests_c"],
           "extra": {
             "qname": "alu_tests_c::div_by_zero_a",
             "seed": 7,
             "checks": {"passed": 0, "failed": 1, "skipped": 0}
           },
           "rawStatus": "xfail",
           "filePath": "demo.pss",
           "line": 27,
           "message": "1 of 1 check(s) failed",
           "stdout": "not ok div_by_zero_a :: divide traps\n"
         },
         {
           "name": "fixed_a",
           "status": "failed",
           "duration": 0,
           "suite": ["xp_tests_c"],
           "extra": {
             "qname": "xp_tests_c::fixed_a",
             "seed": 7,
             "checks": {"passed": 1, "failed": 0, "skipped": 0}
           },
           "rawStatus": "xpass",
           "filePath": "xpass.pss",
           "line": 4,
           "message": "expected to fail, but passed (issue #99)"
         },
         {
           "name": "fmt_a",
           "status": "failed",
           "duration": 0,
           "suite": ["err_tests_c"],
           "extra": {"qname": "err_tests_c::fmt_a", "seed": 7},
           "rawStatus": "error",
           "filePath": "err.pss",
           "line": 3,
           "message": "unsupported statement StmtAnnAssign in M1",
           "trace": "Traceback (most recent call last):\n  ...\n"
         }
       ]
     }
   }

The summary counts are CTRF's, not pssunit's
--------------------------------------------

That same run prints this to a human::

   1 passed, 1 failed, 1 error, 1 skipped, 1 xfailed, 1 xpassed    seed 7    0.00s

and this to ``summary``:

.. code-block:: json

   {"tests": 6, "passed": 2, "failed": 3, "skipped": 1, "pending": 0, "other": 0}

**The two do not match, and both are right.** ``summary`` counts statuses
*after* the collapse to CTRF's five, because the alternative is a document whose
summary does not add up to its own ``tests`` array — which is worse than one
that needs explaining. So the expected xfail joins ``passed``, and the
unexpected pass and the error join ``failed``.

If you want pssunit's own six-way counts, derive them from ``rawStatus``; do not
expect them in ``summary``. A dashboard that wants to show "1 xfailed" should
count ``rawStatus == "xfail"`` rather than trying to reconcile the two numbers.

Status mapping
--------------

CTRF allows five statuses and ``pssunit`` distinguishes six, so the collapse is
lossy — and ``rawStatus`` exists precisely to carry a tool's own
pre-normalization status, so nothing is actually lost.

============ ============= ================= ==================================
pssunit      ``status``    ``rawStatus``     note
============ ============= ================= ==================================
``PASS``     ``passed``    *(absent)*
``FAIL``     ``failed``    *(absent)*
``ERROR``    ``failed``    ``error``         not ``other`` — see below
``SKIP``     ``skipped``   ``skip``          reason in ``message``
``XFAIL``    ``passed``    ``xfail``         expected failure; the run stays green
``XPASS``    ``failed``    ``xpass``         a stale annotation is a failure
============ ============= ================= ==================================

Two of these rows are decisions rather than transcriptions:

**An error is ``failed``, not ``other``.** A runtime or solver exception means
the test never got far enough to say anything, which is a different problem from
a failing check — but folding it into ``other`` hides it from every summary and
every dashboard that counts failures, which is the one place it must not be
hidden. ``rawStatus: "error"`` keeps the distinction for anyone who wants it.

**An unexpected pass is ``failed``.** The bug an ``xfail`` tracks may well be
fixed, but nobody has said so yet, and silently accepting the pass defeats the
point of the mark.

The runner settles the verdict; this file reports what it settled on rather than
re-deriving it from the annotation. Under ``--runxfail`` the two disagree by
design, and the document agreeing with the **exit code** matters more than it
agreeing with the annotation — otherwise the report says ``xfail`` while the
process exited on the raw outcome, which is two answers to one question.

Per-test fields
---------------

=================== ============================================================
field               content
=================== ============================================================
``name``            the short action name, as users refer to it
``status``          the CTRF status; see above
``rawStatus``       pssunit's own status, when it differs. Omitted otherwise
``duration``        wall-clock milliseconds, integer
``suite``           ``[root component]`` — the grouping the human report prints
``filePath``        declaration site, from the IR node's ``loc``. Omitted if the
                    front end recorded none
``line``            declaration line, same source and same caveat
``tags``            ``[group]`` from ``@pssunit_pkg::test{.group}``. Omitted
                    when the test is in no group
``message``         failure detail, or the skip/xfail reason
``trace``           traceback, for an ``ERROR``
``stdout``          the test's captured ``message()`` output — per test, not
                    interleaved across the run
``extra.qname``     the fully qualified name
``extra.seed``      the seed **this test** ran with
``extra.checks``    ``{passed, failed, skipped}`` from the ledger. Omitted when
                    the test recorded no checks
=================== ============================================================

Four of these are worth saying more about.

**``name`` is short, ``extra.qname`` is qualified.** The short name is what a
user types after ``-k`` and what they call the test in conversation, so it is
what a dashboard should show. The qualified name is what disambiguates two
tests of the same name under different components, so it is what tooling should
key on.

**``extra.seed`` is per test, not per run.** Today every test in a run shares
the run's seed, so this is the same number throughout — but it is written per
test because a *single failing test* has to be replayable from the report alone,
without the reader also having to find the run that produced it.

**``extra.checks`` is where per-check granularity lives.** CTRF has no
first-class place for it, and flattening it into the test's own pass/fail would
lose the difference between "1 test failed" and "1 of 40 checks failed" — which
are different sizes of problem.

**``suite`` is a list of one.** CTRF models nesting; ``pssunit`` currently has
one level, the root component. It stays a list so that growing a second level
does not change the field's type.

What is not in the document
---------------------------

**The replay line.** The human report prints ``replay: pssc test ... -k NAME
--seed N`` under every failure, and that string is not stored. It is fully
reconstructible from ``name`` and ``extra.seed``, which is why those two fields
are per test — storing the rendered command as well would be a second copy that
can disagree with the first.

.. note::

   ``pssunit-mvp-design.md`` §10.1 says the replay line goes in ``message``. It
   does not, and ``message`` carries the failure detail instead. The design doc
   is the older of the two.

**The src-specs the run was given.** A report says what happened, not what was
asked for. Reconstructing the exact command line needs the invocation, which CI
already has.

**Anything about tests that did not run.** ``--collect-only`` writes no report,
and a filtered-out test is absent rather than present-and-skipped — it is not a
skip, it was not selected.

For a second implementation
---------------------------

The parts a conforming runner has to match, in rough order of how much breaks if
it does not:

1. **The status mapping table above**, including ``rawStatus``. This is the
   whole point of sharing a format; a runner that reports ``xpass`` as ``passed``
   is reporting a different result, not a differently-shaped one.
2. **``summary`` counts the collapsed statuses**, so that it adds up to
   ``tests``.
3. **``name`` short, ``extra.qname`` qualified**, and a seed on every test.
4. ``suite`` is the root component, as a list.

``specVersion`` is ``0.0.0`` and ``generatedBy`` names the tool, so a consumer
can tell two implementations' documents apart when it needs to.

``pssc.testing.report`` is the reference implementation of all of this — the
status collapse in particular is a table with no logic in it, which is the form
worth copying.

See also
--------

:doc:`test-command`
   The CLI: ``--ctrf``, ``--no-ctrf``, and the exit codes above.
:doc:`annotations`
   How ``.skip``, ``.xfail`` and ``.group`` reach the runner in the first place.
