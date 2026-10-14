# pssc: instance-array offsets written as per-index `if` arms cannot be evaluated

`pssc` cannot compile an operation model whose register package contains an
**instance array** with an offset function that has one `if` arm per index.
`rdl-gen-pss` generates exactly that shape for any register block instantiated
as `<name>_0`, `<name>_1`, ... in the register model.

`pssc` evaluates offset functions with the index kept symbolic, as
`c0 + index*c1`, so it refuses any condition that tests the index. The input
is valid PSS: `pssparser` accepts it, and LRM 21.14.1 leaves the body of
`get_offset_of_instance_array` to the user.

## Versions

| Tool | Version | Result |
|---|---|---|
| pssc, workarea | packages/pssc `41f617d` | error, exit 1 |
| pssc, release | internal release 1.1.0 | internal error + traceback, exit 2 |
| rdl-gen-pss | packages/rdl-pss-generator `ebc3d48` | produces the input below |
| pssparser | workarea | accepts the input (0 errors) |

## Reproduce

`repro.pss` (in this directory) is a minimal, self-contained version: one
register block, instantiated twice, with the array function written the way
`rdl-gen-pss` writes it:

```pss
pure component top_c : reg_group_c {
    blk_c blk[2];
    pure function bit[64] get_offset_of_instance_array(string name, int index) {
        if (name == "blk" && index == 0) {
            return 0x100;
        } else if (name == "blk" && index == 1) {
            return 0x200;
        }
        return 0xFFFF_FFFF_FFFF_FFFF;
    }
}
```

The root exports its operation (`export target function go;`). `op-model-sv`
refuses a model that exports nothing, so without the export the control case
below would fail on that target for an unrelated reason.

```sh
pssc compile repro.pss -t op-model-c --root dev_c -o gen \
     --link-style direct --mem-access functions
```

Workarea `pssc`, same on `op-model-c`, `-cpp`, `-py` and `-sv`:

```
pssc: error: 1 register offset(s) cannot be evaluated
  dev_c: 'top_c.get_offset_of_instance_array' uses a condition on the array index, which cannot be evaluated at build time; an offset function may use if, match and return over the instance name and constants
```

Released `pssc` 1.1.0, where `OffsetFoldError` escapes as an internal error:

```
pssc.targets.progseq_model.OffsetFoldError: 'top_c.get_offset_of_instance_array' is not a match over the instance name, so its offsets cannot be evaluated at build time
pssc: internal error (see traceback above)
```

`affine.pss` is the same model with the array arm rewritten as
`if (name == "blk") { return 0x100 + index*0x100; }`. The workarea `pssc`
compiles it on all four targets and folds the address correctly, e.g. in C:

```c
return _self->base_regs + 0x100u + (pssc_addr_t)i0 * 0x100u;
```

So the layout is fine. Only the *form* of the function is the problem.

## Where it happens

`src/pssc/targets/progseq_model.py`:

- `array_base_stride(group_dtype, field_name)` runs `_OffsetEval` for the
  name and expects one affine result `(base, stride)`. Every consumer goes
  through it: the register walk (`reg_layout.collect_accessors`, the memory
  map) and an explicit `get_offset_of_instance_array("blk", i)` call in a body
  (C, C++, Python and SV `lower_progseq`).
- `_OffsetEval._const()` (line 641) evaluates every `if` condition, via
  `_truth`. When the condition depends on the index (`index == 0`), it calls
  `_refuse("a condition on the array index")`. `&&` short-circuits, so the
  refusal comes from the first arm whose name test matches.

The evaluator only knows the index as the symbol in `c0 + index*c1`. A
per-index arm can't be expressed that way, even though the array size is
known statically from the field (`blk_c blk[2]`).

## Suggested fix

When symbolic evaluation refuses because a condition depends on the index,
fall back to **concrete** evaluation over the declared array size, and check
the result:

1. Find the field named `field_name` in `group_dtype.fields` and take
   `N = array_size(field)`. `array_base_stride` receives only the name, not
   the field. If no array field has that name, report it as today.
2. For each `i` in `0..N-1`, run the function with the index bound to `i`.
   For example, give `_OffsetEval` an optional `index`, and when it is set
   fold every affine value `(c0, c1)` to `(c0 + c1*i, 0)` in both
   `_const` (conditions) and the `return` path. If an element falls off the
   end or returns the -1 sentinel, report it by index, e.g.
   "`top_c.blk[1]` has no offset". That is the renamed-instance hazard
   `OffsetFoldError` already guards against.
3. If the offsets are evenly spaced (`off[i] == off[0] + i*stride` for every
   `i`), return `(off[0], stride)`. Nothing downstream changes.
4. If they aren't evenly spaced, report an error that says so and lists the
   offsets. Supporting a per-index table in the backends is a larger change
   and can wait.
5. With `N == 1` there is no spacing to measure. Use the element size as the
   stride (the value is never multiplied by a non-zero index). Do not return
   0: the memory map refuses a register array whose stride is smaller than
   its register (`reg_layout.py`, `RegMapError`).

This keeps the guarantee in `_affine`'s docstring: affinity is *checked*,
not assumed from two samples. It is checked against every index rather than
structurally. Any other refusal (a call, a loop, a local) stays an error.

On the release branch, `OffsetFoldError` should be reported as a diagnostic
rather than an internal error. The workarea already does this
(`OpModelTarget.check_register_offsets`, which runs before any file is
written), so the fix is to cut a release that includes it.

## Why it matters

- Every block with replicated register blocks (channels, queues, ports)
  produces instance arrays through `rdl-gen-pss`, so any operation model
  built on that output fails to compile.
- The only workaround is to hand-edit the generated register package, which
  defeats the point of generating it.

Found while porting the "PSS Operation Models" hands-on Lab 2 to an
RDL-generated register flow (timer with `ch_0`/`ch_1` → `tmr_ch_c ch[2]`).

## Alternative (rdl-gen-pss side)

`rdl-gen-pss` could emit `if (name == "ch") { return base + index*stride; }`
when an instance array is evenly spaced. That would unblock users sooner and
produce more readable generated code. But the arms it emits today are valid
PSS, so `pssc` should accept them either way.
