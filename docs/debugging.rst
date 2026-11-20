Debugging
=========

.. note::

   This page is a skeleton. The in-process debug session and the ``pssc
   dbg`` command line it describes exist; VS Code, traces, replay and time
   travel come with later work and their sections say so.

A PSS operation test runs on an engine: pyvm (the Python VM of
``zuspec-be-bc``) on the host, or the native engine inside a simulation.
Every engine has a small **debug stub** that knows coroutines, program
counters and addresses (its protocol is the debug-stub spec,
``spec/debug-stub.md`` in the ``zuspec-be-bc`` docs). The **debug front end** in ``pssc`` knows what the stub
does not -- files and lines, register paths, PSS names and expressions -- and
translates between the two. Debugging needs an image lowered with debug
information (``debug=True``; ``pssc test`` does this when it debugs).

Breakpoints
-----------

A breakpoint is set on a ``file:line``. A line that starts no statement (a
blank line, a ``}``, a comment) moves to the next line that does; a line
outside every function or action body stays *unverified*. A function inlined
in several places breaks in each of them.

Each breakpoint may have:

``condition``
   A PSS expression; the run stops only where it is non-zero. A condition
   that cannot be evaluated stops the run and says why.
``hit_condition``
   ``N`` or ``==N`` (the Nth hit), ``>=N``, ``>N``, ``<=N``, ``<N``, or
   ``%N`` (every Nth hit), counted over the hits whose condition held.
``log_message``
   A *logpoint*: the run does not stop; the message is written with each
   ``{expr}`` replaced by its value (``{{`` and ``}}`` are braces).

Register watches
----------------

A watch names a register or a register field by its instance path, as in the
register map: ``ch[1].regs.csr`` or ``ch[1].regs.csr.prio``. It stops the run
after a matching access by the operation code:

``rw``
   ``w`` (the default), ``r`` or ``rw``.
``value``
   Stop only when the access carries this value of the field (or register).
``condition``, ``hit_condition``
   As for breakpoints.

A register's address is known once its component's base is: from the
platform when it has seen an access already, or else from the run's first
access to any register of that component. Until then the watch is unverified;
it is armed when the base becomes known.

Variables
---------

At a stop, each frame has these scopes:

**Locals**
   The function's or exec block's local variables and parameters; the fields
   of a struct are shown under it.
**Action**
   The running action's attributes.
**Component**
   The fields of the frame's component instance, by their names relative to
   it.
**Registers**
   The component's registers, read through the *back door* (no bus access, so
   no side effect and nothing in the bus log), with each field decoded.
**Channels**
   The latest channel events.
**Bus**
   The last accesses on the platform's bus, newest first.

Expressions
-----------

Conditions, logpoints and the ``evaluate`` request take a PSS expression in
the stopped frame: locals and parameters, struct fields (``cfg.src``), the
component's fields (``chan``), action attributes, registers and their fields
(``regs.csr.prio``, relative to the frame's component, or absolute,
``ch[1].regs.csr.prio``), other instances' fields, and integer literals
(``16``, ``'h10``, ``8'hff``). The operators are PSS's arithmetic, bitwise,
shift, comparison, logical and ``?:``. Register values are read when the
expression is compiled, through the back door. Not yet supported: enum
literals, function calls, whole structs and strings.

Debugging from Python
---------------------

:class:`pssc.devenv.debug.frontend.DebugSession` is the session the daemon's
commands and the DAP server will use; tests and scripts can drive it
directly. It wraps a stub that is not yet attached:

.. code-block:: python

   from zuspec.be.bc.interp.debug import Stub
   from pssc.devenv.debug.frontend import DebugSession
   import pssc.testing.vm as pv

   fake = pv.RegisterFake()
   s = pv.Session(model, platform=fake)        # model: lowered with debug=True
   s.call(s.construct(), "initialize", 0x10000000)

   def run():
       s.call(s.instance("ch[1]"), "configure_channel", *cfg)

   d = DebugSession(Stub(s.vm, run=run), model, platform=fake)
   d.set_breakpoints("configure_channel.pss",
                     [{"line": 46, "condition": "cfg.prio == 5"}])
   d.set_watches([{"path": "ch[1].regs.csr.prio", "value": 2}])
   d.start()                    # attached, stopped before the first instruction
   stop = d.cont()              # {"event": "stopped", "reason", "source", ...}
   d.evaluate("cfg.src")
   d.scopes()                   # [{"name": "Locals", "ref": ...}, ...]
   d.end()

A run request (``cont``, ``step``) returns one record: ``{"event":
"stopped", ...}`` with the stop's ``reason`` (``breakpoint``, ``watch``,
``step``, ``pause``, ``check``, ``error``, ``sanitizer``), ``source``
(``file``, ``line``), the ``breakpoints`` or ``watches`` that stopped it and
the logpoint ``output`` written on the way; or ``{"event": "exited",
"status", "output"}`` at the end.

Command-line sessions
---------------------

``pssc dbg`` debugs one test from the shell, one command at a time.
``pssc dbg start`` takes the same sources and test selection as ``pssc
test`` (it must select exactly one test), lowers the model for debugging
and stops before the test's first instruction. The session lives in the
workspace daemon, so it outlives the client: every later ``pssc dbg``
command, each its own process, works on the same run. Commands go to the
latest session, or to ``--session ID``. A session ends on ``pssc dbg end``
or when the daemon exits (``pssc daemon stop``, or idle).

A session on the WB DMA suite (``examples/op_model/tests``):

.. code-block:: text

   $ pssc dbg start @model.f . wb_dma_fx_c::t_routing_mask --seed 1 --platform fake
   session 1: stopped (entry)
     test wb_dma_fx_c::t_routing_mask seed 1
   $ pssc dbg break add prio_a.pss:33
   breakpoint 1 prio_a.pss:33
   $ pssc dbg watch dma.regs.int_msk_b --rw w
   watch 1 dma.regs.int_msk_b (w) -- dma.regs.int_msk_b: the address is known after the first access to a register of dma
   $ pssc dbg run
   session 1: stopped (watch) at ../pss/wb_dma_c/functions/configure_interrupt_routing.pss:34
   $ pssc dbg bt
   #12 configure_interrupt_routing$call5  ../pss/wb_dma_c/functions/configure_interrupt_routing.pss:34
   #0 t_routing_mask$call9  prio_a.pss:33
   $ pssc dbg regs dma.regs.int_msk_b
   dma.regs.int_msk_b = 0x5
     ch = 5
     rsvd_31 = 0
   $ pssc dbg bus -n 2
   [0] = write dma.regs.int_msk_b = 0x5  (../pss/wb_dma_c/functions/configure_interrupt_routing.pss:34:29)
     ch = 5
     rsvd_31 = 0
   $ pssc dbg eval 1 + 2
   3 (0x3)
   $ pssc dbg next
   session 1: stopped (breakpoint) at prio_a.pss:33 breakpoint 1
   $ pssc dbg run
   session 1: exited: 2 checks passed, 0 failed
   $ pssc dbg end
   session 1 ended

The commands:

- ``break add FILE:LINE`` (``--condition EXPR``, ``--hit N|>=N|%N``,
  ``--log TEXT`` for a logpoint), ``break list``; ``watch PATH`` (``--rw
  r|w|rw``, ``--value N``, ``--condition EXPR``) as in `Breakpoints`_ and
  `Register watches`_.
- Run commands: ``run`` (or ``continue``), ``step`` (into), ``next`` (over),
  ``out``, and ``pause``.
- Where it stopped: ``bt`` (``--thread``), ``locals`` (``--frame``), ``regs
  [PATH]`` and ``eval EXPR`` (both through the back door: the bus log does
  not change), ``bus [-n N]`` (the last bus accesses), ``threads``.
- ``reload`` (``--strict``, ``--cancel``): hot reload. The session's sources
  are lowered again and become its next generation; while the run has not
  ended the new generation is staged (``applied`` false) and the run ends on
  the code it started with. The ``--platform`` module's doubles are imported
  again and apply at once. A change to a test's or an import's signature
  (or, with ``--strict``, to the component tree) is refused: exit status 1,
  and with ``--json`` the refusal as ``{"error": {"code", "reason",
  "message", "changes"}}``. More in :doc:`hot-reload`.
- ``list`` (the sessions), ``end``.

A run command waits for the next stop, at most ``--timeout`` seconds
(default 30). If the run is still going then, it says ``running`` and the
run goes on; the next run command waits for the same stop.

``--json`` prints one JSON document. A run command's (and ``start``'s) is a
stop record, schema ``pssunit.stop/1`` (`docs/schemas/pssunit.stop-1.json`_):
the record of `Debugging from Python`_ with ``schema`` and ``session``
added, and ``event`` one of ``stopped``, ``exited`` (with ``checks``:
``passed``, ``failed``, ``skipped``) or ``running`` (with ``timeout``):

.. code-block:: text

   $ pssc dbg run --json
   {
     "checks": {
       "failed": 0,
       "passed": 2,
       "skipped": 0
     },
     "event": "exited",
     "output": [],
     "schema": "pssunit.stop/1",
     "session": "1",
     "status": "ok"
   }

An error (no session, a run that has ended, a bad ``FILE:LINE``) exits
with status 1 and a ``pssc: error: ...`` line on stderr.

.. _docs/schemas/pssunit.stop-1.json: schemas/pssunit.stop-1.json

VS Code
-------

``pssc dap`` is a Debug Adapter Protocol server on stdin and stdout: an
editor starts it as its debug adapter. It relays the stream to the
workspace daemon, so a test the editor launches is a ``pssc dbg`` session
too. With ``PSSC_NO_DAEMON=1`` (or when no daemon can be started) it serves
the protocol in its own process, and the session lives as long as the
editor's connection.

The ``pss`` debug type of the ``vscode-pss-support`` extension (still to
come) runs ``pssc dap``; any DAP client that starts an executable works the
same way. A ``launch.json`` with one configuration that launches a test and
one that attaches to a session started by ``pssc dbg start``:

.. code-block:: json

   {
     "version": "0.2.0",
     "configurations": [
       {
         "name": "Debug t_routing_mask",
         "type": "pss",
         "request": "launch",
         "cwd": "${workspaceFolder}/examples/op_model/tests",
         "sources": ["@model.f", "."],
         "test": "wb_dma_fx_c::t_routing_mask",
         "seed": 1,
         "platform": "fake",
         "stopOnEntry": true
       },
       {
         "name": "Attach to a pssc dbg session",
         "type": "pss",
         "request": "attach",
         "session": "1"
       }
     ]
   }

``launch`` takes what ``pssc dbg start`` takes: ``sources`` (default
``["."]``, relative to ``cwd``), ``test`` (required; it must select one
test), ``seed``, ``platform`` and ``timeout``. With ``stopOnEntry`` the run
stops before the test's first instruction; otherwise it runs once the
editor has set its breakpoints. ``attach`` without ``session`` takes the
latest one. Disconnecting ends a session the editor launched and leaves an
attached one running.

What the editor can do:

- Breakpoints on a ``FILE:LINE``, with a condition, a hit count
  (``N``, ``>=N``, ``%N``) or a log message (a logpoint), as in
  `Breakpoints`_.
- Data breakpoints on registers (*Break on value change* / read / access
  on a register in the Registers scope): a register watch of `Register
  watches`_.
- Exception breakpoints: *Errors* (on by default), *Failed checks* and
  *Sanitizer findings*.
- Continue, step over, step into, step out and pause; the call stack of
  each thread.
- The scopes of a frame: Locals, Component (the fields of the component
  the action runs in), Registers and Bus (the last bus accesses), as in
  `Variables`_.
- Evaluate, in the Debug Console and on hover: `Expressions`_.
- Apply change: the custom request ``pssc/reload`` reloads the edited
  sources into the session, as ``pssc dbg reload`` (:doc:`hot-reload`).

The editor and the command line can work on one session at once. A
``pssc dbg run`` that hits a breakpoint stops the editor at that line as
well, and a *Continue* in the editor is a run that ``pssc dbg`` sees: every
client of a session gets every stop.

Traces, replay and time travel
------------------------------

Traces
~~~~~~

A trace is every event one test ran: each statement, call and return, each
register access, check, branch arm, channel operation, message and
sanitizer verdict, in order, with its source line. ``pssc test`` writes
them when asked:

``--trace all``
    one trace per test.

``--trace fail``
    the tests run untraced; a test that fails is run once more, with the
    seed it failed at, traced, and only that trace is kept. If the re-run
    does not fail the same way, the failure card says ``flaky``; the
    result is still the first run's.

Traces go to ``.pssunit/traces/<test-id>.ndjson`` (``::`` becomes ``.``;
``--trace-dir DIR`` moves them). The failure card names the file
(``trace: .pssunit/traces/wb_dma_fx_c.prio_a.ndjson``), and so does
``test.end``'s ``trace_file`` in ``--json``. Tracing needs the debug image,
so not with ``--no-debug``.

``pssc trace show TRACE`` prints one line per event: its index, its
coroutine (``#N``), its kind, its source location and payload, and the
source line itself. ``--kind K`` (repeatable) and ``--coro N`` filter the
list. With the WB DMA suite and a seeded bug::

    $ pssc trace show .pssunit/traces/wb_dma_fx_c.t_poll_pending.ndjson --kind sanitizer
    trace .pssunit/traces/wb_dma_fx_c.t_poll_pending.ndjson: test wb_dma_fx_c::t_poll_pending seed 1371199115, 179 events
       178 #0   sanitizer  ../pss/wb_dma_ch_c/functions/check_completion.pss:67  name=inflight verdict=fail what="channel inflight still holds 1 token ..."  | inflight.try_put(tok);

``--at IDX`` shows one event and the source lines around it
(``--context N``, default 2). An event with no location of its own (a
call, a return) maps to the last located event before it::

    $ pssc trace show .pssunit/traces/wb_dma_fx_c.t_poll_pending.ndjson --at 60
    trace .pssunit/traces/wb_dma_fx_c.t_poll_pending.ndjson: test wb_dma_fx_c::t_poll_pending seed 1371199115, 179 events
        60 #11  call       -  args=[3, 268435584] depth=2 fn=initialize$call27
    ../pss/wb_dma_regs_pkg.pss:170 (from event 58)
         168 |         pure function bit[64] get_offset_of_instance_array(string name, int index) {
         169 |             match (name) {
      >  170 |                 ["bank"]: return 0x20 + index*0x20;
         171 |                 default: return -1;
         172 |             }

``--line FILE:N`` goes the other way: every event that ran line N of FILE
(a path, or its trailing part)::

    $ pssc trace show .pssunit/traces/wb_dma_fx_c.t_poll_pending.ndjson --line probe_status.pss:39
    trace .pssunit/traces/wb_dma_fx_c.t_poll_pending.ndjson: test wb_dma_fx_c::t_poll_pending seed 1371199115, 179 events
    probe_status.pss:39: run at 125, 126, 156, 157
       125 #13  stmt       ../pss/wb_dma_ch_c/functions/probe_status.pss:39  scope=wb_dma_ch_c::probe_status  | csr = regs.csr.read();
       126 #13  access     ../pss/wb_dma_ch_c/functions/probe_status.pss:39  addr=268435552 data=1 reg_id=66 rw=r width=32  | csr = regs.csr.read();
       ...

Source paths are as the model spells them, usually relative to the
directory ``pssc test`` ran in. ``trace show`` looks for them under each
``--source-root DIR``, then the cwd, then the run's directory (two above
the trace's), then the trace's own. ``--json`` prints one document, schema
``pssc.trace-show/1`` (``docs/schemas/pssc.trace-show-1.json``).

A trace file is be-bc's NDJSON trace, version 2: a header line
``{"zbc_trace": 2, "test": ..., "seed": ...}``, then one event per line
(``seq``, ``kind``, ``coro``, ``src_ref``, ``detail``) with ``file`` and
``line`` added where the event has a location. ``seq`` is the index
``--at`` takes. A trace from a simulation (``+pssvm_trace=DIR``, see
:doc:`simulation`) also has ``t`` on each event, its sim time in fs; there
``--at`` also takes a time with a unit (``--at 1.234us``; fs, ps, ns, us,
ms, s): the last event at or before it.

``trace show`` also reads the binary form of the same records
(``.pstrace``: be-bc's ``trace/binary.py``; the C engine writes it), told
apart by its first bytes. Its events may carry only ``src_ref``, an index
into the model's source table: give the model image the run loaded with
``--image FILE.zbc`` (or let the trace header name it, ``image``, relative
to the trace) and each such event gets its ``file`` and ``line`` as in an
NDJSON trace.

Debugging in a simulation
~~~~~~~~~~~~~~~~~~~~~~~~~

The same debugger works on a model running in a simulation
(:doc:`simulation`): ``pssc sim attach`` opens a ``pssc dbg`` session on
the engine in the simulator, and breakpoints, stepping and variables work
as above while sim time stands still. ``+pssvm_dbg_on_fail`` holds the
simulation after a failed test for ``pssc sim attach``, then ``pssc sim
resume --rerun`` runs the test again under the debugger. Two plusargs tie
the model to the waveform: ``+pssvm_trace=DIR`` writes a trace per test
with each event's sim time (``pssc trace show --at 1.234us``), and
``+pssvm_dbgsig`` drives module ``pssvm_dbg``'s ``cur_op``, ``cur_file``
and ``cur_line`` signals with the function and line the model is at.

Replay and time travel
~~~~~~~~~~~~~~~~~~~~~~

A run recorded with ``--trace all`` can be run again without the bench
that made it: ``--platform replay:TRACE`` (:doc:`test-command`) answers
the test's reads and user imports from the recording, in order, and fails
the test at the first access or import that is not the recorded one::

    $ pssc test t.pss --trace all --platform sim:dma0   # record
    $ pssc test t2.pss --platform replay:.pssunit/traces # a changed model
    t2.pss:31:9: first divergent access (step 2 of 3 recorded): expected
      write32 0x1000 = 0x5a (t.pss:31), got write32 0x1004 = 0x5a

``TRACE`` is one test's trace, or the directory of traces, from which each
test takes its own. Either form is read: NDJSON, as ``pssc test`` and a
simulation's ``+pssvm_trace`` write it, or the binary ``.pstrace`` form of
the C engine (whose events have no source line, so the message gives the
address only). The replay takes the recorded seed unless ``--seed`` is
given, and raises the interrupts the recorded run raised at the access or
import that raised them, so the handler runs at the same point.

A replay is an ordinary run in the Python engine, so it can be debugged:
``pssc dbg start --platform replay:TRACE`` (or a launch configuration with
that platform) stops at breakpoints in a run whose bus traffic came from a
simulation, with sim time no longer in the way; ``pssc dbg start --trace
TRACE`` is the same. Such a run, or any run on the fake platform, can also
be stepped backwards:

``pssc dbg back``
    to the start of the statement before the current one in the stopped
    thread (the reverse of ``step``); at the first statement, the run's
    start (stop reason ``start``).
``pssc dbg rc``
    reverse-continue: to the last breakpoint or watch stop before here, with
    its hit counts as they were then; none, the run's start.
``pssc dbg rc --watch dma.regs.ctrl.en``
    to the last write of a register or field, through a watch that is gone
    afterwards: *who last wrote this?* It lands at the start of the
    statement that wrote it.

In VS Code, *Step Back* and *Reverse* on the debug toolbar do the same as
``back`` and ``rc``.

There are no snapshots: going back runs the same run again from its start
(the same image and seed, a fresh platform) to an earlier statement count,
so each step back costs as much as the run so far. That needs the Python
engine and a platform that answers the same way each time (the fake, a
replay); a C-engine or simulation session refuses (``back needs the Python
engine``), as does a run that has ended. After a staged reload, going back
runs the image the session started with.

Where a value came from
~~~~~~~~~~~~~~~~~~~~~~~

A ``--trace all`` (or ``--trace fail``) trace of a debug image records, for
each statement, the values it reads and writes, and names the register of
each bus access. ``pssc trace`` answers three questions from it, with no
rerun:

``pssc trace why TRACE REG[.FIELD]``
    where the register's last written value came from: the write, the
    statement that made it, and each value that statement read, followed
    back through the statements that set it to a register read, an import's
    return, a function's result, a component field's initial value, or the
    operation's inputs (with the call's arguments)::

        $ pssc trace why .pssunit/traces/t.ndjson 'ch[1].regs.csr.mode'
        ch[1].regs.csr.mode = 1: written 164212 at event 90, configure_channel.pss:60
          by configure_channel.pss:60  | regs.csr.write(csr);
            l:csr.mode: set at configure_channel.pss:45  | csr.mode = (bit)cfg.mode;
              l:cfg.mode: operation input

    For ``REG.FIELD`` it is the last write that changed the field.
``pssc trace slice TRACE --reg REG``
    every access to the register, in order, with its data, its field values
    and the statement that made it.
``pssc trace diff A B``
    the first bus access or import where two runs differ (two seeds, a
    model before and after an edit): its step number and the event on each
    side, with its source line. The rule is the replay's, read data
    included; a run that ends first differs at the step it did not make.
    It exits 1 when the runs differ, 0 when they do not.

Each takes ``--json`` (schemas ``pssc.trace-why/1``, ``pssc.trace-slice/1``,
``pssc.trace-diff/1``; ``pssc help --json`` lists them). A trace from the C
engine or a simulation has no statement events: ``why`` gives the write
alone (with a note), ``slice`` gives no statements, and ``diff`` works
as for any trace.

Debugging generated code
------------------------

The op-model targets can point a native debugger, a coverage tool or a
simulator at the PSS source instead of the generated file: ``pssc compile
-t op-model-c --line-directives`` (also ``op-model-cpp`` and ``op-model-sv``)
precedes each generated statement with a ``#line`` (C, C++) or SystemVerilog
line directive naming its ``.pss`` file and line, so ``gdb`` breaks at
``model.pss:42`` and ``gcov`` counts PSS lines. ``op-model-py`` writes a
``# file:line`` comment instead. See ``pssc help compile``.
