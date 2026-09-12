# Operation model: WB DMA (component tree)

**`pss/` is a copy. Do not edit it here.** Edits belong upstream, in the
fw-wb-dma repository at `src/pss/`; this copy is refreshed from there by
`scripts/sync_op_model.py`.

## What it is

The richer of pssc's two worked examples — the full WISHBONE DMA operation
model, as a component tree:

| | `examples/export/programming_seqs` | this |
|---|---|---|
| shape | one flat component; channel is an argument | `wb_dma_c` owning `wb_dma_ch_c ch[4]` |
| registers | inline `reg_c<…>` specializations | named register component types |
| operations | declared in the component body | declared in `extend component` |
| blocking | poll loop | `yield` to the import layer |
| status | fully supported; the backend contract | the target of current work |

Everything in the right-hand column is a compiler capability under
construction. This example exists so that work is *measured* rather than
asserted: see `docs/op-model-export-plan.md` (in fw-wb-dma) for the phases and
the defects each one closes.

Files are listed in dependency order in `pss/files.f`. **Use that order** —
pssparser D3 makes a wrong order fail silently rather than loudly, so a glob
here produces a model that is quietly missing references and still exits 0.

## Keeping it honest

```bash
scripts/sync_op_model.py --check                     # was the copy edited in place?
scripts/sync_op_model.py --check --src …/src/pss     # has upstream moved on?
scripts/sync_op_model.py --src …/src/pss             # refresh
```

`PROVENANCE.json` records the upstream commit and a SHA-256 per file. The first
form needs only this repository and runs in CI; the other two need both
repositories checked out.

The reason for the manifest rather than a bare "copy it and hope": a stale copy
does not fail, it *passes* — against the wrong model. That is the same
failure shape as the defects this example was created to chase.
