#!/bin/bash
# Hot reload on the C engine, by hand: one native cell (tlm, sw_copy) is held
# at start, a fixed model is built and loaded into the running simulator
# (`pssc sim reload`), then the test runs on the new generation under the
# debugger and stops at a breakpoint set after the reload.
#
# Needs `dfm run uvm.tests-native -D images=tlm` first (the native image and
# the simulator). Run from this directory after `. ./env.sh`. Prints PASS.
set -u
B=$(cd "$(dirname "$0")" && pwd)
R=${PSSC_ROOT:?source env.sh}
PY=${PSSC_PYTHON:?source env.sh}
T=$(mktemp -d /tmp/pssc-reload-native.XXXXXX)
export PSSC_DAEMON_DIR=$T/daemon PYTHONPATH=$R/src
unset PSSC_NO_DAEMON
pssc() { "$PY" -m pssc "$@"; }
fail() { echo "FAIL: $*"; kill "$SIM" 2>/dev/null; pssc daemon stop >/dev/null 2>&1; exit 1; }

SIMDIR=$B/rundir/fw-wb-dma.uvm.sim-img.tlm.native/dv.uvm.tb-img.sim-img
IMG=$B/rundir/fw-wb-dma.pss.op-model-native-img/wb_dma_c.zbc
REGS=$B/rundir/fw-wb-dma.reg-model-pss/wb_dma_regs_pkg.pss
[ -x "$SIMDIR/obj_dir/simv" ] && [ -f "$IMG" ] || fail "run: dfm run uvm.tests-native -D images=tlm"

# the "fix": a copy of the model with a changed source (line numbers kept)
cp -r "$B/src/pss" "$T/pss"
echo "// fixed" >> "$T/pss/wb_dma_ch_c/functions/configure_channel.pss"
(cd "$T" && PSSC_NO_DAEMON=1 "$PY" -m pssc build "$REGS" $(find pss -name '*.pss' | sort) \
    --root wb_dma_c --api --image "$T/fixed.zbc" --native) > "$T/build.log" 2>&1 \
    || fail "build (see $T/build.log)"

pssc daemon start > /dev/null || fail "daemon start"
(cd "$SIMDIR" && exec env -u PYTHONPATH ./obj_dir/simv +UVM_TESTNAME=wb_dma_sw_copy_test \
    +pss_impl=native +pss_img="$IMG" +pssvm_dbg_start) > "$T/sim.log" 2>&1 &
SIM=$!
for _ in $(seq 60); do
    pssc sim list 2>/dev/null | grep -q "sim[0-9]" && break; sleep 1
done
pssc sim list | grep -q "sim[0-9]" || fail "no session"

pssc sim reload "$T/fixed.zbc" --timeout 60 > "$T/reload.log" 2>&1
cat "$T/reload.log"
grep -q "generation 1 -> 2" "$T/reload.log" || fail "reload"
pssc sim attach > /dev/null || fail "attach"
pssc dbg break add "$B/src/pss/wb_dma_ch_c/functions/configure_channel.pss:28" > /dev/null || fail "break"
pssc sim resume > /dev/null || fail "resume"
pssc dbg continue --timeout 60 > "$T/stop.log" 2>&1
cat "$T/stop.log"
grep -q "breakpoint" "$T/stop.log" || fail "no breakpoint stop"
pssc dbg end > /dev/null
wait "$SIM"; RC=$?
pssc daemon stop > /dev/null 2>&1
grep -q "TEST PASSED" "$T/sim.log" && [ $RC = 0 ] || fail "test (rc $RC, see $T/sim.log)"
echo "PASS (same simulator pid $SIM; log $T)"
