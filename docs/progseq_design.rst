Programming-Sequence Generation — Architecture
==============================================

This page summarizes how the ``op-model-sv`` target lowers a PSS component tree to
SystemVerilog. The authoritative design notes are
``design/pss-programming-seq-gen-design.md`` (scheme) and
``design/pss-programming-seq-gen-impl-plan.md`` (implementation/test/doc plan);
the validated reference output is
``examples/export/programming_seqs/wb_dma_sv_proto.sv``.

Pipeline
--------

``pssc compile -t op-model-sv --root <comp>`` resolves the root component from the
translated IR and walks its subtree:

1. **Classify** each reachable component by its ``super`` type
   (`pssc.targets.progseq_model`): ``reg_group_c`` → register group;
   ``packed_s`` → register value struct; otherwise a regular component.
2. **Register model** (`pssc.targets.sv.lower_reg_model`): emit one packed
   struct per value type (fields reversed — PSS is LSB-first, SV packed is
   MSB-first) and one class per ``reg_group_c``, with offsets folded into each
   child handle. Scalar offsets come from the IR's pre-computed ``offset_map``;
   array base/stride is recovered by evaluating the affine
   ``get_offset_of_instance_array`` arms.
3. **Programming API** (`pssc.targets.sv.lower_progseq`): for each regular
   component, an export interface ``<comp>_if`` (one ``task`` per operation) and
   the single component class ``<comp>`` (operations + import redirect + static
   ``create()``). Operation bodies are translated statement-for-statement.
4. **Emit** (`pssc.targets.progseq_gen`): assemble the package, import the
   core, write ``<package>.sv``, and copy ``pssc_reg_pkg.sv``.

Generated shape
---------------

.. code-block:: text

   pssc_reg_pkg            (core, shipped)   addr_handle_t, pss_mem_if, reg_c
   <root>_pkg              (generated)
     value structs         <reg>_s ... (packed, fields reversed)
     register groups       <group>_c ...
     export API            <comp>_if          (pure virtual task per operation)
     import API            <comp>_import_if   (extends pss_mem_if)
     component class       <comp> #(IMP_T)    (impl + import redirect + create)

Body-translation rules
----------------------

PSS operation bodies map to SV with a few rewrites:

==================================  ===========================================
PSS                                 SV
==================================  ===========================================
``int`` return                      leading ``output int status`` arg
arg after an ``output``             explicit ``input`` (SV inherits direction)
``regs.<p>.read()`` (value form)    ``m_regs.<p>.read(tmp)`` (task/output form)
``repeat { B } while (c)``          ``forever begin B; if (!(c)) break; end``
``return v``                        ``status = v; return;``
SV-keyword identifier               renamed (``priority`` → ``priority_``)
==================================  ===========================================

The component class as the bus
------------------------------

The folded component class ``<comp>`` ``implements <comp>_if, <comp>_import_if``.
It builds the register model with ``this`` as the bus: since ``<comp>`` is-a
``pss_mem_if`` (via the import interface), register accesses route
``m_regs`` → ``this`` → the held user object ``m_imp``. This removes the need for
a separate adapter or factory class.

Frozen decisions
----------------

- Memory-access primitives are **tasks** (front-door timing); reads use ``output``.
- PSS ``addr_handle_t`` → ``bit [63:0]``.
- Transaction width = value width rounded up to 8/16/32/64 (``ACC_W``).
- ``create()`` takes the import handle + the root ``ctor`` parameters.
- Interface classes are suffixed ``_if``; the component is one class ``<comp>``.

C and C++ backends
------------------

The ``op-model-c`` and ``op-model-cpp`` targets reuse the language-neutral model
(``progseq_model``: walk + classify + the hoisted affine-offset and type-list
helpers) verbatim; only the per-language *emission* differs. The authoritative
notes are ``design/pss-c-op-model-cpp-gen-design.md`` and
``design/pss-c-op-model-cpp-gen-impl-plan.md``; the validated references are
``examples/export/programming_seqs/c_proto/`` and ``cpp_proto/``.

The one thing that varies between backends is the **seam** — how a register
access reaches the user's bus:

==============  ============================================================
Backend         Seam
==============  ============================================================
SV              ``pss_mem_if`` interface class + self-as-bus redirect
C ``vtable``    ``pssc_mem_if`` struct of function pointers + ``ctx``
C ``direct``    ``extern`` free functions linked by name
C ``mmio``      inlined ``*(volatile T*)addr`` load/store
C++             ``pssc::mem_if`` abstract base (user subclasses it)
==============  ============================================================

Because the seam is the only variable, **every C operation body is
byte-identical across the three link styles** — only the one-line ``pssc_bus()``
shim and the ``_create`` signature/``bus`` field differ. C and C++ bodies are
*simpler* than SV: native value-returning reads, native ``return`` values, and
native ``do...while`` (PSS ``repeat{}while``) — the three SV rewrites do not fire.

PSS construct → C / C++ artifact:

==============================  ==========================  =====================
PSS                             C                           C++
==============================  ==========================  =====================
``addr_handle_t``               ``pssc_addr_t``             ``pssc::addr_t``
``reg_c<T,ACC>``                baked inline accessor       ``pssc::reg<T,ACC>``
``struct : packed_s<>``         ``union{raw; struct{…}}``   same union header
``component : reg_group_c``     baked accessors             ``<group>_c`` class
regular ``component``           ``C_t`` + free fns          ``C_if`` + ``C``
``import`` fn                   extern / vtable ptr         pure virtual
``repeat{}while``, value read   native ``do...while``       native
==============================  ==========================  =====================

The Python backend
------------------

``op-model-py`` (``pssc.targets.py_progseq_tgt``, emitters in
``pssc.targets.py``) generates a Python module driving a structurally-typed
platform object — what a cocotb bring-up, a socket-attached debugger or a
pure-Python device model already has. Its seam is the ``imports`` object passed
to the constructor, and the module GENERATES the declaration of that seam:
``lower_import_api.py`` emits a ``@runtime_checkable`` ``Protocol`` listing
exactly what the bodies call plus every declared import, the same rule the C++
target's ``<ns>_import_if`` follows. Deriving the member list from the emitted
TEXT rather than from a second walk of the model is deliberate — a Protocol
computed independently can disagree with the accessors, and the failure mode of
that disagreement is a platform that implements the declared set and still gets
an ``AttributeError``.

Outside the standard library the module imports nothing unless the model has
channels, in which case it imports the bundled ``share/py/pssc_rt.py`` — or
``share/py/pssc_rt_async.py``, whose ``Chan1`` actually suspends.

``--py-await`` chooses the form. Three things it changes that are not local to
``targets/py/``: the target publishes ``HAVE_EVENT_WAIT`` accordingly
(``Target.resolved_target_cfg_for``), the Tier-2 legality set is re-published
per run so ``get``/``put`` are refused in one form and rendered in the other
(``PyProgSeqTarget._register_legality``), and the copied runtime gains a second
file. Everything else is inside ``targets/py/``.

It was written *after* the shared layer below existed, and is the evidence that
the layer is sufficient: everything except ``targets/py/`` is consumed rather
than reimplemented.

The shared layer
----------------

What used to be copied per backend, and now is not. A new emitter consumes these
rather than reimplementing them; see ``docs/custom-generator-styles.md``.

- ``pssc.targets.op_model`` — ``OpModel`` (the elaborated model: tree, component
  order, register groups, value structs, imports, ctor names) and
  ``OpModelTarget``, the family base class whose ``run`` is elaborate → check →
  emit → manifest. Every op-model target subclasses it, so the call-legality
  gate and ``--emit-manifest`` are common by construction.
- ``pssc.targets.body_walker`` — one walk of an operation body: statement and
  expression dispatch by node class name, comment attachment, and the two
  language-neutral scans (``scan_write_only``, ``scan_output_locals``).
  ``CallDispatch`` routes each call by its ``Disposition``, so the legality
  registry *is* the dispatch table.
- ``pssc.targets.reg_layout`` — the folded register layout
  (``collect_accessors``), shared by the C backend and by the manifest. An
  address is the one thing in a generated API a golden snapshot cannot check, so
  it is computed once.
- ``pssc.targets.call_legality`` / ``validate_calls`` — which calls a target may
  lower, and the pre-emission gate that refuses the rest with a location. See
  ``docs/lowering-call-legality.md``.
- ``pssc.targets.style`` (and ``targets/c/style.py``) — the naming and layout
  decisions, so a house convention is a policy rather than a fork.
- ``pssc.targets.sections`` / ``comments`` / ``overridable`` — file assembly,
  comment rendering, and the published override surface.
- ``pssc.targets.manifest`` — the elaborated model as JSON
  (``--emit-manifest``); see ``docs/op-model-manifest.md``.

Per-target modules
------------------

- ``pssc.targets.progseq_tgt`` — the ``ProgSeqTarget`` (``op-model-sv``) target and
  ``--root`` resolution.
- ``pssc.targets.progseq_model`` — language-neutral tree walk + classification
  (``func_kind``, ``comp_kind``, ``walk_tree``) and the shared affine-offset and
  type-collection helpers (``_array_base_stride``, ``collect_reg_groups``,
  ``collect_value_structs``).
- ``pssc.targets.progseq_gen`` — SV package assembly and file emission.
- ``pssc.targets.sv.lower_reg_model`` / ``sv.lower_progseq`` — SV emitters.
- ``pssc.targets.c_progseq_tgt`` / ``cpp_progseq_tgt`` — the C and C++ targets.
- ``pssc.targets.c.c_progseq_gen`` / ``cpp.cpp_progseq_gen`` — header/source
  assembly + core-seam copy.
- ``pssc.targets.c.lower_reg_model`` / ``c.lower_progseq`` — C value unions,
  baked accessors, handle/shim, export functions, and the body emitter.
- ``pssc.targets.cpp.lower_reg_model`` / ``cpp.lower_progseq`` — C++ value unions,
  ``reg<T>`` group classes, the pure-virtual APIs, and the component class.
- ``pssc.targets.py_progseq_tgt`` and ``pssc.targets.py.*`` — the Python target:
  value classes, accessors, API types, the module assembly and the body emitter.
