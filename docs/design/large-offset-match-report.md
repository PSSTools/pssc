# Large string `match` in register offset functions (2026-10-20)

## What failed

A register group whose `get_offset_of_instance` is one flat `match` over its
instance names (`["WORD_0"]: return 0; ["WORD_1"]: return 4; ...`) failed with
`RecursionError` on every op-model target at 30 names or more. At 500 names
it failed earlier in the pipeline, at the same root cause.

## Why

1. **`match_overlap` guarded distinct strings.** It knew only integer
   intervals, so two string choices counted as "may overlap". It then put an
   `error()` guard in front of the `match`, built from every pair of choices
   (N·(N−1)/2 conjunctions: 435 at 30 names, 124750 at 500), as an `||` chain
   one level deeper per pair. Every recursive walker after it ran out of stack:
   `_OffsetEval` at 30 names, `ExprTypes._collect_locals` (from
   `comp_foreach`) at 500. There was no cycle. The tree was finite but
   thousands of levels deep, and the compiler had built it.
2. **Offset evaluation was quadratic.** `_OffsetEval` runs the offset function
   once per instance and tried each case in turn. That is N² tests per group,
   repeated by five readers of the register walk. With fix 1 alone, 5000
   names took 14 s and 20000 took 187 s.

## Fix

- `match_overlap.overlapping_pairs`: two string literals overlap only when they
  are the same string, and two integer intervals only when they intersect. The
  constant pairs come from a dictionary and a sorted sweep, not from trying
  every pair. A choice known only at run time still pairs with every other
  choice. Repeated strings, intersecting ranges and run-time bounds are guarded
  as before.
- `body_walker.any_of`: the guard, a multi-value arm's test and an `in` list
  are each built as a balanced `||`, so 100000 terms are 17 levels deep.
- `_OffsetEval`: a `match` whose choices are all string literals (or
  `default`) is answered by one dictionary lookup. The index is built once per
  `match` and picks the same arm as the case-by-case walk (the first choice
  holding the name, or `default` if that comes first). An `else if` chain is
  walked in a loop instead of by recursion.
- `ExprTypes._collect_locals` enters statements only. Before, it followed
  every dataclass field, including expressions and type links into the rest
  of the model. It now keeps its own stack. `reg_rmw._walk_exprs` keeps its
  own stack too, in the same order as before.

## Result

| names | before | after, each of C / C++ / Python / SV |
|------:|--------|--------------------------------------|
| 30    | RecursionError | < 1 s |
| 500   | RecursionError | ~1 s |
| 5000  | RecursionError | ~3.6 s, 200 MB |
| 20000 | not run (fails from 30) | ~15 s, 620 MB |

The time now grows linearly.

`tests/progseq/test_large_offset_match.py` drives the result on all four
targets. It checks the first, middle and last register's address and width,
a register placed by `default`, two interleaved register arrays (bases 0x100
and 0x104, stride 0x10), the refusal of a name the function answers with −1,
a repeated string choice that is still a run-time error (Python and SV; C and
C++ have no strings, ruling R3), and generation of C and SV at 500 and 5000
names at the default recursion limit. On the old code, the address and scale
tests fail.

## Follow-up

An offset function written as an `if`/`else if` chain failed from about 500
names, for the same reason: the chain was one nesting level per name. It is
now one flat IR node; see `if-chain-plan.md`.
