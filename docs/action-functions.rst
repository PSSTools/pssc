Action functions (pssc extension)
=================================

PSS 3.0 declares functions in packages and components only; an action has
exec blocks and nothing else. pssc also accepts **functions declared in an
action**. They give a test fixture the *template method*: a base action whose
exec body fixes the flow, and derived actions that override its steps. This
page says what they mean, where each target runs them, and what a model gives
up by using them.

.. warning::

   Action functions are not PSS. Other tools reject a model that uses them,
   and pssparser warns on each one (``LINT412``, below). If the model must
   stay portable, layer set-up with ``super;`` (see :doc:`writing-tests`), or
   move the function to the component and pass the action's fields as
   arguments.

The template method
-------------------

The base action's exec body calls ``arrange()``, ``act()`` and
``expected()``; each derived action supplies the steps it changes. This
suite runs under ``pssc test`` and both tests pass:

.. code-block:: pss

   import pssunit_pkg::*;
   component pss_top : fixture_c {
       rt_c rt;
       abstract action count_test_a : test_a {
           int seen;
           function void arrange() { seen = 10; }
           function void act();
           function int expected();
           exec body {
               arrange();
               act();
               comp.rt.check_eq_int(seen, expected(), "seen");
           }
       }
       action add_one : count_test_a {
           function void act() { seen = seen + 1; }
           function int expected() { return 11; }
       }
       action add_five_twice : count_test_a {
           function void arrange() { super.arrange(); seen = seen + 5; }
           function void act() { seen = seen + 5; }
           function int expected() { return 20; }
       }
   }

Semantics
---------

pssc gives action functions the meaning it already gives component functions:

- **Scope.** An action function sees the action's attributes, ``comp``, and
  its parameters, and may call the functions of the component tree. In a
  ``pssc test`` run it may also assign the action's attributes, as the exec
  body may.
- **Virtual.** A call from a base action's body reaches the most-derived
  override. pssc resolves this statically, once per concrete action: each
  derived action gets a copy of every function it inherits, so no target
  needs a dispatch table.
- **super.** ``super.f()`` in an override calls the base action's ``f``.
  It is an error when the base declares ``f`` with no body.
- **Abstract.** A function declared with no body (``function void act();``)
  is abstract. A concrete action that leaves one unimplemented is an error
  at its declaration, for example::

     line 7: action 'd_a' does not implement function 'act' (declared with no body in 'base_a' at line 4)

The warning
-----------

pssparser reports each function declared in an action as ``LINT412``
(``action-function``, tier *portability*), with the message ``function
'arrange' is declared in action 'base_a'; functions in an action are a pssc
extension``. It is a lint warning, never an error. Portability rules are not
in the default selection, and ``pssc`` itself does not print parser warnings,
so ask the pssparser linter for it:

.. code-block:: shell

   pssparser --select LINT412 model.pss

Where they run
--------------

.. list-table::
   :header-rows: 1

   * - Target
     - Action functions
   * - ``pssc test`` (bytecode)
     - Inlined into the caller, on the action's object. **Recursion is
       refused**: the test is an ERROR, ``recursion in an action's function``.
   * - ``op-model-py``, ``op-model-sv`` with ``--export-action``
     - Each becomes a private helper of the exported entry, named
       ``_pss_af_<entry>_<function>`` (a ``protected task`` in SV). Recursion
       works.
   * - ``op-model-c``, ``op-model-cpp``
     - These targets render no exported action at all, with or without
       functions.

An exported action takes no parameters, so nothing binds its attributes: an
action function, like the exec body, may not read one there (``action 'X'
reads its attribute 'v'; ...``). Use a local or a component attribute.

This model, exported as ``pss_top::d_a`` and ``pss_top::r_a`` on
``op-model-py`` or ``op-model-sv``, prints ``arrange``, ``more``, ``act 4``,
``twice 8`` and then ``fact 120``:

.. code-block:: pss

   import std_pkg::*;
   component pss_top {
     target function int add(int a, int b) { return a + b; }
     abstract action base_a {
       function void arrange() { message(NONE, "arrange"); }
       function void act();
       function int twice(int v) { return comp.add(v, v); }
       exec body {
         arrange();
         act();
         message(NONE, "twice %d", twice(4));
       }
     }
     action d_a : base_a {
       function void arrange() { super.arrange(); message(NONE, "more"); }
       function void act() { message(NONE, "act %d", twice(2)); }
     }
     action r_a {
       function int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
       exec body { message(NONE, "fact %d", fact(5)); }
     }
   }

.. code-block:: shell

   pssc compile -t op-model-py --export-action pss_top::d_a \
       --export-action pss_top::r_a -o out model.pss

``r_a`` would be an ERROR under ``pssc test``: keep action functions
non-recursive if the same model is also tested there.

A note for the PSS working group
--------------------------------

The proposal is small, and pssc implements it as written here:

- **Grammar.** ``action_body_item`` gains ``function_decl``, so a function
  definition or prototype may appear in an action body.
- **Scope.** Inside such a function, names resolve as in the action's exec
  blocks: the action's attributes, ``comp``, then the enclosing scopes.
- **Inheritance.** A derived action inherits its base's functions and may
  override one by declaring a function of the same name and signature. A call
  ``f()`` from any of the action's code calls the most-derived ``f`` of the
  action being executed; ``super.f()`` calls the base's.
- **Abstract functions.** A prototype with no body in an action makes ``f``
  abstract; a non-abstract action must implement every abstract function it
  inherits.
- **Unchanged.** Exec blocks, ``super;``, the rules on when attributes may be
  written, and component functions keep their current meaning. Because calls
  bind per concrete action type, an implementation can resolve them at
  compile time, as pssc does.

The use case is test fixtures: today the only way to share flow between
actions is ``super;``, which runs the base's whole exec body where it stands:
a derived action can add steps around the base's, but cannot replace one in
the middle.
