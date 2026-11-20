# UVM bench: WB DMA (copy of fw-wb-dma)

A copy of the fw-wb-dma project's UVM testbench, used to run the bench's
tests over pssc's pyvm (`+pss_impl=vm`). The original lives in
`~/projects/featherweight-ip/fw-wb-dma` and is not edited from here.

## What was copied

| here | from fw-wb-dma | changed |
|---|---|---|
| `tests/uvm/` | `tests/uvm/` | see below |
| `src/` | `src/` (rdl, pss, spl, spl-rtl, rtl) | no |
| `flow.yaml` | `flow.yaml` | `tests/perf`, `tests/bench`, `tests/fw` fragments dropped |
| `uvm-dv-project.yaml`, `uvm-dv-project/` | same | no |

Changes in `tests/uvm/`: fw-proto-wb moved the TLM memory adapters
(`wb_proto_client_export`, `wb_mem_*`) into `fw_proto_wb_spl_pkg`, which it
exports only under a config. The copy compiles that package itself (task
`fw-proto-wb-spl` in `tests/uvm/flow.yaml`) and imports it in
`env/wb_dma_env_pkg.sv`. Without this the original does not compile against
its current packages.

## Build and run

```sh
cd examples/fw_wb_dma_uvm
. ./env.sh                       # packages symlink, UVM_HOME, Verilator 5.052
dfm run tests -D images=tlm      # 6 tests on the class-model view (~40 s)
dfm run tests                    # 14 tests on the tlm, wb and rtl views
```

Results land in `rundir/` (gitignored). One test by hand:

```sh
cd rundir/fw-wb-dma.uvm.sim-img.tlm.opt/dv.uvm.tb-img.sim-img
./obj_dir/simv +UVM_TESTNAME=wb_dma_sw_copy_test
```

## The PSS model API

`pss.op-model-sv` (`src/pss/pssvm_gen.py`) compiles the PSS model with the
pssc of this repository, not the one in the fw-wb-dma packages: `env.sh` sets
`PSSC_ROOT` (this repo) and `PSSC_PYTHON` (its Python). The target is
`op-model-sv-vm --with-gen`, so the package holds both the generated model and
the pyvm facade; the env creates the model with `wb_dma_c_root_sel::create`,
which picks one at run time from `+pss_impl=gen|vm` (default `gen`).

The image also compiles pyhdl-if's `pyhdl_if.sv` and pssc's
`pssvm_hif_pkg.sv`, and links `libpyhdl_if.so`, so the VM path can start
(task parameter `dpi`, default on). dv-flow does not rerun that task when only
`pssvm_gen.py` changes: remove `rundir/fw-wb-dma.pss.op-model-sv` after editing it.

## Running the model in pyvm

`+pss_impl=vm +pss_img=<image.zbc>` runs the same tests with the model in
pssc's Python VM, embedded through pyhdl-if (the simulator needs
`PYHDL_IF_PYTHON=$PSSC_PYTHON` and `PYTHONPATH=$(pssc sv-pssvm-path --py)`).
`+pss_bus_log` prints each model register access as `PSSBUS <time> R|W addr data`.

`./vm_cmp.sh [tlm|wb|rtl ...]` builds the image (`pssc build --api`), runs each
test both ways and checks that both make the same accesses at the same times;
logs go to `rundir/vm_cmp/`. `VM_CMP_TIMEOUT` (seconds, default 300) bounds
each run. All 14 tests match.

`dfm run uvm.tests-vm` runs the regression's 14 cases in pyvm (`-D images=`
subsets it as for `tests`); `dfm run tests` stays on the generated model. The
image comes from task `pss.op-model-vm-img` (`src/pss/pssvm_gen.py`
`PssvmImage`), and the cells run through `tests/uvm/pssvm_case.py`, the stock
Verilator UVM case with the environment above.
