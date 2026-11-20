Simulation integration
======================

A testbench written against op-model-sv's API (``<root>_exp_if``,
``<root>_imp_if``, ``<root>_root #(Timp)::create``) can run the model two
ways:

- **gen**: op-model-sv's generated SystemVerilog classes;
- **vm**: the compiled image (``pssc build --image``) run by an engine in the
  simulation, over ``pssvm_pkg``. The image can be rebuilt without
  recompiling the testbench.

Both make the same bus accesses through the same ``<root>_imp_if``.
Verilator is the simulator checked so far; the engine reaches Python through
pyhdl-if.

The image
---------

Build an image that holds every function of the root component, so that each
API call has an entry:

.. code-block:: text

   $ pssc build @model.f . --root wb_dma_c --api --image wb_api.zbc

The ``op-model-sv-vm`` target
-----------------------------

``-t op-model-sv-vm`` writes a package with op-model-sv's API files, text
for text, and a facade in place of the generated classes:

- ``<type>_exp_vm``: a sub-component's API, each method a call into the
  engine by its ``comp::fn`` name on the instance it stands for;
- ``<root>_vm_plat``: the platform that sends the engine's bus accesses to
  your ``<root>_imp_if``;
- ``<root>_root_vm #(Timp)::create(imp, <ctor args>)``: as
  ``<root>_root``'s, over the engine ``pssvm_default_engine()`` returns.

.. code-block:: text

   $ pssc compile <model .pss files> -t op-model-sv-vm --root wb_dma_c -o gen/

Compile the package with the same ``--target-cfg`` settings the image was
built with (the WB DMA tests set ``HAVE_EVENT_WAIT=false``): ``create``
compares the functions the facade calls, with their parameters, against the
image and refuses an image that differs, naming each change:

.. code-block:: text

   PSSVM-ERROR: wb_dma_c_root_vm::create: image API differs: wb_dma_c::notify_irq: not in the image

Choosing the implementation at run time: ``+pss_impl``
-------------------------------------------------------

``--with-gen`` puts op-model-sv's whole package and the facade in one
package, and adds ``<root>_root_sel #(Timp)::create(imp, <ctor args>)``. It
builds the model the ``+pss_impl`` plusarg names:

=====================  ================================================
``+pss_impl=gen``      ``<root>_root::create`` (also with no plusarg)
``+pss_impl=vm``       ``<root>_root_vm::create``, over the engine
anything else          an error message; ``create`` returns null
=====================  ================================================

.. code-block:: text

   $ pssc compile <model .pss files> -t op-model-sv-vm --with-gen --root wb_dma_c \
       --target-cfg HAVE_EVENT_WAIT=false --target-cfg HAVE_RUNTIME_SOLVER=false -o gen/

The testbench sets the engine (for ``vm``) and calls ``_root_sel``:

.. code-block:: systemverilog

   import pssvm_pkg::*;
   import pssvm_hif_pkg::*;
   import wb_dma_c_pkg::*;
   ...
   string img;
   if ($value$plusargs("img=%s", img))
     pssvm_set_default_engine(pssvm_engine_hif::create(img));
   m = wb_dma_c_root_sel #(my_imp)::create(imp, 'h1000);

One binary then runs ``+pss_impl=gen`` and ``+pss_impl=vm +img=wb_api.zbc``;
pssc's own test checks that the two make the same bus accesses at the same
times (``tests/unit/test_sv_vm_target.py``).

Building with Verilator
-----------------------

``pssc sv-pssvm-path`` prints the directory of ``pssvm_pkg.sv``,
``pssvm_hif_api_pkg.sv`` and ``pssvm_hif_pkg.sv``; ``--py`` prints the
``PYTHONPATH`` the engine's Python side needs. Compile them before the
generated package, with pyhdl-if's ``pyhdl_if.sv`` and its library:

.. code-block:: text

   $ D=$(pssc sv-pssvm-path)
   $ verilator --binary --timing +incdir+<pyhdl-if share>/dpi -Igen \
       -LDFLAGS -L<pyhdl-if lib dir> -LDFLAGS -lpyhdl_if \
       -LDFLAGS -Wl,--export-dynamic \
       -MAKEFLAGS LDFLAGS=-Wl,--no-as-needed,-L<pyhdl-if lib dir>,-lpyhdl_if \
       <pyhdl-if share>/dpi/pyhdl_if.sv $D/pssvm_pkg.sv $D/pssvm_hif_api_pkg.sv \
       $D/pssvm_hif_pkg.sv gen/pssc_reg_pkg.sv gen/wb_dma_c_pkg.sv tb.sv
   $ PYTHONPATH=$(pssc sv-pssvm-path --py) obj_dir/Vtb +pss_impl=vm +img=wb_api.zbc

(``python -c "import hdl_if; print(hdl_if.share(), hdl_if.libs())"`` prints
the pyhdl-if paths.)
The ``-MAKEFLAGS`` line puts the pyhdl-if library ahead of the model's
archive on the link line; without it, a link can fail with ``undefined
reference to `pyhdl_call_if_invoke_hdl_t'`` (pyhdl-if's DPI exports are in
the archive and nothing else pulls them in).

Running on the C engine
-----------------------

The same package also runs the image on pssc's C engine, with no Python
in the simulator. Build the image with ``--native``, which refuses up
front, with its location, a test that needs a Python double (a mock or a
spy), since nothing could run it in the simulator:

.. code-block:: text

   $ pssc build <model .pss files> --root wb_dma_c --api --image wb_api.zbc --native

``pssc sv-pssvm-path --lib`` prints the C engine's DPI library (built and
cached on first use). Compile ``pssvm_pkg.sv`` with ``+define+PSSVM_DPI``
and link the library; pyhdl-if is not needed:

.. code-block:: text

   $ D=$(pssc sv-pssvm-path); L=$(pssc sv-pssvm-path --lib)
   $ verilator --binary --timing +define+PSSVM_DPI $D/pssvm_pkg.sv \
       gen/pssc_reg_pkg.sv gen/wb_dma_c_pkg.sv tb.sv \
       -LDFLAGS $L -LDFLAGS -Wl,-rpath,$(dirname $L)
   $ obj_dir/Vtb +img=wb_api.zbc        # PYTHONPATH not needed

The testbench's engine is ``pssvm_engine_dpi``: ``new(img)`` loads the
image and constructs the root. Imports reach a subclass's ``call_import``
task by name (``req.name``), as on the default engine. Server mode, the
debugger (``+pssvm_dbg_on_fail``, ``+pssvm_dbg_start``, ``pssc sim
attach``) and ``pssc sim reload`` work on it as described below; start the
workspace daemon first (``pssc daemon start``), since the C engine cannot.
The library changes when pssc's C engine does: rebuild the simulator after
an upgrade.

The C engine stops a call frame that runs more than 10 million bytecode
steps (about 20 ms) with ``step limit exhausted``, so that a broken loop
ends instead of hanging the simulation. The Python engine has no such limit,
and ``pssc test --engine c`` runs without it. ``new``'s ``opts`` sets it:
``pssvm_engine_dpi::new(img, "", "step_limit=0")`` turns it off, and
``step_limit=N`` sets it to ``N``.

In a dv-flow flow: ``pssc.Image`` and ``pssc.Test``
---------------------------------------------------

The ``pssc`` dv-flow package has a task for the image and one that runs it:

.. code-block:: yaml

   tasks:
     - { name: pss, uses: std.FileSet, with: { type: pssSource, include: "*.pss" } }
     - { name: img, uses: pssc.Image, needs: [pss], with: { root: wb_dma_c, api: true } }
     - { name: test, uses: pssc.Test, needs: [img], with: { k: "smoke*" } }

``pssc.Image`` runs ``pssc build --image`` and emits ``img.zbc`` (the task
name, or its ``image:`` parameter) as a ``pssVmImage`` fileset. Its other
parameters are ``pssc build``'s: ``root``, ``api``, ``sources_in_image``
(``false`` is ``--no-sources``) and ``args``. ``pssc.Test`` runs
``pssc test --image`` on it in pyvm (``k``, ``platform``, ``seed``, ``args``),
writes ``pssc_test.log`` and ``pssunit-ctrf.json`` to its run directory, and
fails when a test fails.

A simulation flow builds the simulator from SystemVerilog only (the
``op-model-sv-vm`` facade and the testbench) and gives the run step the image
(``+img=<path to img.zbc>``). The image is never an input of the build, so a
PSS edit rebuilds the image and reruns the simulation without recompiling it.
``pssc.Image`` reports its output unchanged when the new image has the same
bytes as the last one (generation 0, the default, makes a build
reproducible), so an edit that changes nothing in the image reruns nothing.
With the sources kept in the image (the default), any edit to them changes
it.

Several tests in one simulation
-------------------------------

``pssvm_runner`` runs the image's tests (those ``+pssunit=GLOBS`` selects)
one after the other in one simulation, and each starts as it would under
``pssc test``: before every test but the first, the runner builds the
component tree again (``pssvm_engine::fresh``) and calls the platform's
``reset()`` task, which should put the design back in its reset state. The
default ``reset()`` does nothing.

A platform whose design cannot be reset says so:

.. code-block:: systemverilog

   class my_plat extends pssvm_platform_if;
     virtual function bit can_reset();
       return 0;
     endfunction
   endclass

The runner then runs only the first selected test; each other prints a
line and counts as neither passed nor failed:

.. code-block:: text

   PSSVM-TEST: PASS c_f::t_b @0..40
   PSSVM-TEST: SKIP c_f::t_c (platform cannot reset: one test per sim)
   PSSVM-SUMMARY: 1 passed, 0 failed, 1 skipped

Run one simulation per test (``+pssunit=NAME``) to run them all.

A suite with ``@pssunit_pkg::session`` functions (:doc:`writing-tests`)
works the other way: the runner calls them once, before the first test's
set-up, and the tests then share the model and the design: no new tree,
no ``reset()``, whatever ``can_reset()`` says. A session function that
fails makes every test fail.

In server mode the simulation keeps count across ``run`` requests: the
first test of a later ``run`` also gets a new tree and a reset, and on a
platform that cannot reset it is skipped.

Server mode
-----------

A testbench that runs ``pssvm_runner`` can, instead of running the image's
tests, stay up and take work from the workspace daemon. Give it
``+pssvm_serve`` (or ``+pssvm_serve=NAME`` to name the session):

.. code-block:: text

   $ PYTHONPATH=$(pssc sv-pssvm-path --py) obj_dir/Vtb +img=wb.zbc +pssvm_serve=tb1
   PSSVM-SERVE: sim1
   ...
   PSSVM-SERVE: end

The runner registers with the daemon's simulator socket (the daemon of the
workspace the simulator runs in, or the socket named by the environment
variable ``PSSC_SIM_SOCKET``; the simulator does not start a daemon), then
answers the daemon's requests, moving simulation time on by one unit
between them, until the daemon ends the session; ``run`` then returns 0.
If it cannot register it prints ``PSSVM-SERVE: failed`` and the reason, and
``run`` returns 1. The daemon stays up while a simulator is connected.

A session answers ``ping``, ``info`` (its name, process id, directory,
top module and image), ``end`` and ``run``. ``run`` takes ``sel``, a list
of test globs as for ``+pssunit`` (none: every test), and runs those tests
in the same simulation, the model and the design keeping their state from
one run to the next. The runner prints the usual ``PSSVM-TEST:`` (with the sim time the test
started and ended at, ``@start..end``) and
``PSSVM-SUMMARY:`` lines, sends a ``test`` event (``name``, ``status``
``pass`` or ``fail``) as each test finishes, and answers the request with
the counts (``passed``, ``failed``) when the last one has.

``pssc sim`` sends these requests through the daemon:

.. code-block:: text

   $ pssc sim list
   sim1  tb1  pid 4242  top tb  image /work/wb.zbc
   $ pssc sim run '*prio*'
   PASS  wb_dma_fx_c::t_csr_prio_bits
   sim1: 1 passed, 0 failed
   $ pssc sim run 't_csr_*'
   ...
   $ pssc sim log
   [1] test  PASS  wb_dma_fx_c::t_csr_prio_bits
   ...
   $ pssc sim end
   sim1: ended

``--session ID`` picks a session by id or name (it may be left out while
only one is connected). ``run`` waits for the tests (``--timeout SECONDS``
to stop waiting) and exits 1 if one failed; ``log`` shows the session's
last 1000 events, numbered (``--since N``: those after N); ``end`` ends the
session and the simulation finishes. With ``--json`` each prints one JSON
object (not versioned): ``ok`` and ``error``, and ``sessions`` (``list``),
``session`` with ``passed``, ``failed`` and ``tests`` (``run``), ``events``
(``log``), ``old``, ``new`` and ``image`` (``reload``), ``dbg`` and
``image`` (``attach``), ``why`` and ``rerun`` (``resume``, below).

Every event a simulation sends carries the sim time it was sent at as
``t``, in femtoseconds whatever the timescale (``pssvm_now_fs()``): the
``test`` events of ``run``, ``held`` and ``resumed``, and the debugger's
``stopped`` and ``output``. The SV side tells the engine the time
(``pssvm_engine::set_time``) before each step of a call, so an event from
inside a call has the time of the bus access or wait that led to it.

Hot reload
~~~~~~~~~~

``pssc sim reload`` makes an image rebuilt from edited sources the
simulation's next generation (see :doc:`hot-reload`), without restarting
the simulation: the next ``run`` is on the new code, against the same
design state.

.. code-block:: text

   $ pssc sim run t_v
   FAIL  tests_c::t_v
   sim1: 0 passed, 1 failed
   $ # fix the source, then rebuild the image in place
   $ pssc build dut.pss t.pss --root tests_c --image /work/rl.zbc
   $ pssc sim reload
   sim1: generation 1 -> 2 (/work/rl.zbc)
     changed: ['tests_c::t_v']
   $ pssc sim run t_v
   PASS  tests_c::t_v
   sim1: 1 passed, 0 failed

``IMAGE`` names another image (default: the one the simulation loaded).
The testbench answers between runs, so the reload applies at once, at a
test boundary; the tests and setup/teardown hooks are the new image's.
The same checks as ``pssc dbg reload`` apply: a changed function
signature, or a constructor that touches the bus, refuses the reload, as
does a change to the component tree under ``--strict``. A refusal exits 1
(``--json``: ``refused`` holds the reason and the changes) and leaves the
simulation on its current generation.

On the C engine the reload is the same, in the simulator itself: a part
of the component tree that is unchanged (same path, type and fields)
keeps its state, a new or changed one starts fresh (``--strict`` refuses
instead). A reload clears the debugger's breakpoints; an attached client
sets them again. The bench in ``examples/fw_wb_dma_uvm`` shows it:
``reload_native.sh`` holds a native cell at start, reloads a rebuilt
image (``generation 1 -> 2``), and stops at a breakpoint set afterwards.

Debugging a simulation
~~~~~~~~~~~~~~~~~~~~~~

``pssc sim attach`` puts a debug stub on the simulation's engine and makes
it a ``pssc dbg`` session (see :doc:`debugging`) on the image the
simulation loaded. The session is not stopped: the calls SV makes run on
until one reaches a breakpoint. The engine runs inside the SV call, so a
stop holds that call, and with it the whole simulation: sim time does not
move until the run goes on.

.. code-block:: text

   $ pssc sim attach
   sim1: dbg session 1 (image /work/rl.zbc)
   $ pssc dbg break add dut.pss:2
   $ pssc sim run t_v &          # the call stops at the breakpoint
   $ pssc dbg continue           # waits for the stop
   session 1: stopped (breakpoint) at dut.pss:2 breakpoint 1
   $ pssc dbg bt
   $ pssc dbg continue           # the call returns; the run ends
   $ pssc dbg end                # the stub comes off; the simulation goes on

The ``pssc dbg`` commands work as on a ``pssc dbg start`` session (break,
watch, bt, locals, regs, eval, step, pause), and so does a DAP client on
it. ``pssc dbg end`` takes the stub off without ending the simulation;
``pssc sim end`` ends the debug sessions on the simulation first.
Register bases are not learned from the run's accesses (the engine is in
another process), and ``pssc dbg reload`` is refused: use ``pssc sim
reload``.

Holding a failing test
~~~~~~~~~~~~~~~~~~~~~~

With ``+pssvm_dbg_on_fail``, a test that fails holds the simulation instead
of going on. The runner prints ``PSSVM-DBG: held NAME @TIME`` and calls
``pssvm_dbg_wait()``: the engine registers with the workspace daemon (if
the simulation is not serving already), sends a ``held`` event, and answers
the daemon's requests inside that call, so sim time does not move. Attach a
debugger, reload a fix, then go on; ``--rerun`` runs the test again first,
and only the last run of a test counts in the summary:

.. code-block:: text

   $ ./simv +img=rl.zbc +pssunit=t_v +pssvm_dbg_on_fail &
   PSSVM-TEST: FAIL tests_c::t_v @0..10
   PSSVM-DBG: held tests_c::t_v @10
   $ pssc sim reload               # after the fix
   $ pssc sim resume --rerun
   sim1: resumed tests_c::t_v (run again)
   PSSVM-TEST: PASS tests_c::t_v @10..20
   PSSVM-SUMMARY: 1 passed, 0 failed

While held, ``pssc sim run`` is refused. A testbench can hold the
simulation itself with ``pssvm_dbg_wait("why")`` (package ``pssvm_pkg``,
on the default engine; 1 = run again, 0 = go on, -1 = no daemon). Long
holds can trip a simulator's watchdog or licence heartbeat.

Holding at start
~~~~~~~~~~~~~~~~

A testbench that makes its own calls (a UVM bench, with no runner) never
waits for a debugger. With ``+pssvm_dbg_start`` the engine holds as soon as
it is created: it prints ``PSSVM-DBG: held start @0`` and waits in
``pssvm_dbg_wait("start")`` until ``pssc sim resume``, so breakpoints can
be set before the first call. It works on both engines: the default one
and the C engine (``pssvm_engine_dpi``, no Python in the simulator).

The C engine cannot start the workspace daemon, so start it first; without
one, the hold returns at once:

.. code-block:: text

   $ pssc daemon start
   pssd: running (/work)
   $ ./simv +UVM_TESTNAME=copy_test +pss_img=wb_dma.zbc +pssvm_dbg_start &
   PSSVM-DBG: held start @0
   $ pssc sim attach
   $ pssc dbg break add configure_channel.pss:28
   $ pssc sim resume
   $ pssc dbg continue --timeout 30
   session 1: stopped (breakpoint) at configure_channel.pss:28 breakpoint 1
   $ pssc dbg end                  # the test runs on to the end

On the C engine ``+pssvm_dbg_on_fail``, ``pssc sim attach`` and ``pssc sim
reload`` work as above.

From ``pssc test``
~~~~~~~~~~~~~~~~~~

``pssc test --platform sim:SESSION`` does the build, the reload and the run
in one command, from the sources; with ``--watch`` it does so on every save:

.. code-block:: text

   $ pssc test dut.pss t.pss --watch --platform sim:tbW
   sim1: reloaded tests_c, generation 1 -> 2
   FAIL  tests_c::t_v
   0 passed, 1 failed (sim session sim1)
   --watch: waiting for a change (Ctrl-C ends)
   sim1: reloaded tests_c, generation 2 -> 3
   1 passed, 0 failed (sim session sim1)

The image goes to ``.pssunit/sim/SESSION.zbc``; one that has not changed
since the last round is not reloaded. See ``--platform sim:`` in
:doc:`test-command`.

Traces and waveforms
--------------------

With ``+pssvm_trace=DIR`` the runner traces each test run, setup and
teardown included: its events go to ``DIR/<test-id>.ndjson`` (``::``
spelled ``.``), the same be-bc NDJSON file ``pssc test --trace`` writes,
and the runner prints the file:

.. code-block:: text

   $ ./obj/simv +img=rl.zbc +pssvm_trace=tr
   PSSVM-TRACE: tr/tests_c.t_v.ndjson
   PSSVM-TEST: PASS tests_c::t_v @0..0
   PSSVM-SUMMARY: 1 passed, 0 failed

Each event also has ``t``, the sim time it happened at, in fs (whatever the
timescale). ``pssc trace show`` prints it on each row, and ``--at`` takes a
time with a unit (``fs``, ``ps``, ``ns``, ``us``, ``ms``, ``s``) as well as
an index: the last event at or before that time, with its source line. So
a time read off a waveform maps to the PSS line that was running:

.. code-block:: text

   $ pssc trace show tr/tests_c.t_v.ndjson --at 1.234us

A test run again after ``pssc sim resume --rerun`` rewrites its file. The
engine traces through ``pssvm_engine::trace(dir, name)`` ("" ``name`` ends
the trace); an engine of your own may leave it out (no trace).

With ``+pssvm_dbgsig`` the model's place is also a set of signals, for the
waveform beside the bus. Instantiate module ``pssvm_dbg`` (in
``pssvm_pkg.sv``) anywhere in the testbench and add its signals:

.. code-block:: systemverilog

   pssvm_dbg u_pssvm_dbg();

``cur_op`` (string) is the function the call runs (``tests_c::t_v``),
``cur_file`` (string) and ``cur_line`` (int) the statement it last stopped
at to wait for SV: a bus access or a target import. A statement the engine
runs without SV (a check, arithmetic) takes no sim time, so it never shows.
``seq`` counts the updates. ``pssvm_ctxt::call`` sets them after each
``run`` step, from ``pssvm_engine::cur_op/cur_file/cur_line(h)`` (""/0 in
an engine of your own that leaves them out); without the plusarg they stay
still. The string signals show in a waveform where the dump format and
viewer carry strings; otherwise ``$display`` them on ``seq``.

Not yet
-------

- Exported actions are not run by the engine (the facade's task reports
  ``PSSVM-ERROR``); functions are.
- A string argument or result, and a callee's writes to a struct argument,
  are not carried.
- The root's constructor runs inside ``create`` and must not access the bus.
