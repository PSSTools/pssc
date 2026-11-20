#!/bin/bash
# Run each bench test with the generated model (+pss_impl=gen), in pyvm
# (+pss_impl=vm) and, when its image is built, on the C engine
# (+pss_impl=native), and check all make the same bus accesses at the same times.
#
#   . ./env.sh; dfm run tests     # builds the images
#   dfm run uvm.tests-native      # optional: builds the native images
#   ./vm_cmp.sh [tlm|wb|rtl ...]  # default: all three
#
# The pyvm image is built here (`pssc build --api`); the native run uses the
# simv and image the native flow built. Logs go to rundir/vm_cmp/.
set -u
cd "$(dirname "$0")"
: "${PSSC_ROOT:?source env.sh first}"
out=rundir/vm_cmp
mkdir -p $out
img=$PWD/$out/wb_dma_c.zbc
PYTHONPATH=$PSSC_ROOT/src PSSC_NO_DAEMON=1 $PSSC_PYTHON -m pssc build \
    rundir/fw-wb-dma.reg-model-pss/*.pss $(find src/pss -name '*.pss' | sort) \
    --root wb_dma_c --api --image $img > $out/build.log 2>&1 \
    || { echo "pssc build failed: $out/build.log"; exit 1; }
nimg=$PWD/rundir/fw-wb-dma.pss.op-model-native-img/wb_dma_c.zbc
pp=$(PYTHONPATH=$PSSC_ROOT/src $PSSC_PYTHON -m pssc sv-pssvm-path --py)

declare -A tests=(
  [tlm]="sw_copy arb arb_eq err hol hw_hs"
  [wb]="sw_copy arb arb_eq err"
  [rtl]="sw_copy arb arb_eq err")
# The reference-compare plusargs each test gets in tests/uvm/flow.yaml.
declare -A pa=(
  [arb]="+CMP_BY_CHANNEL +CMP_NO_ORDER" [arb_eq]="+CMP_BY_CHANNEL +CMP_NO_ORDER"
  [err]="+CMP_BY_CHANNEL" [hol]="+NO_CMP" [hw_hs]="+NO_CMP" [sw_copy]="")
fail=0
for top in ${@:-tlm wb rtl}; do
  simv=$PWD/rundir/fw-wb-dma.uvm.sim-img.$top.opt/dv.uvm.tb-img.sim-img/obj_dir/simv
  nsimv=$PWD/rundir/fw-wb-dma.uvm.sim-img.$top.native/dv.uvm.tb-img.sim-img/obj_dir/simv
  impls="gen vm"; [ -x $nsimv ] && impls="gen vm native"
  for t in ${tests[$top]}; do
    for impl in $impls; do
      d=$out/$top-$t-$impl; mkdir -p $d
      s=$simv; i=$img; [ $impl = native ] && { s=$nsimv; i=$nimg; }
      (cd $d && PYHDL_IF_PYTHON=$PSSC_PYTHON PYTHONPATH=$pp PSSC_NO_DAEMON=1 \
         timeout ${VM_CMP_TIMEOUT:-300} $s +UVM_TESTNAME=wb_dma_${t}_test ${pa[$t]} +pss_impl=$impl \
         +pss_img=$i +pss_bus_log > sim.log 2>&1)
      grep '^PSSBUS' $d/sim.log > $d/bus.txt
    done
    g=$out/$top-$t-gen; v=$out/$top-$t-vm
    if ! grep -q 'TEST PASSED' $v/sim.log; then r="FAIL (vm test failed: $v/sim.log)"
    elif ! grep -q 'TEST PASSED' $g/sim.log; then r="FAIL (gen test failed: $g/sim.log)"
    elif ! cmp -s $g/bus.txt $v/bus.txt; then r="FAIL (bus differs: diff $g/bus.txt $v/bus.txt)"
    else r="PASS ($(wc -l < $v/bus.txt) accesses)"; fi
    n=$out/$top-$t-native
    if [[ $impls == *native* && $r == PASS* ]]; then
      if ! grep -q 'TEST PASSED' $n/sim.log; then r="FAIL (native test failed: $n/sim.log)"
      elif ! cmp -s $g/bus.txt $n/bus.txt; then r="FAIL (bus differs: diff $g/bus.txt $n/bus.txt)"
      else r="$r, native same"; fi
    fi
    case $r in FAIL*) fail=1;; esac
    echo "$top $t: $r"
  done
done
exit $fail
