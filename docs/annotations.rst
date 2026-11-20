Annotations in the IR
=====================

PSS 3.1 §7.13 lets a user declare an ``annotation`` type and attach instances of
it to declarations. ``pssc`` translates every annotation it sees into the IR and
interprets none of them.

That is not a shortcut. §7.13 requires a tool that has never heard of an
annotation type to **ignore** it rather than reject it, and that requirement is
the whole portability story: a suite annotated for one tool must still elaborate
under another. So the front end has no list of annotation names it knows about,
and adding a new consumer needs no change to ``ast2ir``.

.. code-block:: text

   package pssunit_pkg {
       annotation test {
           string group   = "";
           bool   skip    = false;
           bool   xfail   = false;
           string reason  = "";
           int    samples = 1;
           string tags    = "";
       }
   }

   @pssunit_pkg::test{.group = "io", .xfail = true, .reason = "known wrong"}
   action dma_single_a : test_a { ... }

The node this page does not describe
------------------------------------

The IR node itself — `zuspec.ir.core.Annotation`, the ``annotations`` slot on
`Base`, `find_annotation`, and what a consumer owes when reading ``params`` —
is documented with the IR, in ``zuspec-ir-core``'s ``docs/annotations.rst``.
It belongs there because it is the contract every consumer of the IR shares,
not just this front end.

What follows is the half that is genuinely ``pssc``'s: how an annotation in PSS
source becomes one of those nodes, and what the first consumer does with it.

Translation rules
-----------------

**Declarations are recorded in the CONST pass.** ``ast2ir`` elaborates in three
passes (``CONST`` → ``DECLARE`` → ``EXTEND``), and ``AnnotationDecl`` is handled
in the first alongside package constants. §18.2 permits an annotation to be
applied in a file the parser saw *before* the one declaring it; recording
declarations in a later pass would make the folded result depend on the order
source files were listed in, which is the exact failure the pass split exists to
prevent.

**Defaults are folded at translation time.** An annotation's ``params`` starts
from its declaration's defaults, so a bare ``@pssunit_pkg::test`` and one that
spells out every attribute arrive at a consumer as the same map. This removes
the "absent versus unset" distinction from every downstream reader — otherwise
each consumer answers "the author didn't write ``.samples``" for itself, and two
consumers eventually answer it differently.

An attribute declared with **no** default is left out rather than given an
invented ``0`` or ``""``: a guessed value is indistinguishable downstream from
one the author wrote.

**Parameters are folded by the front end.** pssparser folds each value to a
constant and checks it against the field's declared type (PSS150), so
``.samples = 1 + 1`` arrives as ``2``. The runner reads the values through
``pssc.annotations``; a value that did not fold arrives as ``None``, and
``meta_of`` keeps the default for it rather than coercing it.

Note that two layers reject non-constant parameters and they do not reject the
same set. ``pssparser`` refuses ``{.samples = some_field}`` before translation
ever runs; ``{.samples = 1 + 1}`` reaches ``ast2ir`` and is caught there.

**An unknown annotation type is not a diagnostic.** It gets no defaults, no
error, and is carried through as written. This is §7.13, and it is the property
the cross-vendor portability story rests on.

**Application sites.** ``_apply_annotations`` runs at each declaration site that
builds an IR node — components, actions, structs — and fields pass
``annotations=`` to the `ir.Field` constructor directly. It *extends* rather
than assigns, so an ``extend`` of an already-declared type adds to what the
original declaration carried instead of replacing it.

Worked consumer: ``@pssunit_pkg::test``
---------------------------------------

``pssc test`` is the first consumer, and it lives entirely outside the front
end. ``pssc.testing.discover.meta_of`` reads the annotation off a discovered
action into a ``TestMeta``:

=========== ======= =========== =============================================
attribute   type    default     effect
=========== ======= =========== =============================================
``group``   string  ``""``      selectable with ``pssc test --group NAME``
``skip``    bool    ``false``   not run; reported ``SKIP``; ``--noskip`` runs it
``xfail``   bool    ``false``   a failure becomes ``XFAIL``, a pass ``XPASS``
``reason``  string  ``""``      shown on the skip/xfail line
``samples`` int     ``1``       carried; not yet consumed by the runner
``tags``    string  ``""``      carried; comma-separated; not yet consumed
=========== ======= =========== =============================================

``pssunit_pkg`` declares more annotations — ``property``, ``expect_error``,
``tag``, ``under_test``, ``platform``, ``field_access``, ``allow``, ``setup``
and ``teardown`` — each readable through ``pssc.annotations.of``. The runner
does not act on them yet; :doc:`writing-tests` lists them with the check and
platform-double imports.

The **runner**, not the report, settles the ``xfail`` verdict. Under
``--runxfail`` the annotation and the verdict disagree by design, so a report
that re-derived its status from the annotation would print ``xfail`` while the
process exited on the raw outcome — two answers to one question. By the time a
`Result` leaves ``run()`` its ``status`` is final, and the CTRF writer's status
map contains no logic.

The command-line surface for these — ``--group``, ``--noskip``, ``--runxfail``,
and what each status means in the report — is :doc:`test-command`.
