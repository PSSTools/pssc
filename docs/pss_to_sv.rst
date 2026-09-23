PSS to SystemVerilog Backend
=============================

The ``zuspec-fe-pss`` SV backend translates PSS source into synthesisable
SystemVerilog 2017 classes that can be compiled directly with VCS, Questa, or
Xcelium.  The only Zuspec dependency at simulation time is the solver DPI
library (``libzsp_solver_dpi.so``), and it is only loaded for the small subset
of patterns that require joint-chain solving.

.. contents:: Contents
   :local:
   :depth: 2

Quick Start
-----------

From Python::

    from zuspec.fe.pss import generate_sv_files

    files = generate_sv_files(
        ["dma.pss", "top.pss"],
        output_dir="./sv_out",
    )
    # Produces: zsp_dpi_pkg.sv, zsp_rt_pkg.sv, zsp_gen_pkg.sv, zsp_filelist.f

Compile and simulate::

    # VCS
    vcs -sverilog -f sv_out/zsp_filelist.f -o simv && ./simv

    # Questa
    vlog -f sv_out/zsp_filelist.f && vsim -c zsp_test_top -do "run -all"

    # Xcelium
    xrun -f sv_out/zsp_filelist.f

Generated Files
---------------

====================  ============================================================
File                  Contents
====================  ============================================================
``zsp_dpi_pkg.sv``    DPI import declarations for the Zuspec solver interface.
``zsp_rt_pkg.sv``     Runtime classes: ``zsp_action``, ``zsp_component``,
                      ``zsp_resource_pool``, ``zsp_stream_channel``,
                      ``zsp_state_pool``, trace macros.
``zsp_gen_pkg.sv``    Generated package containing one SV class per PSS action
                      and component type.
``zsp_filelist.f``    ``-f`` filelist in dependency order.
====================  ============================================================

Action Lifecycle
----------------

Every generated action class follows this five-step lifecycle, matching the
PSS execution model:

.. code-block:: systemverilog

   action_inst.pre_solve();          // construct output flow objects; disable
                                     // sub-action constraint graphs
   if (!action_inst.randomize())     // SV solver: fields + inline constraints
       $fatal(1, "randomize failed");
   action_inst.post_solve();         // sample covergroups
   action_inst.body();               // exec body + activity traversal
   // (resource release happens after body inside the activity block)

Constraint Solving Strategy
----------------------------

Three tiers, chosen per-action at generation time:

**SV-native** -- No flow-object inputs, no cross-action constraints.
``randomize()`` handles everything; no DPI overhead.

**SV with injection** -- One or more flow-object input fields.  The solved
values from the upstream producer are injected before ``pre_solve()`` and
pinned via ``randomize() with { field == pinned_value; }``.  SV solver only;
DPI not involved.

**DPI joint-chain** -- Cross-action constraints span a pipeline chain.  The
backend compiles the full chain problem at generation time, embeds it as a
base64 constant, and emits the five-step DPI protocol at runtime.  Used for
pipeline ``schedule`` blocks with exit constraints that back-propagate.

Flow Objects
------------

Buffer and stream objects
~~~~~~~~~~~~~~~~~~~~~~~~~

Output ``buffer`` / ``stream`` fields are declared ``rand`` on the action
class.  This is necessary (and VCS-verified) so that the SV randomization
engine includes the output object's sub-fields in its constraint solve.
Input fields are non-``rand`` and are injected by the activity block before
``pre_solve()``.

.. code-block:: pss

    buffer packet_buf { rand payload_s payload; rand int seq_num; }

    action send_pkt {
        output packet_buf pkt;       // rand handle in SV
        constraint pkt.seq_num >= 0;
    }

Generated::

    class tx_c__send_pkt extends zsp_action;
        rand packet_buf pkt;          // rand -- solver can constrain sub-fields
        virtual function void pre_solve();
            if (pkt == null) pkt = new();
        endfunction
    endclass

State objects
~~~~~~~~~~~~~

State objects behave like buffers for output fields (declared ``rand``).  For
**input** state fields the activity block injects the reference before
``pre_solve()``; no ``rand_mode(0)`` is applied because VCS applies
``rand_mode`` class-wide rather than per-instance.

.. code-block:: pss

    state link_state_s {
        rand bool established;
        rand int  retry_count;
    }

    action link_connect {
        input  link_state_s prev;
        output link_state_s next;           // rand in SV
        constraint next.established == true;
        constraint next.retry_count == prev.retry_count;
    }

Activity block injection (produced by the lowering)::

    // inject before pre_solve
    a_conn.prev = _flow_a_init_link;
    a_conn.constraint_mode(1);   // re-enable after parent randomize
    a_conn.pre_solve();          // next = new()
    if (!a_conn.randomize()) ...

Compound Actions and Named Handles
-----------------------------------

When an action declares named action handles (either at action scope or inside
the activity block), the lowering promotes them to class-level fields and
generates a ``pre_solve()`` that:

1. Constructs each handle (``if (h == null) h = new()``).
2. Calls ``h.constraint_mode(0)`` so that the parent action's own
   ``randomize()`` does not traverse the sub-action's constraint graph while
   flow inputs are still null.

In the activity ``body()``, before each named handle's own traversal:

.. code-block:: systemverilog

   a_conn.constraint_mode(1);   // re-enable for the explicit randomize
   a_conn.comp = comp;
   a_conn.pre_solve();
   if (!a_conn.randomize()) $fatal(...);

.. note::

   For compound actions with no direct ``rand`` fields (i.e. actions whose
   only purpose is to sequence sub-actions via an activity), the generated test
   harness skips the top-level ``randomize()`` call entirely.  The sub-actions
   are each randomized individually inside ``body()``.

Resource Pools
--------------

Declarations
~~~~~~~~~~~~

.. code-block:: pss

    resource dma_channel_r { rand bit[4] id; }

    component dma_c {
        pool [4] dma_channel_r ch_pool;
        bind ch_pool *;

        action mem_copy {
            lock dma_channel_r ch;
        }
    }

Generated component::

    class dma_c extends zsp_component;
        zsp_resource_pool #(dma_channel_r) ch_pool;
        function new(string name, zsp_component parent);
            super.new(name, parent);
            ch_pool = new(16);   // capacity inferred; default 16
        endfunction
    endclass

Generated action::

    class dma_c__mem_copy extends zsp_action;
        dma_channel_r     ch;
        rand int unsigned ch_instance_id;
        constraint _c_ch_instance_id_bounds {
            ch_instance_id inside {[0:15]};
        }
    endclass

.. note::

   The PSS parser absorbs ``pool [N]`` declarations during linking so the
   declared capacity is not available in the linked AST.  The backend defaults
   to 16.  If a specific capacity is required, subclass the component and
   override the pool constructor argument.

Lock/unlock lifecycle
~~~~~~~~~~~~~~~~~~~~~

::

    // after randomize -- acquire
    comp.ch_pool.lock(inst.ch_instance_id);
    inst.ch = comp.ch_pool.get(inst.ch_instance_id);

    inst.body();

    // after body -- release (reverse alphabetical order)
    comp.ch_pool.unlock(inst.ch_instance_id);

``share`` uses ``try_share`` / ``unshare``.

Parallel coordination
~~~~~~~~~~~~~~~~~~~~~

When a ``parallel`` block has N branches that all claim from the same pool,
the backend emits a **head-action coordinated solve** before the ``fork``.  A
Fisher-Yates shuffle (N ≤ 8) or ``std::randomize`` with a ``unique``
constraint (N > 8) assigns distinct pool indices to each branch before any
branch starts, preventing lock contention.  Acquisition within each branch
still follows canonical alphabetical type order to eliminate hold-and-wait
deadlocks.

Coverage
--------

PSS ``covergroup`` blocks map to SV ``covergroup`` declarations sampled in
``post_solve()``.

.. code-block:: pss

    action write_op {
        rand int addr;
        rand bool cached;
        covergroup cg_write_op {
            cp_addr:   coverpoint addr { bins low = {[0:0xFF]}; }
            cp_cached: coverpoint cached;
        }
    }

Generated::

    class pss_top__write_op extends zsp_action;
        rand int addr;
        rand bit cached;
        covergroup cg_pss_top__write_op;
            cp_addr:   coverpoint addr;
            cp_cached: coverpoint cached;
        endgroup
        pss_top__write_op cg_inst;

        virtual function void post_solve();
            if (cg_inst != null) cg_inst.sample();
        endfunction
    endclass

The ``cover`` statement in exec blocks::

    `ZSP_TRACE("cover: point_name");
    cover (condition_expr);

Trace and Debug
---------------

The ``zsp_rt_pkg`` trace macros are controlled by the ``zsp_rt_verbosity``
runtime parameter.

========  ======================  ==================================================
Level     Macro                   Output
========  ======================  ==================================================
0         --                      Silent
1         ``ZSP_TRACE_ACTION``    Action entry (name, component path)
2         ``ZSP_TRACE_RESOURCE``  Resource acquire/release events
========  ======================  ==================================================

Set at simulation startup::

    ./simv +zsp_verbosity=2

Or programmatically in your testbench::

    zsp_rt_pkg::zsp_rt_verbosity = 2;

Supported PSS Features
-----------------------

**Legend:** ✅ Fully supported · 🔶 Partial (see note) · ❌ Not yet supported

Data types
~~~~~~~~~~

======================================  =============================  ========
PSS construct                           SV mapping                     Status
======================================  =============================  ========
``int`` / ``bit[N]`` / ``bool``         ``int`` / ``bit [N-1:0]``      ✅
``string``                              ``string``                     ✅
``chandle``                             ``chandle``                    ✅
``enum E { ... }``                      ``typedef enum { ... } E``     ✅
``struct S { ... }``                    ``class S`` + rand fields      ✅
``list<T>`` / ``array<T,N>``            ``T [$]`` / ``T [N]``          ✅
``map<K,V>`` / ``set<T>``               associative arrays             ✅
``bit[N] field[hi:lo]`` (constraint)    ``field[hi:lo]`` in SV         ✅
======================================  =============================  ========

Flow objects
~~~~~~~~~~~~

===================================================  ===================================  ========
PSS construct                                        SV mapping                           Status
===================================================  ===================================  ========
``buffer B { ... }``                                 ``class B extends zsp_buffer``       ✅
``stream S { ... }``                                 ``class S extends zsp_stream``       ✅
``state T { ... }``                                  ``class T extends zsp_state``        ✅
``output F f`` on action                             ``rand F f``; captured post-body     ✅
``input F f`` on action                              ``F f``; injected pre-``pre_solve``  ✅
``pool [N] T p; bind p *;``                          ``zsp_resource_pool #(T)``           ✅
Constraint back-propagation through binds            ``with { field == pinned; }``        ✅
===================================================  ===================================  ========

Resources
~~~~~~~~~

==============================================  ===================================  ========
PSS construct                                   SV mapping                           Status
==============================================  ===================================  ========
``resource R { ... }``                          class inheriting ``zsp_resource``    ✅
``lock R r``                                    ``rand int unsigned r_instance_id``  ✅
``share R r``                                   ``try_share`` / ``unshare``          ✅
Parallel head-action coordinated solve          Fisher-Yates or ``unique``           ✅
==============================================  ===================================  ========

Actions and activity
~~~~~~~~~~~~~~~~~~~~

==============================================  =======================================  ========
PSS construct                                   SV mapping                               Status
==============================================  =======================================  ========
``action A { ... }``                            ``class A extends zsp_action``           ✅
``abstract action A``                           ``virtual class A``                      ✅
Action inheritance + named constraint override  ``class Derived extends Base``           ✅
``exec body``                                   ``virtual task body()``                  ✅
``exec pre_solve`` / ``exec post_solve``        ``virtual function void``                ✅
``covergroup`` in action                        SV ``covergroup`` sampled in post_solve  ✅
Sequential activity                             ``begin ... end``                        ✅
Named action handle traversal ``h;``            lifecycle on class-level handle          ✅
Anonymous traversal ``do T;``                   ``T _anon = new(); ...``                 ✅
Inline constraint ``do T with { ... }``         ``randomize() with { ... }``             ✅
``parallel { ... }``                            ``fork ... join``                        ✅
``parallel join_first`` / ``join_none``         ``fork ... join_any`` / ``join_none``    🔶 pssparser gap
``schedule { ... }``                            Topologically ordered staged fork/join   ✅
``bind`` in schedule / activity                 Flow-object inject/capture wiring        ✅
``repeat (N)`` / ``repeat (i: N)``              ``repeat`` / ``for`` loop                ✅
``do-while`` / ``while-do``                     ``do...while`` / ``while``               ✅
``foreach``                                     ``foreach``                              ✅
``if``/``else`` / ``match``                     ``if``/``else`` / ``case``               ✅
``select { [w]: ... }``                         Weighted random via named begin block    ✅
``replicate (N)``                               ``fork`` with loop                       ✅
``atomic { ... }``                              Semaphore-guarded ``begin``              ✅
``super`` traversal                             ``super.activity()``                     ✅
==============================================  =======================================  ========

Exec-body statements
~~~~~~~~~~~~~~~~~~~~~

===========================================  =============================  ========
PSS construct                                SV mapping                     Status
===========================================  =============================  ========
Assignment / augmented assignment            Direct SV assignment           ✅
``if``/``else``, ``match``, loops            Standard SV control flow       ✅
``break`` / ``continue`` / ``return``        Direct SV equivalents          ✅
``message(verbosity, fmt, ...)``             ``$display(fmt, ...)``         ✅
``yield``                                    Comment (no SV equivalent)     🔶
``cover(expr)``                              ``cover (expr);``              ✅
``assert(expr)``                             ``assert (expr);``             ✅
===========================================  =============================  ========

Simulator Compatibility
-----------------------

=========  ===========  ==========================================================
Simulator  Status       Notes
=========  ===========  ==========================================================
VCS        Supported    Full ``std::randomize``, ``fork/join``, class support.
Questa     Supported    Full support.
Xcelium    Supported    Full support.
Verilator  Limited      No ``std::randomize`` or class randomization; compile-check only.
Icarus     Unsupported  No class support.
=========  ===========  ==========================================================

VCS-specific notes
~~~~~~~~~~~~~~~~~~

The following VCS behaviours influenced the generated code style:

* ``rand_mode(0)`` is applied class-wide in VCS (not per-instance).  The
  backend avoids calling it on flow-object handles for this reason.
* ``constraint_mode(0/1)`` is used per-handle to exclude sub-action constraint
  graphs from the parent's ``randomize()`` call.
* Output flow-object fields must be declared ``rand`` so VCS includes their
  sub-fields in the constraint solve.  Non-``rand`` handles are treated as
  fixed-value constants, making constraints like ``next.established == 1``
  unsatisfiable.
* Array declarations inside unnamed ``begin``/``end`` blocks inside
  ``virtual task`` bodies are rejected by VCS.  The ``select`` lowering uses
  a named ``begin : _zsp_sel`` block to work around this.

Known Gaps
----------

* ``join_first`` / ``join_none`` / ``join_select`` parallel modifiers require a
  pssparser grammar extension; the IR representation exists but parsing is not
  yet implemented.
* Resource pool declared capacity (``pool [N]``) is absorbed by the PSS linker
  before the IR translator sees it; the backend defaults to 16.
* ``assume`` in exec blocks is emitted as a comment.

