Code coverage
=============

``pssc test --cov PATH`` measures which of the model's statements, branch
arms and registers the tests reached, and writes the counts to ``PATH`` as
JSON. It needs the debug image (the default; ``--cov`` with ``--no-debug``
is refused), and counts the same with ``-j N`` as without: each test is
counted apart and the counts are summed.

.. code-block:: console

   $ pssc test tests/ --cov cov.json
   $ pssc test tests/ --cov cov.json --cov-scope dut_c

The options are in :doc:`test-command`; how to write the tests, in
:doc:`writing-tests`.

A ``PATH`` ending ``.cdb`` is written as a UCIS coverage database for
covsight instead (`Coverage databases`_).

What is measured
----------------

Statements
   Each statement of a function, of an action's ``exec`` block and of a
   component's ``exec init_*`` block, with the number of times it ran.

Branch arms
   Each arm of an ``if`` (``then``, ``else``) and of a ``match``, with the
   number of times it was taken. An ``if`` with no ``else`` still has an
   ``else`` arm (``"implicit": true``): a test suite that never skips the
   ``if`` leaves it unreached.

Registers and fields
   With the register fake (``--platform fake``, :doc:`platform-doubles`),
   each register instance: reads, writes and, per field, value bins --
   each bit seen 0 and 1 (``bit<i>=0``, ``bit<i>=1``), then each enum item
   or ``zero`` and ``max``, and ``cleared`` for an RC or W1C field whose
   side effect was seen. Every register of the model is reported, accessed
   or not.

A point the run never reached is reported with 0 hits: the points are read
from the model, not from the run.

Stable IDs
----------

Each statement and arm has an ID, ``<scope>/<path>``. The scope is the
function (``<component type>::<function>``), the action (its exec blocks:
``a_t/body/0``) or the component type (its init blocks:
``pss_top/init_down/0``). The path is the statement's place in its block
structure: statement ``2`` of the body, its first nested statement ``2/0``,
the ``then`` arm of that ``2/0/then``. Moving code to other lines keeps
every ID; the line is reported for information only.

For this component:

.. code-block:: pss

   component cov_c {
       function int sum(int n) {
           int acc;
           acc = 0;
           repeat (i : n) {
               if (i % 2 == 0) {
                   acc += i;
               }
           }
           return acc;
       }
       function int never(int n) {
           return n;
       }
   }

a call ``sum(5)`` counts, by ID:

.. code-block:: python

   assert {k: v for k, v in st.items() if k.startswith("cov_c::sum/")} == {
       "cov_c::sum/0": 1, "cov_c::sum/1": 1, "cov_c::sum/2": 1,
       "cov_c::sum/2/0": 5, "cov_c::sum/2/0/then/0": 3, "cov_c::sum/3": 1}
   assert _hits(cov.arms) == {"cov_c::sum/2/0/then": 3, "cov_c::sum/2/0/else": 2}

and ``cov_c::never/0`` is reported with 0 hits.

The boundary
------------

Only the code under test counts. By default that is everything but the
standard libraries, ``pssunit_pkg``, the tests' root components (fixtures)
and their bases, ``mock_c`` components, test actions, and what is marked
``@pssunit_pkg::nocover`` (a component, an action or a function):

.. code-block:: pss

   @pssunit_pkg::nocover
   function int debug_dump(int n) { return n; }

To narrow it to one component, name its type with ``--cov-scope`` (it may
be given more than once), or annotate the fixture with
``@pssunit_pkg::under_test {.comp = "other_c"}``. The component counts with
its bases, its derived types, its sub-components (recursively) and their
actions:

.. code-block:: python

   assert main(["test", ".", "--no-ctrf", "--cov", "b.json",
                "--cov-scope", "other_c"]) == 0
   assert _scopes(json.loads((tmp_path / "b.json").read_text())) == {"other_c::dec"}

``--cov-scope`` needs ``--cov``. The boundary does not apply to registers:
they are the device's.

The report
----------

The file is one JSON object, ``pssunit.coverage/1`` in ``pssc help
--json``:

``version``
   ``1``.
``statements``, ``arms``
   ID -> ``scope``, ``path``, ``hits``, ``file``, ``line``; an arm also has
   ``branch`` (its branch's ID), ``arm`` (``then``, ``else``, ...) and
   ``implicit``.
``registers``
   register instance path (``dma.ch[0].regs.adr0``) -> ``comp`` (the
   instance path of the component that holds it), ``type`` (the register
   type), ``comp_type`` (that component's type), ``reads``, ``writes``,
   ``fields``: name -> ``kind``, ``width``, ``bins`` (bin ->
   count).
``envelope``
   operation (``dut_c::f``) -> ``calls``, ``params``: name -> ``type``,
   ``bins`` (bin -> count), ``result`` (the same, or ``null`` for a
   ``void`` operation) and ``crosses`` (see `Operation envelope`_).
``summary``
   ``statements``, ``arms``, ``registers``, ``bins`` and ``envelope``, each
   ``{"total": N, "hit": M}``.

A register's counts, after a write and two reads of its status register:

.. code-block:: python

   st = regs["regs.st"]
   assert (st["reads"], st["writes"]) == (2, 1)
   done, err, irq = (st["fields"][n]["bins"] for n in ("done", "err", "irq"))
   assert done == {"bit0=0": 1, "bit0=1": 2, "zero": 1, "max": 2, "cleared": 1}

Operation envelope
------------------

Each operation under test (a function inside the boundary) gets an
envelope: how often the tests called it, and which values its arguments
and its result took. The bins come from each parameter's type:

* an enum: one bin per item;
* ``bool``: ``false``, ``true``;
* an integer of up to 3 bits: one bin per value;
* a wider integer: ``zero``, ``other``, ``max``, and, if signed, ``min``
  and ``neg``.

A string, a struct or an array parameter has no bins. For

.. code-block:: pss

   component dut_c {
       function bit[8] f(mode_e m, bit[8] v, bool b, int s) {
           return g(v);
       }
       function bit[8] g(bit[8] v) { return v; }

a test's ``dut.f(mode_e::B, 255, true, -3)`` hits ``m=B``, ``v=max``,
``b=true``, ``s=neg`` and ``result=max``. Only the outermost call of an
operation counts: ``f``'s own call of ``g`` is not a call of ``g`` by the
test:

.. code-block:: python

   assert f["calls"] == 1
   assert {p: _bins(v) for p, v in f["params"].items()} == {
       "m": {"B"}, "v": {"max"}, "b": {"true"}, "s": {"neg"}}
   assert _bins(f["result"]) == {"max"}
   # f's own call of g is not counted: only the test's two
   assert g["calls"] == 2

To cross parameters (or a parameter and ``result``), annotate the
operation with ``@pssunit_pkg::cover_cross {.params = "m,result"}``, once
per cross. A cross has a bin per combination of its points' bins
(``"B,max"``) and is reported under ``crosses`` by its names (``"m,result"``);
a name the operation does not have drops the cross.

The envelope is the same on the Python and the C engine. In a ``.cdb``,
each operation is a covergroup named after it under its component's type,
with attributes ``pssunit.kind`` (``envelope``), ``pssunit.op`` and
``pssunit.calls``, a coverpoint per parameter and ``result`` (attribute
``pssunit.type``) and a cross per ``cover_cross``.

Coverage databases
------------------

``--cov x.cdb`` writes the same counts as a UCIS database (covsight's NCDB
format), which ``covsight`` reads, merges and reports on:

.. code-block:: console

   $ pssc test tests/ --cov all.cdb
   $ pssc test tests/ -k prio --cov prio.cdb
   $ covsight merge all.cdb prio.cdb -o merged.cdb
   $ covsight show summary merged.cdb
   $ covsight show tests merged.cdb

The database holds:

* a design unit and an instance per component type (``language=PSS``);
  the counts are per type, as in the JSON report;
* under it, a block per scope (function, action, init) with a statement bin
  per statement, named by its path (``2/then/0``), and a branch per branch
  statement with a bin per arm; an arm the source does not write is
  flagged as an implicit ``else``;
* a covergroup per register type, under the type of the component that
  holds it, with a cover instance per register; coverpoints ``access``
  (``read``, ``write``) and one per field, with the field's bins;
* a test history node per test: its ID, status (``pass``, ``skip`` and ``xfail``
  are UCIS ``OK``; ``fail`` and ``xpass`` ``ERROR``; ``error``
  ``FATAL``), seed, and attributes ``pssunit.status``,
  ``pssunit.engine``, ``pssunit.platform``; and which bins each test hit,
  and how often, so ``covsight`` can say which tests reached a point.

With ``--cov``, every test function of a component is built into its image
whether it was selected or not, so a ``-k`` run has the same points as the
whole suite, and ``covsight merge`` sums their counts and keeps both runs'
tests (and adds a merge history node). Databases from different models
still merge, but more slowly.
``pssc cov merge`` does the same (`Reading a report`_).

Reading one from Python:

.. code-block:: python

   db = NcdbReader().read(str(project / "cov.cdb"))
   hs = list(db.historyNodes(HistoryNodeKind.TEST))

Reading a report
----------------

``pssc cov`` reads either report, the JSON or the ``.cdb``; the full
options are in :doc:`cli-reference`. Source paths in a report are as the
filelist spelled them, so they resolve relative to ``--source-root``, then
the current directory, then the report's directory.

``pssc cov gaps``
   Every statement, branch arm, register and field bin no test reached,
   with ``--context N`` source lines around it (``--kind`` selects):

   .. code-block:: console

      $ pssc cov gaps cov.json --kind arm --source-root tests
      ../pss/wb_dma_ch_c/functions/configure_channel.pss:51: implicit else arm never run (wb_dma_ch_c::configure_channel/17/else)
            49 |         // Capability-gated. Hardware silently ignores these on a channel built
            50 |         // without the capability, so decline rather than pretend they took.
        >   51 |         if (caps.ars) {

``pssc cov report``
   Hit and total statements, arms, registers and bins, overall and per
   scope:

   .. code-block:: console

      $ pssc cov report cov.cdb
      scope                                     statements             arms
      wb_dma_ch_c::check_completion             7/9     77%      3/4     75%

``pssc cov lcov`` and ``pssc cov cobertura``
   Statement and branch counts as an LCOV tracefile or a Cobertura XML
   file, for an editor's gutter or a CI coverage page:

   .. code-block:: console

      $ pssc cov lcov cov.json -o cov.info --source-root tests
      $ pssc cov cobertura cov.json -o coverage.xml --source-root tests

``pssc cov merge``
   Sums the counts of several reports. When every input and the output
   are ``.cdb``, the merge is covsight's and keeps each run's tests;
   otherwise the output is a JSON report, or a ``.cdb`` without tests:

   .. code-block:: console

      $ pssc cov merge all.cdb prio.cdb -o merged.cdb
      $ pssc cov merge a.json b.cdb -o merged.json

``pssc cov diff``
   The gaps on lines changed since a git revision, to check that new code
   is tested:

   .. code-block:: console

      $ pssc cov diff --base main cov.json --source-root tests

Each takes ``--json`` where it prints (``gaps``, ``report``, ``diff``); the
schemas are ``pssc.cov-gaps/1``, ``pssc.cov-report/1`` and
``pssc.cov-diff/1``, listed by ``pssc help --json``.

From Python
-----------

:class:`pssc.testing.coverage.Coverage` is the collector ``pssc test``
uses. Give it to a session as its events sink:

.. code-block:: python

   def _call(model, fn, *args):
       cov = Coverage(model)
       s = bc_session.Session(model, events=cov, watch=Coverage.WATCH)
       ret = s.call(s.construct(), fn, *args)
       return ret, cov

``cov.report()`` is the report above; ``Coverage.merge(report)`` adds
another's counts. With a ``RegisterFake`` platform the session hands the
fake the collector, and register accesses are counted too.
