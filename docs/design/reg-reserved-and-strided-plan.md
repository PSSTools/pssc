# Plan: `_<digit>` register members and strided register arrays

Status file for this change (2026-10-19). Baseline `40892c9`.

Two defects made a generated register-map HAL build fail on every target
while pssc exited 0:

1. A group member named `_0`, `_1`, ... (a generator's spelling of a numeric
   index) was treated as a reserved placeholder: no accessor, no SV/C++ class
   member, yet the body still referenced it.
2. A register array whose stride exceeds its width was declared `uintN_t X[n]`
   in the C layout struct, so every later member moved.

## Steps

| # | Step | Status |
|---|------|--------|
| 1 | One reserved predicate (`reg_layout.is_reserved`: `_` + non-digit), used by `collect_accessors`, `progseq_model._is_reserved`, `testing/conformance.py` | done |
| 2 | Shared-front refusal: an access through a reserved member is a located `CompileError` on every op-model target (`targets/reserved_access.py`) | done |
| 3 | C `_reg_call`: a register access with no `_Acc` is an internal error, no untyped fallback | done |
| 4 | C++: no `__` from joining a `_`-led member (`make_<name>` maker) | done |
| 5 | Strided register arrays: `is_strided_reg_array`, stride-multiple refusal, element struct `{ v; _rsvd[] }`, element-size assertion, `.v` access via `RegAccessor.elem_stride` | done |
| 6 | Tests: regarr model on all C seams (gcc c11/c99 -Werror + trace), Python, C++, SV (Verilator) | done |
| 7 | Release note; goldens unchanged | done |

## Not done here

- The full downstream example (regenerate from its `regs.pss`/sequence
  `.pss`, build with GCC, elaborate under VCS) lives in another project that
  is not checked out on this machine, and VCS is not installed. The SV
  acceptance here is Verilator, through the construct matrix (generate,
  build, elaborate and run, same trace as C/C++/Python).

## Tests

- `tests/progseq/test_c_reg_map_strided.py` -- every C seam, C99 and C11
  under -Werror, run against a trace or a buffer; element shapes; refusal.
- `tests/progseq/test_numbered_reg_members.py` -- the rule; manifest, C++,
  Python and SV surfaces.
- `matrix/test_m_registers.py` "numbered members and a strided register
  array" (one trace, four targets); `matrix/test_m_refusals.py` "an access to
  a reserved register".
