Platform doubles
================

A PSS test runs its device code in pssc's VM, in this Python process. Every
register access and every user import the code makes goes to a *platform*: an
object with ``read8``..``read64``, ``write8``..``write64``, ``message(text)``
and one method per user import. This page describes the platforms pssc ships
(plain memory, and the register fake), the Python API for writing your own
(``pssc.testing.vm``), how a run picks one, and how to run all of this under
pytest. How to write the tests themselves is :doc:`writing-tests`; the
``pssc test`` options are in :doc:`test-command`.

Every code block on this page is copied from a unit test that runs it.

The default: plain memory
-------------------------

With no ``--platform``, ``pssc test`` runs each test on a new
``pv.Platform``: sparse little-endian memory, every byte 0 until written. A
user import that nothing implements returns 0. That is enough for code that
writes a register and reads it back. It is not enough for code that waits on a
status bit, or for a register whose fields behave differently from plain
memory.

The register fake: ``--platform fake``
--------------------------------------

``pssc test --platform fake`` runs each test on a new ``pv.RegisterFake``. It
uses the register map of the debug image (so not with ``--no-debug``):

- Each register instance has its own storage, starting at its reset value.
  Two instances bound to one address (a group bound to another group's
  registers) are one register.
- ``READONLY`` registers ignore writes; ``WRITEONLY`` registers read as 0.
- Each field has a *kind*: by default the register's access, then the kind a
  register description gives it (``--reg-semantics FILE``, below), then the
  kind set by a ``@pssunit_pkg::field_access`` annotation. ``RO`` keeps its bits on a
  write, ``WO`` reads as 0, ``RC`` is cleared by a read, and ``W1C`` / ``W1S``
  clear / set the bits written as 1 and leave the rest. ``VOLATILE`` is
  stored as ``RW``: only the device changes it, so a test sets it through the
  back door (``poke_reg``) or a hook.
- Every other address is the platform's sparse memory.

The annotation can go on a register type, on a group type (naming one of its
registers with ``.reg``), on a register instance, or on a group instance:

.. code-block:: pss

   @pssunit_pkg::field_access {.field = "done", .kind = pssunit_pkg::access_kind_e::RC}
   pure component st_c : reg_c<st_s, READWRITE, 32> {}
   pure component ro_c : reg_c<st_s, READONLY, 32> {}

   @pssunit_pkg::field_access {.reg = "ctl", .field = "err", .kind = pssunit_pkg::access_kind_e::W1S}
   pure component grp_c : reg_group_c {
       @pssunit_pkg::field_access {.field = "err", .kind = pssunit_pkg::access_kind_e::W1C}
       st_c st;
       st_c ctl;
       ro_c sts;

The package is ``pssunit_pkg``. An annotation written as ``@pssunit::...``
is dropped without a message.

Rather than annotate each field, a test run can take the kinds from the
register description the PSS registers were made from:
``pssc test --reg-semantics FILE`` reads an IP-XACT ``.xml``, a SystemRDL
``.rdl`` (``rclr`` is ``RC``, ``woclr`` / ``woset`` are ``W1C`` / ``W1S``;
needs ``pip install pssc[rdl]``) or a JSON ``{"REG": {"FIELD": "KIND"}}``
table, and matches each register by the end of its instance path
(``ch.csr`` names ``regs.ch[1].csr``). An annotation still wins over the file.
The rules, and the same option for the debugger and the pytest plugin, are in
:doc:`test-command`::

   pssc test tests/ --reg-semantics src/rdl/wb_dma_ch_regs.rdl

A failure card from a run on the fake shows the decoded register. The
register calls a PSS test can make on the fake (``poke_reg``,
``status_sequence``, ``complete_after``, ...) are listed in
:doc:`writing-tests` ("Platform double").

From Python, ``RegisterFake`` is a platform like any other. Its back door
reads and sets registers and fields by instance path without a bus access:
``peek_reg``, ``poke_reg``, ``peek_field``, ``poke_field``, ``check_reg``
(fails with every field that differs), ``mem_peek`` and ``mem_poke``.
``bus`` is the decoded bus log: one entry per access, with the register,
its fields and the PSS source line that made it (``None`` for plain memory):

.. code-block:: python

   fake = pv.RegisterFake()
   s = pv.load([src], "t_pkg::dev_c", platform=fake)
   root = s.construct()
   s.call(root, "initialize", BASE)
   s.call(root, "wr_ctl", 0b110)
   fake.write32(0x2000, 7)                 # memory, no note: no source
   e, m = fake.bus[-2:]
   assert (e["op"], e["addr"], e["data"], e["reg"]) == ("write", BASE + 4, 0b110, "regs.ctl")
   assert e["fields"] == {"done": 0, "err": 1, "irq": 1, "rest": 0}

``e["src"]`` is ``file:line:col`` of the ``regs.ctl.write_val`` call.

Hooks
~~~~~

To make the fake act like a device, subclass it and mark methods with
``@pv.on_write(reg, field=None)`` (after each write; with ``field``, only a
write that sets that field) or ``@pv.on_read(reg)`` (before each read). A
register is named by its group type and register, package-qualified or not,
or by its instance path. The hook gets ``ctx`` (below) and an ``Access``:
``op``, ``width``, ``addr``, ``data``, ``reg``, ``group``, ``index``,
``fields``, ``src``, and for a write ``old`` and ``new``:

.. code-block:: python

   class HookedFake(pv.RegisterFake):
       def __init__(self):
           super().__init__()
           self.seen = []

       @pv.on_write("grp_c.ctl", field="irq")      # group type and register
       def armed(self, ctx, acc):
           self.seen.append(("armed", acc.reg, acc.group, acc.index, acc.old,
                             acc.new, acc.fields["irq"], acc.src is not None,
                             ctx is not None))
           self.poke_field("regs.st", "done", 1)   # the device finishes

A hook that names no register (``ValueError``, naming the class and method)
or no field (``KeyError``) fails ``pv.load``. The back door runs no hooks.

The Python API: ``pssc.testing.vm``
-----------------------------------

``import pssc.testing.vm as pv`` runs PSS in the calling Python process.

``pv.load(files, root, platform=None, seed=0, use=(), ...)``
   Translates ``files`` together with ``pssunit_pkg`` and the preludes,
   lowers component ``root`` (a debug image by default), and returns a
   ``Session`` that runs on ``platform``. A translation error raises
   ``ValueError`` with the messages.

``Session``
   ``construct()`` builds the component tree and returns its root;
   ``call(obj, "fn", *args)`` calls a function; ``instance("ch[0]")`` finds
   an instance by path; ``run_test("suite::t")`` runs one test and returns
   ``(n_pass, n_fail, n_skip)``; ``use(doubles)`` applies doubles (below);
   ``failure_cards()`` and ``findings()`` give what a failed test and the
   sanitizers reported.

``pv.Platform``
   A platform to start from: sparse memory, ``log`` (every access as
   ``("read"|"write", width, addr, data)``) and ``messages``. Add the
   model's user imports as methods with the names they have in PSS. A
   platform does not have to inherit ``Platform``; any object with the
   methods the run reaches works.

The methods are those of the ``<Root>ImportApi`` Protocol that op-model-py
generates, so one Python model can drive both a VM run and generated
op-model-py code. ``pssc_rt.check_import_api(platform, <Root>ImportApi)``
lists the methods a platform lacks.

For this model:

.. code-block:: pss

   import addr_reg_pkg::*;

   import function bit[32] plat_id();
   import function void plat_delay_us(bit[32] us);

   component board_c {
       function bit[32] probe() {
           plat_delay_us(3);
           write32(make_handle_from_handle(0, 0x100), 7);
           return read32(make_handle_from_handle(0, 0x100)) + plat_id();
       }
       function bit[32] scale(bit[32] x) {
           return x * 2;
       }
   }

and this test suite:

.. code-block:: pss

   import pssunit_pkg::*;

   component board_f : fixture_c {
       rt_c rt;
       board_c board;
       @pssunit_pkg::test
       function void t_probe() {
           rt.check_eq_int(board.probe(), 7 + 0x5A, "probe");
       }
       @pssunit_pkg::test
       function void t_scale() {
           rt.check_eq_int(board.scale(4), 8, "scale");
       }
   }

a platform implements both imports:

.. code-block:: python

   class Board(pv.Platform):
       def __init__(self):
           super().__init__()
           self.delays = []

       def plat_id(self):
           return 0x5A

       def plat_delay_us(self, us):
           self.delays.append(us)

and a session runs a test on it:

.. code-block:: python

   board = Board()
   s = pv.load(files, root="board_f", platform=board)
   assert s.run_test("board_f::t_probe", seed=1) == (1, 0, 0)
   assert board.delays == [3]
   assert ("write", 32, 0x100, 7) in board.log

Doubles
~~~~~~~

Three module-level decorators declare doubles, which ``Session.use`` (or
``load(..., use=[...])``) applies to a session. ``use`` accepts a module, a
module name or a ``pv.Registry``:

- ``@pv.implements("plat_delay_us")``: implements a user import. It is
  called instead of the platform's method.
- ``@pv.mock("board_c::scale")``: replaces any PSS function. It gets
  ``ctx``, the component, and the arguments.
- ``@pv.spy("board_c::scale")``: the real function runs, and each call is
  recorded as a ``pv.Call`` (also in ``session.calls``).

.. code-block:: python

   @pv.mock("board_c::scale")
   def _scale(ctx, self_, x):
       MOCKED.append(x)
       ctx.raise_irq(3)
       return 8

A mock or spy lasts the whole session and is applied again after each
construction. Mocks and spies need a debug image (the default of ``load``).
A double's ``ctx`` has ``session``, ``platform``, ``time`` (the run's clock:
``now``, ``advance``, ``advance_ns``, ``advance_us``) and ``raise_irq(n)``.

``raise_irq`` delivers the interrupt to the model: the root component's
``notify_irq()`` (or its nearest base's) runs as a thread of its own at the
next scheduling point, after the access or import that raised it returns
and the running thread yields, so a test blocked on what the handler posts
wakes up. ``load(..., irq_handler="<comp>::<fn>")`` names another handler;
``irq_handler=None`` delivers nothing. The ``notify`` event says
``{"irq": n, "delivered": true|false}``.

Binding a platform to a run
---------------------------

The PSS source never names a platform or a double; the run chooses them.
A model is therefore the same file whether it runs on the VM, on a board or in
generated code. In order of precedence:

1. ``pssc test --platform fake`` or ``--platform py:MOD[:CLASS]``;
2. ``platform = "..."`` under ``[tool.pssunit]`` in ``./pyproject.toml``;
3. otherwise, plain memory.

For ``py:MOD``, ``pssc test`` imports ``MOD`` (searching the current
directory first) and applies the doubles that its decorators declared to each
test. A module with doubles only:

.. code-block:: python

   import pssc.testing.vm as pv

   @pv.implements("plat_id")
   def plat_id(ctx):
       return 0x5A

With ``:CLASS``, each test also gets its own new ``CLASS()``, so state that
one test leaves on its platform is never seen by the next test:

.. code-block:: python

   import pssc.testing.vm as pv

   class Board(pv.Platform):
       def __init__(self):
           super().__init__()
           self.calls = 0
       def plat_id(self):
           self.calls += 1
           return 0x5A if self.calls == 1 else 0   # a shared instance fails t_b

The run fails with exit 3 if the module does not import, if it has no class
``CLASS``, or if it declares no doubles and names no class. It prints one line
on stdout and writes the traceback to ``.pssunit/platform.log``. From Python,
``pv.load_platform(spec)`` does the same and raises ``pv.PlatformLoadError``.

PSS reaction hooks
------------------

A mock written in PSS reacts to the driver the way the device would. It runs
on the register fake under ``pssc test`` and from Python, and needs no
Python code. A mock is a component derived from ``pssunit_pkg::mock_c``. Its
hooks are ``target function``\ s with one of these annotations:

- ``@pssunit_pkg::on_write {.reg = "...", .field = "..."}`` runs after a
  write to the register, or, with ``.field``, after a write of a 1 to that
  field;
- ``@pssunit_pkg::on_read {.reg = "..."}`` runs after a read;
- ``@pssunit_pkg::on_event {.name = "..."}`` runs when
  ``pssunit_pkg::schedule_after(delay, name, arg)`` comes due, and is given
  ``arg`` if it takes an ``int``.

A register hook may take one ``pssunit_pkg::access_s`` (``addr``, ``width``,
``data``, ``is_write``, and ``index``: the register's first array index, or
-1). ``.reg`` names a register as ``<group type>.<register>``, or by its
instance path. Names are resolved when the model is lowered, so a typo stops
the test with an error at the annotation:
``hooks_a.pss:5:5: pssunit_pkg::on_write on wb_dma_mock_c::armed:
'wb_dma_ch_regs_c.cs' names no register``.

This mock finishes a WB DMA copy two ticks after the driver sets a
channel's ``ch_en``. It then sets that channel's ``CSR.done`` and raises the
interrupt:

.. code-block:: pss

   component wb_dma_mock_c : pssunit_pkg::mock_c {
       @pssunit_pkg::on_write {.reg = "wb_dma_ch_regs_c.csr", .field = "ch_en"}
       target function void armed(pssunit_pkg::access_s a) {
           pssunit_pkg::schedule_after(2, "done", a.index);
       }
       @pssunit_pkg::on_event {.name = "done"}
       target function void finish(int ch) {
           pssunit_pkg::poke_field_at(ch, "wb_dma_ch_regs_c.csr", "done", 1);
           pssunit_pkg::raise_irq();
       }
   }

A test file binds the mock by adding it to the fixture. The driver's wait
then ends when the mock completes the copy:

.. code-block:: pss

   extend component wb_dma_fx_c {
       wb_dma_mock_c mock;

Hooks can also call ``poke_field_at(index, reg, field, v)`` (the back door,
for the register ``reg`` names at ``index``; one alias of a register is all
of them), ``raise_irq(n)`` (delivered as above), and
``state_get(key)``/``state_set(key, v)``, which hold the mock's state in the
double, because component fields cannot change after ``init``. A hook runs as
a thread of its own in its mock instance, at the next scheduling point after
the access. It does not change the access.

Virtual time moves on only when every thread waits. A driver that waits for
an event (``wb_dma_cfg_pkg::HAS_EVENT_WAIT``) lets a ``schedule_after``
come due. A driver that polls with a bare ``yield`` never does, so it needs
``status_sequence`` or ``complete_after``. Those Python helpers are hooks of
the same kind, kept in the fake's hook list.

Under pytest
------------

The pytest plugin (``pssc.testing.pytest_plugin``) does nothing until the
pytest configuration sets ``pssunit_paths``. It then collects the PSS tests
under those paths as pytest items. ``pssunit_platform`` takes the same values
as ``--platform``. The ``pss`` fixture opens a ``Session`` on the same
translation:

.. code-block:: python

   def test_twice(pss):
       s = pss.load("calc_c")
       assert s.call(s.construct(), "twice", 21) == 42

The configuration, node IDs, seeds and outcome mapping are in
:doc:`test-command` ("Under pytest").
