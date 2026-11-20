# Source from this directory: `. ./env.sh`
#
# The bench builds against the fw-wb-dma project's packages (UVM, fw-hdl,
# fw-proto-*, fwvip-*, dv-flow, Verilator 5.052). They are not copied: a
# `packages` symlink (gitignored) points at them.
FW_WB_DMA=${FW_WB_DMA:-$HOME/projects/featherweight-ip/fw-wb-dma}
[ -e packages ] || ln -s "$FW_WB_DMA/packages" packages
export IVPM_PACKAGES=$FW_WB_DMA/packages
export IVPM_PROJECT=$PWD
export UVM_HOME=$IVPM_PACKAGES/uvm
# Verilator 5.052, not pssc's 5.055: 5.055 fails to compile the C++ of the
# class model (`wb_dma_irq`: "no unique final overrider for VlClass::to_string").
export PATH=$IVPM_PACKAGES/verilator/bin:$IVPM_PACKAGES/python/bin:$PATH

# The PSS model API is built by the pssc this example lives in (src/pss/pssvm_gen.py),
# with that repo's own Python, not by the pssc in the fw-wb-dma packages.
export PSSC_ROOT=${PSSC_ROOT:-$(cd ../.. && pwd)}
export PSSC_PYTHON=${PSSC_PYTHON:-$PSSC_ROOT/packages/python/bin/python}
