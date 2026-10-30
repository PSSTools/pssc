# Effect sequencing: statement-form register reads

Status: design (PC-10). Building blocks landed: `ir_walk.walk`,
`ir_walk.rebuild`, `ir_walk.Transformer`. The pass itself
(`targets/effect_seq.py`), the target flag and the fixture change follow.

## Problem

A target whose platform reads a register through a *statement* (a macro
that writes its result into a variable, an awaited call in Python) cannot
print a read in expression position: `if (rd(A) && rd(B))`, a read in a
loop condition, in a call argument, in a `return`. Today each such target
reorders the body itself, in its emitter (`op-model-py` `_awaited`) or by
overriding unpublished `stmt_*` hooks. Where reads go and in what order
decides the bus trace, so that is model semantics: pssc decides it, the
plugin spells it.

## The pass

A language-neutral pass over a function body, run before rendering. Given a
predicate over calls, it rewrites the body so that every matching call is
either

- a whole statement, or
- the whole right-hand side of a declaration or assignment to a **fresh
  local**.

The default predicate is register accesses by `call_legality.REG_DIRECTION`;
a target may widen it (model calls, imports). Matching calls are hoisted in
source order, keeping C/PSS semantics:

- **Short circuit.** The right operand of `&&` / `||` and the arms of `?:`
  become nested `if`s; they are never hoisted unconditionally.
- **`if` / `else if` chains.** A later clause's test is hoisted under the
  preceding `else` (the `StmtIfChain` rule in AGENTS.md).
- **Loop conditions** are bottom-tested: evaluated once before the loop and
  again at the end of each iteration (as `op-model-py` does for awaited
  calls). `repeat … while` tests only at the bottom.
- **Ordering around other calls.** A read is never moved ahead of a call
  that is evaluated before it in the same expression: the pass refuses,
  with the call's location, unless the predicate is widened to hoist that
  call too.
- **Temporary types.** Each temporary has the register's unsigned width;
  everything after hoisting goes through `IntSemantics` as usual.

With the default target settings the pass does not run, and the goldens stay
byte-identical. A target opts in with `OpModelTarget.reg_read_form =
"statement"`; then each read reaches the emitter only as `local = <read>`
and is spelled by the existing `reg_read_into(ra, lw, local)` hook, and
`reg_access` is never asked for a read in expression position. The pass
runs before `body_kinds`, `call_legality` and `state_uses`, so those see
the body that is printed.

## Building blocks (`targets/ir_walk.py`)

- `walk(node)`: pre-order, field order (source order), through `children`
  only, so it never enters `loc`, `provenance` or `annotations`.
- `rebuild(node, **children)`: a copy with child fields replaced, every
  other field carried; identity when nothing changed. Replacing a non-child
  field is an error.
- `Transformer`: bottom-up rebuild in the manner of `ast.NodeTransformer`.
  A visitor of a node held in a list may return a list to splice (how a
  hoisted read becomes statements before its user). The input is not
  changed; untouched subtrees are shared.

## Placement

In `pssc.targets` (`effect_seq.py`), not zuspec-ir-core `xf/`: the pass,
the transformer and the operator predicates it needs live in pssc for now.
They can move to ir-core (ZIR-3, ZIR-4) without changing the contract.

## Contract to carry into be-* printing

When statement printing moves to be-*: reads reach the printer already
sequenced, and the register hooks (`reg_read_into`, `reg_write_from`,
`reg_value_field_get/set`, `reg_value_local`) stay the plugin surface.
