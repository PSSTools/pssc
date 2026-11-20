Writing tests
=============

``pssc test`` injects ``pssunit_pkg`` ahead of your sources, so a suite
imports it and names no framework file. This page is the reference for what
the package declares. How tests are found and run is :doc:`test-command`; what
``@pssunit_pkg::test`` does is :doc:`annotations`.

Types
-----

==================== ===========================================================
type                 use
==================== ===========================================================
``test_a``           base action of a test; a concrete action derived from it is
                     a test
``fixture_c``        base component of a fixture: the root a test runs under
``mock_c``           base component of a PSS reaction hook (see Annotations_)
``access_s``         one bus access: ``addr``, ``width``, ``data``, ``is_write``,
                     ``index`` (the register's array index, or -1)
``access_kind_e``    field behaviour for ``field_access``: ``RO``, ``WO``,
                     ``RW``, ``RC``, ``W1C``, ``W1S``, ``VOLATILE``
``rt_c``             a result ledger written in PSS, with ``check_*`` methods
``status_e``         ``PASS``, ``FAIL``, ``ERROR``, ``SKIP``
==================== ===========================================================

Annotations
-----------

Each is readable through ``pssc.annotations.of``. The runner acts on
``test``, ``field_access``, ``allow`` (see Sanitizers_), and ``setup``,
``teardown`` and ``session`` (see `Set-up and tear-down`_); it reads the others as the
features that use them land. The hook annotations (``on_write``, ``on_read``,
``on_event``) are checked when the test image is built: a register name that
matches no register of the fixture, a field the register lacks, or a hook on
a function that is not a ``target function`` is an error at the annotation.
With the register fake as the platform, an ``on_write`` or ``on_read`` hook
runs after an access to its register (with ``field``, a write of 1 to that
field) as a thread of its own in its mock instance. Such a hook takes no
arguments or one ``pssunit_pkg::access_s``, the access. An ``on_event`` hook
runs when a hook calls ``schedule_after`` with its name; it takes no
arguments or one ``int``.

==================== ==================================== =========================
annotation           attributes                           on
==================== ==================================== =========================
``test``             see :doc:`annotations`               a test action or function
``property``         ``int runs = 100``                   a test action
``expect_error``     ``string text``                      a test
``tag``              ``string name``                      a test; repeatable
``under_test``       ``string comp``                      a fixture
``nocover``          none                                 a function, action or
                                                          component
``cover_cross``      ``string params = ""`` (points,      an operation (a public
                     comma-separated, e.g. ``m,result``)  function); repeatable
``platform``         ``string min``                       a test or fixture
``field_access``     ``string reg = ""``, ``string        a register or group
                     field``, ``access_kind_e kind``
``allow``            ``string what``                      a test
``on_write``         ``string reg``, ``string field``     a ``mock_c`` target
                     (default: any write)                 function
``on_read``          ``string reg``                       a ``mock_c`` target
                                                          function
``on_event``         ``string name``                      a ``mock_c`` target
                                                          function
``setup``            none                               a fixture function
``teardown``         none                                 a fixture function
``session``          none                                 a fixture function
==================== ==================================== =========================

Checks
------

Each is an ``import target function``, implemented by the runner
(``pssc.testing.imports``), so a test runs on generated code too. The last
argument, ``what``, is optional and names the check in a failure. A register
is named by its path from the fixture, as a string (``"dma.ch[0].regs.csr"``):
an import cannot take a component.

====================================== =========================================
call                                   meaning
====================================== =========================================
``check(e)``                           a failure if ``e`` is false; go on
``require(e)``                         a failure if ``e`` is false; stop
``check_eq(a, b)``, ``check_ne``,      compare two ``int[64]`` values
``check_lt``, ``check_le``,
``check_gt``, ``check_ge``
``check_reg(reg, v)``                  the register's value, read through the
                                       back door (no bus access), is ``v``
``check_field(reg, field, v)``         one field of it is ``v``
``check_written(reg, field, v)``       the test's last write set the field to
                                       ``v``
``check_not_accessed(reg)``            the test never read or wrote the register
``fail(msg)``                          a failure; stop
====================================== =========================================

pssc rewrites each of these calls before lowering
(``pssc.testing.check_rewrite``): each operand is evaluated once, into a
temporary, and the call becomes ``check_operand(v)`` for each operand and
then ``check_capture(id, ok)``, whose ``id`` names the call's source text and
operands in the model's check table. So a failure can report each operand's
value, and only scalars cross to the runner. The string arguments (``reg``,
``field``, ``what``, ``msg``) must be literals for the rewrite; tests do not
call ``check_operand`` or ``check_capture`` themselves.

``pssc test`` counts these checks with the ledger's (``1/2 checks``) and
prints a card under the failed test. A failed check is shown as a failure card: where it is, the call with each
operand's value under it, the register it names decoded into fields, the
last bus accesses with their source lines, and the command that repeats
the run::

    FAIL suite.pss:7:9  t_s::t_b  seed=7
      one
      check_eq(n + 1, 2, "one")
               │      │
               1      2
      repro: pssc test -k t_b --seed 7

Platform double
---------------

Also imports. They need a platform double in the run, and fail by name in a
run without one.

======================================== =======================================
call                                     meaning
======================================== =======================================
``base(name)``                           the base address of the named block
``poke_reg(reg, v)``                     set a register through the back door
``poke_field(reg, field, v)``            set one field through the back door
``mem_poke(addr, data, size = 4)``       write memory, without a bus access
``mem_peek(addr, size = 4)``             read memory, without a bus access
``complete_after(reg, field, reads)``    clear the field after ``reads`` reads
``status_sequence(reg, field, v)``       queue ``v`` as the field's next read
``fail_on_access(reg)``                  a failure if the model touches it
======================================== =======================================

Their register and field names are rewritten too, since only scalars cross
to the runner: each call becomes ``double_call(id, v)`` (``double_handle(id)``
for ``base``), whose ``id`` names the strings in the model's check table. So
they must be literals, and tests do not call ``double_call`` or
``double_handle`` themselves. With ``--platform fake`` the register fake
implements them; ``base`` gives each name its own address window.

For a hook (or a test), ``pssunit_pkg`` also has a small library:

=================================================== ==========================================
call                                                meaning
=================================================== ==========================================
``poke_field_at(index, reg, field, v)``             ``poke_field`` of the instance of ``reg``
                                                    (named without indices) at ``[index]``
``raise_irq(irq = 0)``                              raise an interrupt, as the platform would
``state_get(key)``                                  a value the test's mocks keep (0 if unset)
``state_set(key, v)``                               set it
``schedule_after(delay, name, arg)``                run the ``on_event`` hooks named ``name``
                                                    ``delay`` ticks from now, given ``arg``
=================================================== ==========================================

Their names are rewritten the same way; the ``index`` of ``poke_field_at``
and the ``delay`` of ``schedule_after`` go first, as a ``check_operand``.
``schedule_after`` with a name no hook has is an error. A call written ``pssunit_pkg::f(...)``
is rewritten as ``f(...)`` after ``import pssunit_pkg::*`` is.

The fake, Python platforms and doubles, and how a run binds them are
:doc:`platform-doubles`.

Expected access sequences
-------------------------

Opt-in: most tests should check results (``check_written``, ``check_reg``),
not the order of accesses. Where the order is the point -- start a channel
before polling it -- queue the accesses the test expects and check them:

========================================== =====================================
call                                       meaning
========================================== =====================================
``expect_write(reg, field, v)``            expect a write to ``reg`` that sets
                                           ``field`` to ``v`` (``""``: the
                                           whole register value is ``v``)
``expect_read(reg)``                       expect a read of ``reg``
``check_seq(what)``                        the expected accesses happened in
                                           order; others may come between
``check_seq_strict(what)``                 the accesses were exactly the
                                           expected ones, in order
========================================== =====================================

.. code-block:: pss

   component t_seq : fixture_c {
       t_pkg::dev_c dev;
       @pssunit_pkg::setup
       function void bind_dev() { dev.initialize(base("dev")); }

       @pssunit_pkg::test
       function void t_in_order() {
           expect_write("dev.regs.ctl", "irq", 1);
           expect_read("dev.regs.st");
           dev.wr_ctl(4);
           dev.wr_sts(0);
           dev.rd_st();
           check_seq("start, then poll");
       }

A ``check_seq`` matches against the accesses since the last ``check_seq``
(or the start of the test), whether they came before or after the
``expect_`` calls, and clears the queue. Its failure card says which
expected access did not happen, or which access was not expected, and
lists the accesses it saw. Expectations that no ``check_seq`` checks fail
the test when it ends. They need the fake (``--platform fake``), on the
Python and the C engine.

Sanitizers
----------

A run through ``pssc.testing.vm`` (a debug image; ``pssc test`` runs every
test that way, on a plain sparse-memory platform) watches each bus access
and fails the test, located at the access, when one of these finds
something (``pssc.testing.sanitizers``):

==================== ==============================================================
name                 fails when
==================== ==============================================================
``poll-budget``      one address read more than ``poll_budget`` times (1000)
                     returning the same value with no write between; it stops
                     the test, so a spin on a fake that never completes is a
                     failure at the polling read, not a hang
``unmapped``         an access no register covers: inside a register block that
                     the run has placed, or, when the platform declares
                     ``regions = [(base, size)]``, outside all of them
``access-rule``      a write to a read-only register, or a read of a write-only
                     one (a register with fields: all its fields RO, or all WO)
``inflight``         at the end of the test, a latch still holds a token: an
                     operation started and never completed. A latch is a channel
                     the model only uses with ``try_put`` and ``try_get`` (a wake
                     channel, which someone ``get``\ s, is not one); the finding
                     is at the ``try_put`` whose token was never taken
``rc-read``          a register with a read-to-clear (``RC``) field is read as a
                     statement, its value unused (``regs.st.read_val();``): the
                     read cleared the field and what it held is lost
``unbound``          a register is accessed before ``set_handle`` placed its
                     group (its address is its bare offset; once per register
                     and test). A group really placed at address 0 needs
                     ``allow``
``unconsumed-irq``   a double raises an interrupt (``ctx.raise_irq``) that
                     nothing takes: the run has no interrupt handler
                     (``irq_handler``) and the platform no ``raise_irq``; at
                     the access or import whose double raised it, once per
                     interrupt number and test
==================== ==============================================================

``@pssunit_pkg::allow {.what = "access-rule"}`` on a test function switches
one off for that test; a name that is no sanitizer is an error. Each finding
is also a ``sanitizer`` trace event. ``inflight`` is checked only when the
test ran to its end (not after a failed ``require`` or a ``poll-budget``
stop), and tells channels apart by name, so the ``inflight`` of every
instance is one count.


Test IDs
--------

A test's ID is ``<fixture>::<test>``: the component it runs under, then the
action or function name. The file is not part of it, so moving a file keeps
every ID. Reports, failure cards and replay lines print it, and a src-spec
containing ``::`` selects by it as a glob (``pssc test "a_f::t_*"``). Two
fixtures may each declare a ``t_ok``; their IDs differ:

.. code-block:: pss

   component a_f {
       rt_c rt;
       @pssunit_pkg::test {.tags = "slow"}
       function void t_ok() {
           rt.check_eq_int(1, 1, "one");
       }
       @pssunit_pkg::test
       function void t_bad() {
           rt.check_eq_int(1, 2, "two");
       }
   }
   component b_f {
       rt_c rt;
       @pssunit_pkg::test
       @pssunit_pkg::tag {.name = "fast"}
       function void t_ok() {
           rt.check_eq_int(3, 3, "three");
       }
   }

Selection by ID, tag, file and line is in :doc:`test-command` ("Selecting
tests", "Selecting by line").

Inheritance
-----------

A fixture derived from another inherits its tests and runs each under its
own root, with its own ID. An abstract test action is not a test, but a
concrete action derived from it is:

.. code-block:: pss

   component base_f : fixture_c {
       @pssunit_pkg::tag {.name = "smoke"}
       @pssunit_pkg::property {.runs = 7}
       @pssunit_pkg::platform {.min = "sim"}
       action t1 : test_a {
           exec body {
               int x = 1;
           }
       }
       abstract action t_abs : test_a { }

and a fixture derived from it

.. code-block:: pss

   component derived_f : base_f {
       action t2 : t_abs { }
   }

run ``base_f::t1``, ``derived_f::t1`` and ``derived_f::t2`` (not
``base_f::t_abs``).

A test action derived from another one replaces its ``exec body``; the body
runs the base's with ``super;``, where it stands. An action that declares no
body runs its base's, so here ``l4_t`` runs all three in order and each of
the four is a test:

.. code-block:: pss

   component sup_f : fixture_c {
       rt_c rt;
       action l1_t : test_a {
           int x;
           exec body { x = 1; }
       }
       action l2_t : l1_t {
           exec body {
               super;
               comp.rt.check_eq_int(x, 1, "l1 ran first");
               x = x * 10 + 2;
           }
       }
       action l3_t : l2_t {
           exec body {
               super;
               comp.rt.check_eq_int(x, 12, "l1 then l2");
               x = x * 10 + 3;
               comp.rt.check_eq_int(x, 123, "then l3");
           }
       }
       action l4_t : l3_t { }
   }

The op-model gate
-----------------

The code under test is meant to become generated code, so before any test
runs ``pssc test`` checks it with the gate of each operation-model target
(``op-model-c`` unless ``--targets`` or ``[tool.pssunit] targets`` says
otherwise). A DUT the C target cannot render yet fails the run with exit 2
and the refusal's location, even when every test would pass on the VM:

.. code-block:: pss

   component dut_c {
       target function int sum(array<int,4> a) {
           return a[0];
       }
   }

Only the component instances the fixtures hold are gated. A test file, and
``pssunit_pkg``, are not: a test may use what no target renders. To test
such a DUT anyway, run with ``--targets none``. The option and the message
are in :doc:`test-command` (``--targets``).

Generated code
--------------

The gate says a target can render the code; ``--engine gen-py`` and
``--engine gen-sv`` check that what it renders does the same thing. Each
test runs twice:

1. on the Python VM, as with ``--engine py``: the reference run. Its
   register bases and its bus accesses are kept;
2. as the generated code, on a fresh fake with the same doubles:
   ``gen-py`` imports the ``op-model-py`` module of the test's action;
   ``gen-sv`` builds the ``op-model-sv`` code with a small testbench under
   Verilator and replays its accesses and imports into the fake.

A bus access that is not the reference's -- another register, another
value written, or one access more or fewer -- fails the test, and the
failure names the first such access:

.. code-block:: text

   diverges from pyvm at bus access 3: expected write32 0x1004 = 0x1 (dma.regs.csr), got write32 0x1008 = 0x1 (dma.regs.size)

When the generated code stops early, the "got" side reads ``nothing (the
run ended)``.

Otherwise the generated run's own checks decide, with the same cards as
on the VM. Coverage and ``--trace`` are of the reference run.

Only an action test is generated: one ``--export-action`` can export
(its activity a plain call chain into the code under test, no ``rand``
attributes, every action in the ``--root`` component). Any other test --
a test function, an activity, a random attribute -- is a SKIP that names
the engine and the reason (``gen-py: a test function is not exported to
generated code``). The engines run on ``--platform fake`` only.

``gen-sv`` needs ``verilator`` on ``PATH``. It builds once per root
component, in ``.pssunit/gen-sv/<root>``; ``gen-py`` writes its modules
to ``.pssunit/gen-py/<root>``. The SV code cannot call back into Python
during the run, so it is served what the reference read, and the outputs
of its imports come from a ``gen-py`` run of the same test; a test the
Python generator cannot render is an ERROR on ``gen-sv`` too.

Set-up and tear-down
--------------------

Every test runs on a freshly built component tree, so nothing one test
writes to a component field is seen by the next. On that tree, the
fixture's ``@pssunit_pkg::setup`` functions run before the test, base
component first, and its ``@pssunit_pkg::teardown`` functions after it,
derived component first (``pssc.testing.catalog.hooks``). Tear-down runs
whatever the test did; when the test already failed with an error, an error
in a tear-down does not replace it.

.. code-block:: pss

   component dma_f : fixture_c {
       rt_c rt;
       int ready;
       @pssunit_pkg::setup
       function void arm() { ready = 1; }
       @pssunit_pkg::teardown
       function void check_idle() { rt.check_eq_int(ready, 1, "still armed"); }
   }

A ``@pssunit_pkg::session`` function boots the DUT. On a fake every test
still has a fresh tree, so it runs before the test's set-up, every time. In
a simulation it runs once, before the first test, and the component's tests
then share the DUT with no platform ``reset()`` between them
(:doc:`simulation`).

Property tests
--------------

An action test annotated ``@pssunit_pkg::property {.runs=N}`` (default
100) runs ``N`` times, its ``rand`` attributes solved again on each run
with a seed of its own. The first run uses the run seed itself; the others
are drawn from it and the test's name (``pssc.testing.runner.run_seeds``),
so the same command always runs the same seeds.

.. code-block:: pss

   @pssunit_pkg::property {.runs=200}
   action len_a : test_a {
       rand bit[8] len;
       exec body { comp.rt.check_true(len < 128, "length fits"); }
   }

The first run that does not pass ends the test. On the Python engine the
failing run is then shrunk: the test is run again with the same seed, each
of its ``rand`` attributes in turn held to smaller values (a binary search
down from the failing value), and the smallest values that still fail the
same way are kept. Its report names the run, its seed and what shrank
(``run 2 of 200 failed (seed 1454033498, shrunk: len=128 (from 202))``),
and the replay line gives that seed, so ``--seed 1454033498`` fails on the
first run (with the original values: the shrunk ones are a hint for the
reader, not a seed). A passing test's line shows how many runs it made.

Only the test action's own attributes shrink, at most 64 re-runs in all; a
failure that is an unsatisfiable solve is not shrunk. On the C engine, in
generated code and under ``--platform replay:`` the run is not shrunk and
the seed is the repro. A run re-made for ``--trace fail`` is the unshrunk
one.

A failing seed is also appended to
``.pssunit/regressions/<test name, :: as .>.seeds`` under the working
directory, one seed per line (``#`` starts a comment); every seed in that
file runs before the fresh ones, on every later run, until the file is
edited. Commit the file to keep a found bug found.

Expected errors
---------------

A test annotated ``@pssunit_pkg::expect_error {.text="..."}`` passes only
if it stops with an error -- ``error()``, ``fatal()``, or anything the run
raises -- whose message contains ``text``. Its report keeps the error's
location (``expected error: suite.pss:7:31: error: counter overflow at 3``).

.. code-block:: pss

   @pssunit_pkg::test
   @pssunit_pkg::expect_error {.text="overflow"}
   function void t_overflow() { error("counter overflow at %d", 3); }

It fails if the test runs to completion, fails a check, or stops with an
error whose message does not contain ``text``; the report says what was
expected and what happened. A model that does not build or cannot be
solved is reported as that, never as the expected error.


Examples in doc comments
------------------------

A block fenced by three backticks with the info string ``pss-example``, in a
component function's doc comment (``/** ... */`` or ``///``), is a test. ``pssc test`` runs each block as the
body of a test in a fixture that holds the documented component as ``dut``;
the block does its own set-up, as a hand-written test would, and may use the
checks above. Fences with any other info string (``pss``, or none) are not run.

.. code-block:: pss

   package m_pkg {
       component calc_c {
           /**
            * Adds two numbers.
            * ```pss-example
            * check_eq(dut.add(1, 2), 3, "1 + 2");
            * ```
            * Not an example:
            * ```pss
            * dut.add(0, 0);
            * ```
            */
           function int add(int a, int b) { return a + b; }

           /// Subtracts, wrongly.
           /// ```pss-example
           /// check_eq(dut.sub(3, 1), 2, "3 - 1");
           /// ```
           function int sub(int a, int b) { return a + b; }
       }
   }

Here ``add``'s example passes and ``sub``'s fails. The test is
``doc_<component>_<function>_<n>::t_example`` (``doc_m_pkg_calc_c_sub_1::t_example``;
``::`` in the component's name becomes ``_``); its location, in the failure
card, ``--collect-only`` and the CTRF report, is the example's line in the doc
comment, so ``pssc test dev.pss:17`` runs just that example:

.. code-block:: text

   FAIL dev.pss:17:13  doc_m_pkg_calc_c_sub_1::t_example  seed=...
     3 - 1
     check_eq(dut.sub(3, 1), 2, "3 - 1")

The wrappers are written to ``.pssunit/doctest.pss`` and translated with the
model; nothing is written when the model has no examples. ``--no-doctest``
leaves the examples out of the run.
