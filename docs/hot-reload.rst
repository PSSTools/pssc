Hot reload
==========

A debug session can take an edited model without starting again: the
session's sources are lowered once more and the new image becomes the
session's next **generation**. The component tree keeps its state, so a test
that failed half way can be fixed and its next call runs the new code.

.. code-block:: text

   $ pssc dbg start @model.f . wb_dma_fx_c::t_routing_mask --platform fake
   session 1: stopped (entry)
   $ # ... edit a .pss file of the model ...
   $ pssc dbg reload
   session 1: generation 1 -> 2 (staged until the run ends)

In Python, ``bc_session.Session.reload(model)`` does the same with a model
you lowered yourself. A simulation in server mode takes a rebuilt image with
``pssc sim reload`` (:doc:`simulation`, *Hot reload*).

What reloads
------------

- **The code.** Every function, action and exec block of the new image.
  Calls already running finish on the code they started with; the next call
  runs the new code.
- **The state.** The tree is built again under the new image and each
  component's fields are copied from the old one, matched by **instance
  path** (``dut.dma.ch0``): a component whose path, type and field names are
  unchanged keeps its values, channel tokens included. A component that was
  added starts fresh; one that was removed is dropped.
- **The platform doubles.** The ``--platform`` module is imported again (its
  stale ``.pyc`` dropped) and its doubles apply at once, also to a run that
  is still going.
- **Host bindings** (``Session.bind``) carry over to the new generation.

Staged and applied
------------------

A reload applies at once when no call is running. During a call (a run that
has not ended, under ``pssc dbg``) it is **staged**: ``applied`` is false
and the run ends on the generation it started with; the new one applies when
the call returns. ``--cancel`` ends the running call instead and applies at
once.

Each applied reload raises a ``reload`` trace event (old and new generation,
the migrated paths, the structural changes).

Refusals
--------

A refused reload leaves the session as it was. Exit status 1; with
``--json``, ``{"error": {"code", "reason", "message", "changes"}}``.

``api``
   A test's, a function's or an import's signature changed: the host could
   no longer call it. Restart the session.
``bus``
   Building the tree under the new image would touch the device (a memory
   access or a user import in a constructor or ``init`` block). The device
   is not programmed behind the test's back.
``structure``
   Only with ``--strict``: a component, channel or sub-component was added
   or removed, or changed type. Without ``--strict`` the change is reported
   (``structure``: ``added``, ``removed``, ``changed``) and the reload goes
   on.

From the debugger
-----------------

A DAP client (:doc:`debugging`) reloads with the custom request ``pssc/reload``
(arguments ``strict`` and ``cancel``, as the options). It reloads the
client's own session and answers with the same object as
``pssc dbg reload --json``; an ``output`` event says whether the change was
applied or staged, and an applied one is followed by an ``invalidated``
event. A refusal is a failed response whose ``body.error`` is the object
above. The ``initialize`` reply lists the request under
``pssc.requests``. An editor binds it to its *Apply change* command.

Only ``pssc dbg`` sessions (pyvm) take it for now: a simulation is reloaded
with ``pssc sim reload`` until the debugger can attach to one.

Coverage across generations
---------------------------

Coverage points are keyed by scope and block path (``top_c::step/0``), not
by the image's program counters, so the hits before and after a reload
count on the same points. A point takes the new generation's file and line;
a statement the edit added is a new point with its own hits. See
:doc:`coverage`.

The engine side (checks, bus-isolated construction, migration) is specified
in be-bc's spec, ``packages/zuspec-be-bc/docs/spec/generations.md``.
