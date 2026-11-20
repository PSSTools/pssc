Tutorial: your first operation test
===================================

This page takes the WB DMA operation model from no tests to a passing test, a
failing card and a fix. Every PSS file it shows is quoted from
``examples/op_model/tests/``, which the unit suite runs
(``tests/unit/test_wb_dma_suite.py``), so what you read here is what runs.

You need a ``pssc`` that runs (``python -m pssc --help``). Nothing else: the
tests run on the bytecode VM against a register fake, with no simulator and no
C compiler.

1. The model
------------

A test suite is the device model's sources plus your test files. The model is
the companion example under ``examples/op_model/pss/``; ``model.f`` lists its
files in dependency order so a command line can name them with ``@model.f``.
A directory alone (``pssc test .``) reads only the ``*.pss`` files under it,
not the model in ``../pss``, so name the model with ``@model.f`` as well.
The first file it lists is the suite's own execution target:

.. literalinclude:: ../examples/op_model/tests/target_cfg.pss
   :language: none

The register fake raises no interrupts, so the target says it cannot wait on
an event and the model's ``wait_hint()`` polls the status register instead.

2. A fixture
------------

A fixture is the component every test runs under. It derives from
``fixture_c`` (declared by ``pssunit_pkg``, which ``pssc test`` adds for you)
and owns the device. A function marked ``@pssunit_pkg::setup`` runs before each
test; this one binds the device at the base address the platform reports for
``"dma"``:

.. literalinclude:: ../examples/op_model/tests/wb_dma_fx_c.pss
   :language: none

3. A test
---------

A test is a concrete action derived from ``test_a``, in the fixture's scope.
This one does what the companion example's ``sw_copy_a`` does: route the
interrupts, then one software-mode copy on channel 0, and check the result:

.. literalinclude:: ../examples/op_model/tests/sw_copy_a.pss
   :language: none

``check`` and ``check_field`` are ``pssunit_pkg`` checks: a failing one is
recorded with its source line and the run goes on. ``status_sequence`` scripts
what the fake returns for a field on successive reads, since a fake has
registers but no DMA engine behind them.

Run it from ``examples/op_model/tests/``:

.. code-block:: text

   pssc test @model.f . --platform fake --targets none

``.`` finds every test in the directory; ``--platform fake`` runs against the
register fake built from the model's own register map; ``--targets none``
skips the gate that first checks the model lowers to C (see
:doc:`test-command`). With only the three files above, the run prints:

.. code-block:: text

   wb_dma_fx_c
     sw_copy_a ................................... PASS    0.00s  2/2 checks

   1 passed    seed 898669310    0.00s

4. A failing card
-----------------

Now make the mistake everyone makes once: leave out the two
``status_sequence`` lines (and their comment), so nothing ever sets
``csr.done``. The model's ``transfer_single`` polls the channel's status
register for completion, which on a real device ends when the copy does. On
the fake it would never end; instead the poll-budget sanitizer stops it, and
the run prints a card for the failure:

.. code-block:: text

   wb_dma_fx_c
     sw_copy_a ................................... FAIL    0.08s  0/1 checks

   FAIL wb_dma_fx_c::sw_copy_a
     1 of 1 check(s) failed
     stopped: sanitizer poll-budget at ../pss/wb_dma_ch_c/functions/probe_status.pss:39:9: register dma.ch[0].regs.csr (0x10000020) read 1001 times returning 0x19 with no progress (budget 1000)
     ...
     replay: pssc test @model.f . wb_dma_fx_c::sw_copy_a --platform fake --targets none --seed 1108270667

   0 passed, 1 failed    seed 1108270667    0.08s

Read it from the top: which test, how many of its checks failed, and why the
run stopped -- with the model's source line that was polling, the register by
name and address, and the value it kept reading. The ``replay:`` line runs the
one test again with the same seed. A failing check (rather than a stop) prints
the check's call with its arguments, the register decoded field by field and
the last few bus accesses, each with the model line that made it.

5. The fix
----------

The card says the model polled ``csr`` waiting for a change the fake never
makes, so the fix is in the test: put the two ``status_sequence`` lines back.
The first scripted read (``done`` = 0) is the arming read-modify-write; the
second (``done`` = 1) is the poll that sees the copy finish. Run again and it
passes. A run that passes writes nothing; a run whose failures change records
them in ``.pssunit/lastfailed``, which ``--lf`` reruns (see :doc:`test-command`).

6. The rest of the suite
------------------------

``examples/op_model/tests/`` goes on from here, one file per idea; each is
described in :doc:`writing-tests`.

- A derived fixture, ``wb_dma_routed_fx_c.pss``: its setup runs after the
  base's, and every test of the base runs under it too.

  .. literalinclude:: ../examples/op_model/tests/wb_dma_routed_fx_c.pss
     :language: none
     :start-at: component wb_dma_routed_fx_c

- More of the companion example's operations as test actions: ``arb_a.pss``,
  ``abort_a.pss``, ``hw_handshake_a.pss``.
- ``prio_a.pss``: a property test (``@pssunit_pkg::property``, solved afresh
  on each of 8 runs), a test function (``@pssunit_pkg::test`` on a
  function) and an expected error:

  .. literalinclude:: ../examples/op_model/tests/prio_a.pss
     :language: none
     :start-at: // A property: any priority
     :end-before: // A test function

- ``regmap_a.pss`` and ``completion_a.pss``: checks of the register map itself
  and of completion handling. The fake takes its map from the model, so a test
  that only drives the model cannot see a wrong offset or field position; these
  state the expected values outright.

The whole suite, 21 tests over both fixtures, runs in well under a second:

.. code-block:: text

   pssc test @model.f . --platform fake --targets none
