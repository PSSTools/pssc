# Effect sequencing: statement-form register reads

Status: implemented (PC-10): `ir_walk`, `effect_seq.sequence`,
`OpModelTarget.reg_read_form`, the C emitter's statement-form reads and the
HAL fixture's `DEV_READ32_INTO`. The last section is the contract a be-*
printer inherits.

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
- **Loop conditions** are re-read each iteration: `while (c)` becomes
  `while (1) { reads; if (!c) break; body }`, so a `continue` still re-reads.
  `repeat { body } while (c)` becomes `repeat { body; reads; if (!c) break; }
  while (1)`, refused when the body has a `continue` of that loop. A `while`
  with an `else` is refused. `if`, `match`, `for`/`foreach` and `repeat`
  heads are evaluated once and hoisted before the statement.
- **Refused, located:** a read in a `match` guard or an assignment target.
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

## Operators (`targets/ir_ops.py`)

`is_short_circuit`, `is_bitwise`, `is_comparison` and `swapped` take the
operator enums (`BinOp`, `BoolOp`, `CmpOp`, `AugOp`), not tables of names.
The canonical short-circuit form in pssc's IR is `ExprBin(op=BinOp.And |
BinOp.Or)`, which the front end writes. `ExprBool` can still arrive from
another producer, so the pass reads both through `short_circuit(e)` ->
`(BoolOp, operands)` and handles one shape; it builds an `ExprBool` only
internally, to split `a && b && c` at the first operand with a read.

## Placement

In `pssc.targets` (`effect_seq.py`), not zuspec-ir-core `xf/`: the pass,
the transformer and the operator predicates it needs live in pssc for now.
They can move to ir-core (ZIR-3, ZIR-4) without changing the contract.

## Contract to carry into be-* printing

When statement printing moves from pssc's emitters to zuspec-be-sw, the
printer takes over spelling, not sequencing. The HAL plugins written
against today's hooks must keep working unchanged. What a be-* printer may
rely on, and what it must keep:

**What reaches the printer.** `OpModelTarget.sequence_reads` runs at the
end of `build_model`, before `body_kinds`, `call_legality` and
`state_uses`, and records the form on the model (`OpModel.reg_read_form`).
Under `"statement"` every register read (`read`, `read_val`, `read_field`
on a `self`-rooted path) is, in the body the printer gets, exactly one of

- the whole `value` of a `StmtAnnAssign` declaring a local,
- the whole `value` of a `StmtAssign` to a local, or
- the whole expression of a `StmtExpr`;

and no read is anywhere else: not in a condition, an operand, a call
argument or a `return`. Order, short circuit, `else if` placement and loop
re-reads are already decided (see "The pass"); the printer prints the
statements in list order and must not reorder, merge or drop a read.
Bodies that could not be sequenced never reach the printer: they were
refused, located, before emission. Under `"expression"` (the default) the
body is the model's as written, and the printer is asked for reads in
expression position.

**Temporaries.** A local the pass introduced is declared by a
`StmtAnnAssign` carrying `getattr(decl, effect_seq.TEMP, False) == True`.
Its name is fresh in the function; its type is the read's (the register's
value struct for `read()` of a struct-valued register, else unsigned at the
register's width; `effect_seq.BOOL` for a split `&&`/`||`). It is set by
the pass alone (at its declaration, or in the arms of the `if` that
replaced a short circuit) and only read after. So a
printer may hold a `TEMP` local in another form: the C emitter holds a
value-struct `read()` temporary as the register word (`_read_word`) when
the target spells `read()` and `read_val()` alike, and reads its fields
through `reg_value_field_get`. A local without `TEMP` is the model's and
keeps its declared type.

**The plugin surface.** These hooks stay the way a target spells register
accesses, with today's arguments (`ra`: the `reg_layout.RegAccess`; `lw`:
the access lowered for the language) and today's meaning:

| Hook | Asked for |
|---|---|
| `reg_access(ra, lw)` | an access as an expression; under `"statement"` never a read |
| `reg_read_into(ra, lw, local)` | each read under `"statement"`, and the first step of a composed masked write |
| `reg_write_from(ra, lw, local)` | the last step of a composed masked write; `r.write(v)` of a value-struct local held as the word |
| `reg_value_field_get(value, fs, ra, lw)` | a field of a value read whole, or of a local holding it |
| `reg_value_field_set(local, fs, value, ra, lw, mask)` | each named field of a composed masked write |
| `reg_value_local(ra, lw)`, `reg_value_local_scope(ra, lw)` | the word local's type and name, and where it is declared |

A be-* printer calls these hooks (or forwards to a target that defines
them); it does not add a second way to spell a register access. Anything
it needs beyond them is a new hook, published with `@overridable` and a
row in docs/extension-stability.md.

**`reg_read_form` semantics.** It is a property of the target class
(`"expression"` or `"statement"`; anything else is an error naming the
target). It changes where reads sit, never which reads happen or their
order relative to each other and to other calls: a body that would need a
read moved past another call is refused rather than reordered. With
`"expression"` nothing is rewritten, so output is byte-identical to a
target that never heard of the pass.
