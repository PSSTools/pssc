``pssc test`` internals
=======================

How the runner is built, for two readers: a contributor changing it, and whoever
writes the second ``pssunit`` runner. :doc:`test-command` is the same machinery
from the outside.

.. warning::

   **``pssc.testing`` is two things.** The package's own ``__init__`` is the
   *plugin-author kit* — ``compile_op_model``, ``assert_common_tier``,
   ``assert_deterministic`` — for people writing pssc **target** plugins. It has
   nothing to do with running PSS tests.

   The pssunit runner is the set of submodules below, and none of them is
   re-exported from ``pssc.testing``. Import them by module::

      from pssc.testing.sources import resolve_sources
      from pssc.testing.discover import discover
      from pssc.testing.runner import run
      from pssc.testing.report import write_ctrf

   ``from pssc.testing import run`` does not work, and the name collision is
   accidental rather than designed.

The pipeline
------------

Six separable stages, in one place — ``pssc.testing.cmd`` is the only module
that knows the order:

.. code-block:: text

   src-spec -> resolve -> translate -> discover -> plan -> execute -> report

============= ======================= =========================================
stage         module                  produces
============= ======================= =========================================
resolve       ``testing.sources``     an ordered list of ``.pss`` paths
translate     ``pssc.driver``         an IR ``Context``
discover      ``testing.catalog``     ``list[TestItem]``
plan          ``testing.cmd``         the filtered list, and its roots
execute       ``testing.runner``      ``list[Result]``
report        ``testing.report``      stdout, and the CTRF document
============= ======================= =========================================

The split is what keeps *"did discovery find it?"* answerable separately from
*"did it run?"* — the two questions a test runner gets wrong in different ways,
and the reason ``--collect-only`` stops after ``plan`` rather than being a flag
threaded through execution.

Each stage is importable and testable without the ones after it. A second runner
can reuse ``resolve``, ``translate`` and ``discover`` unchanged and replace only
``execute``, which is roughly what porting to another backend means.

Resolve
-------

``resolve_sources(specs) -> list[Path]``, and ``all_sources(specs)`` which puts
the shipped ``pssunit_pkg.pss`` in front of the user's files so a suite names no
framework file and installs nothing.

Three contracts, covered in :doc:`test-command`: stable order, duplicates
dropped, a spec matching nothing is a ``SourceSpecError`` rather than an empty
contribution. Order is contract rather than convenience — ``pssc`` has been
bitten by file order before, and there is a regression test for it.

Discover
--------

The whole rule:

   A test is a **non-abstract action type** whose ``super`` chain reaches
   ``pssunit_pkg::test_a``.

``TEST_BASES`` is a tuple, not a string, and already names
``pssunit_pkg::property_test_a`` — which is not declared yet. Naming it costs
nothing and records that the rule was never meant to be single-valued.

Two facts about the IR make this less obvious than it reads, and both were
verified against real output rather than inferred from class names:

**PSS actions are ``ir.DataTypeClass``, not ``ir.DataTypeAction``.**
``DataTypeAction`` belongs to the ``@zdc`` authoring path. What marks an action
in the PSS type map is its presence in ``ctx.parent_comp_names`` — the same
table that answers "which component declares it", so one lookup does both jobs
and ``TestItem.comp`` falls out of it.

**``super`` is an unresolved ``ir.DataTypeRef``.** Walking the chain means
re-resolving ``ref_name`` through ``type_map`` at every hop, and ``ref_name``
arrives as written in the source — so it may be unqualified.
``resolve_type_name`` does a suffix match, and the walk is cycle-guarded: ``a :
b`` with ``b : a`` is a bug upstream, but it must not be one that hangs the
runner.

Why this stage shouts
~~~~~~~~~~~~~~~~~~~~~

An unresolvable or ambiguous name **raises**; it is never a quiet "not a test".

This is the one stage where failing soft produces a green summary for a suite
that never ran. A typo in a base type would delete a test from the run and
report success — and an ambiguous reference resolved by picking the first
candidate would decide which of two types a test derives from by dictionary
order. Both raise ``DiscoveryError`` and name what they could not resolve.

The same instinct governs ``meta_of``: an annotation attribute of the wrong type
is **ignored, not coerced**. ``skip = 1`` would coerce to ``True`` and silently
delete a test from the run. Nothing can currently produce that — an annotation
parameter's type is fixed by its declaration and the front end rejects a
non-constant value — and the ``isinstance`` guards are what keep it true if
either of those changes.

.. note::

   In Python ``bool`` subclasses ``int``, so the ``samples`` guard has to
   exclude ``bool`` explicitly or ``skip = true`` would satisfy an ``int``
   check. See ``zuspec-ir-core``'s annotations page, which owns this trap.

Order is declaration order, not sorted: it is the order the user wrote, which is
the order they will look for a failure in.

The catalog
~~~~~~~~~~~

``testing.discover.discover`` delegates to ``testing.catalog.build``, which
adds what the rule above leaves out:

* **Roots and inheritance.** Every concrete component with a test, its own or
  inherited, is a root. A component derived from another runs the base's
  tests under its own ID, ``<root>::<test>``; ``TestItem.decl`` keeps the
  declaring action, which is what the runner looks up (``type_name``).
  ``is_fixture`` says whether the root derives from ``pssunit_pkg::fixture_c``.
* **Test functions.** A component function marked ``@pssunit_pkg::test``
  (``kind = "function"``).
* **Spans.** The IR records only where a declaration starts; ``span_end``
  finds the closing brace in the source, so ``FILE:LINE`` can select by body.
* **Base types are located.** ``base_chain`` prefixes an unresolvable link's
  error with ``file:line:col`` of the type that names it.

``to_json`` renders the ``pssunit.catalog/1`` document
(``docs/schemas/pssunit.catalog-1.json``). Its ``calls`` member is the test's
slice (``slice.py``): ``units``, the actions and functions it reaches by name,
and ``slice``, the hash ``--changed`` compares.

The data
--------

Four small frozen-ish dataclasses carry everything between stages.

``TestMeta``
   The ``@pssunit_pkg::test`` attributes: ``group``, ``skip``, ``xfail``,
   ``reason``, ``samples``, ``tags``. Every field carries the annotation's own default, so
   an un-annotated test and a bare ``@pssunit_pkg::test`` are indistinguishable
   downstream. The defaults are folded by the **front end**, so ``meta_of`` does
   not need to know them and there is exactly one place — ``pssunit_pkg.pss`` —
   where a default is written down.

``TestItem``
   ``qname``, ``name``, ``comp``, ``loc``, ``meta``, and the catalog's
   ``kind``, ``decl``, ``end_line``, ``tags``, ``property_runs``,
   ``expect_error``, ``platform_min``, ``bases``, ``is_fixture``. ``comp`` is
   the root component the test runs under: the component declaring it, or a
   component inheriting it. ``loc`` is the declaration site, carried for the
   CTRF ``filePath``/``line`` fields, and ``None`` when the front end recorded
   none.

``Result``
   One test's outcome: ``status``, ``kind``, ``elapsed``, ``seed``, ``detail``,
   ``trace``, ``log``, and the three check counts. **Both reports render from a
   list of these**, neither parsed out of the other's text, so the human summary
   and the CTRF document cannot drift into disagreeing.

``Status`` / ``Kind``
   ``Status`` is the six-way verdict (see :doc:`ctrf-report` for the collapse to
   CTRF's five). ``Kind`` is *why* — ``UNSAT``, ``CHECK``, ``NO_CHECKS``,
   ``UNSUPPORTED``, ``UNBOUND_IMPORT``, ``EXCEPTION``, ``DIVERGED`` (``--platform
   replay:``) — kept separate because
   failure formatting wants the reason and the verdict does not.

``Result.ok`` is ``status in (PASS, SKIP, XFAIL)``, and it is what the exit code
is computed from.

Execute
-------

The path is **ZBC** (``zuspec-be-bc``), not the live-Python builder. Constraints
lower to a relocatable dv-solve problem and are solved by dv-solve itself, so
the values a test sees come from the same solver — and the same bytes — the
native engine will use. ``print`` and ``message`` are implemented inside ZBC, so
output does not depend on the host supplying anything either. There is no
backend to select and nothing to pin.

Work is amortized along the grain of the model:

- one ``translate`` per run;
- one PSS→Scenario lowering and one ZBC image **per root component**, cached, so
  a suite whose tests share a component lowers once;
- one fresh object and one fresh frame tree **per test**.

A test is run from an **entry** of the image (``ZbcModel.entries``): an action
test from its action's coroutine, a ``@pssunit_pkg::test`` function from the
function's own code unit, called in the root component instance. The runner
asks ``lower_module`` for an entry for each test function of the component
(``entry_functions=["<comp type>::<fn>", ...]``). The function is looked up from
the root's type, so a test function a base fixture declares calls the derived
fixture's overrides.

The image is lowered **for debugging** by default (``bc_session.lower(...,
debug=True)``; ``pssc test --no-debug`` lowers the release image). A debug image
marks where each PSS statement starts (the ``LINE`` table), keeps calls to
functions with scalar parameters and result as calls -- each with a named frame
-- rather than inlining them, and names the locals in each frame (the ``LOCAL``
section). It runs the same test to the same verdict; it is larger and slower,
which a test run can afford and a stepping debugger cannot do without.

An entry's code is relative to the instance its frame runs in, so one entry
serves every instance of its type. ``pssc.testing.bc_session.Session`` uses
this to hold one constructed component tree and call functions on any instance
of it, one call after another, each seeing the state the last one left::

    model = bc_session.lower(ctx, "dma_f", functions=["*"])  # every function
    s = bc_session.Session.load(model)
    root = s.construct()                      # runs $comp_init
    s.call(root, "ch[0].bump", 2)             # -> 2
    s.call(root, "ch[0].bump", 3)             # -> 5: state persists
    s.run_test("dma_f::t_sum", seed=1)        # fresh tree; (n_pass, n_fail, n_skip)

Isolation is therefore a property of the architecture rather than something the
runner maintains. A run builds its own frames from the image, and the image is
immutable, so there is no state between tests to reset. Nothing accumulates
across iterations and nothing writes to a buffer another test can see — which
costs nothing today and is the difference between parallelism later being a
change and being a rewrite. The MVP runs sequentially so failure output stays
ordered.

An image can also be **written to a file and run from it**:
``pssc build SRC --image out/x.zbc`` writes one root's debug image, and
``pssc test --image out/x.zbc [IDS] [-k ...]`` lists (``--collect-only``) and
runs its tests with no sources and no lowering. The file is a ``.zbc`` (the
container format of zuspec-be-bc's spec ``zbc-format``) whose ``META`` section carries pssc's documents:

- ``pssc.catalog``: the root's tests (``pssunit.catalog/1``);
- ``pssc.image``: the root, its set-up / tear-down entries, the API hash and
  generation (``pssc.image/1``, also ``pssc build --json``);
- ``pssc.registers``: the register map and field semantics;
- ``pssc.pyvm``: what pyvm needs and be-bc has no encoding for yet (solve
  problems, select tables, object layouts, ...), pickled and refused under
  another be-bc ABI ("rebuild the image"). It is interim: a specified
  encoding replaces it when the native engine loads images (plan P4.3).

``build_cmd.load(path)`` gives ``(image, items, meta)``; ``pssc test --image``
passes the image to ``runner.run(images=...)``. What needs the sources or the
IR is refused with exit 2: ``--changed``/``--base``, ``--watch``, ``--cov``,
``--targets`` and source positionals. A replay line carries ``--image``.

Output is **captured, not redirected**: ZBC formats each line and hands it to a
callback ``pssc.testing.bc_session.run`` collects, so nothing of the test's ever reaches the real stdout, and the
capture keeps working under parallel execution.

Two decisions worth knowing
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**A lowering failure is captured, not raised.** It is scoped to a *component*,
and every test under that component has to report it. One unsupported construct
must not abort the run and hide the tests that would have passed — so
``_build_image`` stores the exception and each test under that component fails
with it.

**Every test gets the run's seed**, not a derived per-test seed. That is what
makes ``pssc test -k NAME --seed N`` reproduce exactly what the test did inside
the suite, which is the property the replay line depends on.

The verdict, in order
~~~~~~~~~~~~~~~~~~~~~

1. ``.skip`` and not ``--noskip`` → ``SKIP``, **without touching the image**.
   Skipping a test whose component would not even lower has to work: a skip mark
   is often exactly what stands in for "this does not build yet".
2. The test runs. An exception is classified — a solve failure is ``FAIL``/
   ``UNSAT`` (the model said something untrue), a lowering failure is ``ERROR``/
   ``UNSUPPORTED``, anything else is ``ERROR``/``EXCEPTION``. Reporting a tool
   limitation as a ``FAIL`` would blame the design and send the reader looking
   for a constraint bug that is not there.
3. The ledger is read. A failed check makes it ``FAIL`` even though nothing
   raised — which is the whole point of the ledger, since "did not raise" cannot
   express "produced the wrong value". Under ``--strict``, zero checks is also a
   ``FAIL``.
4. ``_settle_xfail`` rewrites ``PASS`` → ``XPASS`` and anything else → ``XFAIL``,
   unless ``--runxfail``. **This happens in the runner, not at report time**, so
   the human summary, the CTRF document and the exit code all read one settled
   verdict instead of each re-deriving it from ``meta``. Two consumers deriving
   the same thing separately is how a runner reports one outcome and exits with
   another.

The ledger
~~~~~~~~~~

Check counts are read out of the elaborated tree, from **every**
``pssunit_pkg::rt_c`` instance, summed. Not "the" instance: a fixture hierarchy
may legitimately hold more than one, and picking an arbitrary single one would
silently ignore the others' checks. An instance never touched contributes
nothing.

Extension seams
---------------

Where to change things, roughly in order of how often you will want to:

``TEST_BASES``
   Add a base type and a new family of tests is discovered. No other stage
   changes.

``TEST_ANNOTATION`` / ``TestMeta``
   New metadata. Add the field to ``pssunit_pkg.pss`` (where the default lives)
   and to ``TestMeta``; ``meta_of`` reads it generically.

``Kind``
   A new distinguishable failure mode, for failure formatting. Adding one does
   not touch ``Status``.

``filter_items``
   New selection flags. Pure function over ``list[TestItem]``, so it is testable
   without a model.

``report``
   A new output format. Write another function over ``list[Result]`` — do not
   derive it from the human text.

``run_one``
   A different execution backend. This is the function a second runner replaces,
   and everything above it is reusable.

What is deliberately absent
---------------------------

- **ivpm / dv-flow integration, incremental caching, per-file include paths.** A
  directory walk covers every early user, and the smaller ``sources``' contract
  is, the longer it stays changeable.
- **A test registry generated into PSS.** Not needed: the runner invokes
  discovered entries directly.
- **Parallel execution.** The architecture is ready for it; the MVP is
  sequential so failure output stays ordered.
- **``samples``.** Carried through to ``TestMeta``, not yet consumed by the
  runner.

See also
--------

:doc:`test-command`
   The same machinery from the outside.
:doc:`ctrf-report`
   The result format, and the conformance surface it defines.
:doc:`annotations`
   How ``@pssunit_pkg::test`` reaches ``meta_of``.
