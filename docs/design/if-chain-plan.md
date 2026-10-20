# `else if` chains as one IR node (plan, status and report, 2026-10-20)

Follow-up to `large-offset-match-report.md`. That fixed a register offset
function written as one flat `match`. This does the same for one written as
an `if` / `else if` chain, and for any operation body with a long chain.

## What failed

| Where | Effect |
|---|---|
| ast2ir | pssparser keeps a chain as one node with its clauses in a list; ast2ir nested them again, an `ir.StmtIf` in each `else`. N links were N levels deep. |
| `array_methods._rewrite`, the first op-model pass | `RecursionError` from about 500 links, on every target |
| op-model-py output | `else:` + `if` per link: from about 100 links the module did not load (`IndentationError: too many levels of indentation`) |
| op-model-c/cpp/sv output | `else { if ... }` per link: indentation, and file size, grew quadratically |
| `_OffsetEval` | answered each name by walking the chain: quadratic over a group |
| pssparser | the grammar rule was the LRM's `if (e) stmt (else stmt)?`: one level of parse tree and native stack per link. Parse time was quadratic (0.2 s at 1000 links, 4.7 s at 5000), and at 10000 the process died with a segmentation fault. |

## Decision

A new ir-core node, a flat chain (`zuspec.ir.core.stmt`):

```python
class StmtIfClause(Base):   test: Expr; body: List[Stmt]
class StmtIfChain(Stmt):    clauses: List[StmtIfClause]; orelse: List[Stmt]
```

* ast2ir produces it for an `if` with at least one `else if`. A plain
  `if` / `if-else` stays `StmtIf`, so its output and bytecode do not change
  (no golden snapshot moved).
* It is a new kind, not a new field on `StmtIf`. A consumer that has not
  learned it fails by name (the body walker's missing hook, the kind
  registries). A field it did not know would have been dropped silently,
  and the branches with it.
* The clause is named `Stmt...` so the generic walkers that follow every
  list of `Stmt*` reach its body with no change.
* Every renderer prints it flat: `else if` (C, C++, SV), `elif` (Python).
  Python (async) and SV hoist calls out of a condition. A later clause's
  test runs only when every earlier one was false, so a call it hoists
  cannot go in front of the chain. That clause is rendered as `else`, then
  the hoisted statements, then the rest of the chain one level deeper.
* `_OffsetEval` indexes a chain whose tests are all `name == "literal"`
  (either way round, or an `||` of those) as it indexes a `match`: the first
  clause holding the name wins, and anything else falls back to the walk.
* pssparser: the links are a loop of the rule
  (`procedural_else_if_clause*`). A left-edge predicate keeps the final
  `else` from starting with `if`. Without it, both readings of `else if`
  stay viable to the end of the chain, and prediction scanned that far at
  every link (a first attempt without it did not finish 1000 links in ten
  minutes). An `else` still belongs to the nearest `if`.

## Changes

| Repo | Files |
|---|---|
| zuspec-ir-core | `stmt.py`, `__init__.py`: the two classes |
| pssparser | `PSSParser.g4` (the rule), `AstBuilderInt.cpp` (the builder reads the links); probes in `tests/python/parsing/test_ast_node_probes.py` |
| zuspec-be-bc | `lower/procedural.py`: a run of tests, each taken body jumping past the rest |
| pssc | `ast2ir.py`; `progseq_model.py` (`_OffsetEval` walk and index); `c/`, `cpp/`, `py/`, `sv/lower_progseq.py`; `body_kinds.py`; `sw_lower.py`, `sw_tgt.py`, `sv/lower_stmts.py`, `sv/lower_pure.py`, `sv/lower_api_types.py`, `array_literal.py` (walkers that name fields) |

## Results

Offset function as an `else if` chain (C target; SV is the same):

| links | before | after |
|------:|--------|-------|
| 500   | RecursionError | 0.5 s |
| 5000  | RecursionError | 4.0 s, 235 MB |
| 20000 | segmentation fault in the parser | 16 s, 755 MB |

Parse alone, operation body of N links: 0.05 s at 1000, 0.15 s at 5000,
0.56 s at 20000 (was 0.2 s, 4.7 s, crash). Total time is linear and matches
the `match` form (3.6 s at 5000, 15 s at 20000).

## Tests

* `tests/progseq/test_large_if_chain.py`, on C, C++, Python and SV:
  * the first, middle and last address of a 500-link offset chain, and a
    register placed by the final `else`;
  * a 200-link chain in an operation body (twice Python's nesting limit
    under the old form), with a repeated condition where the first wins;
  * a register read in the second clause's test is made only when that
    clause is reached (SV hoists it; async Python is checked separately);
  * `else` binds to the nearest `if`;
  * the name index (first clause wins, either operand order, `||`, and the
    tests it must not index);
  * a 5000-link bank generating on C and SV at the default recursion limit;
  * a 20000-link chain parsing and translating, in a child process.
* `tests/unit/integration/test_bc_if_chain.py`: the same semantics on bc
  (600 links).
* `tests/unit/test_ast_to_ir.py`, `tests/unit/ir/test_ir_statements.py`: the
  IR shape (`else { if }` with braces stays a body).
* Kind registries: `StmtIfChain` rendered, `StmtIfClause` part of it.

Suites: pssc unit 2527 passed; progseq 2967 + 14 new; compliance 925; sim
33 (24 skipped, Questa); ir-core 101; be-bc 250; pssparser 9160 + 4 new.

## Status

Done and pushed 2026-10-20: pssparser f6389e6 (tagged v3.4.0),
zuspec-ir-core 17953ab, zuspec-be-bc 0471680, and pssc in the commit that
adds this note. pssc needs an ir-core release with `StmtIfChain` before its
own next release (its floor is still `zuspec-ir-core>=0.2.0`).
