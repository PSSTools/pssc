"""dv-flow task: `hdlsim.vlt.SimUVMCase` with the model running in pyvm.

The run itself is the stock Verilator UVM case; the `+pss_impl=vm
+pss_img=...` plusargs come from `pss.op-model-vm-img`'s SimRunArgs. What the
stock case cannot say is the simulator's environment, which pyhdl-if needs to
start this repo's Python (`PYHDL_IF_PYTHON=$PSSC_PYTHON`) and import the pssvm
shim (`PYTHONPATH` = `pssc sv-pssvm-path --py`).
"""
import os
from dv_flow.libhdlsim.vlt_sim_uvm_case import UVMCaseRunner


class _VmCaseRunner(UVMCaseRunner):

    async def exec_sim(self, cmd, logfile="sim.log", **kwargs):
        root = os.environ["PSSC_ROOT"]
        env = dict(self.ctxt.env)
        env.update(
            PYHDL_IF_PYTHON=os.environ["PSSC_PYTHON"],
            PYTHONPATH=os.pathsep.join([
                os.path.join(root, "src"),
                os.path.join(root, "src", "pssc", "share", "py")]),
            PSSC_NO_DAEMON="1")
        return await super().exec_sim(cmd, logfile=logfile, env=env, **kwargs)


async def SimUVMCaseVm(runner, input):
    return await _VmCaseRunner().run(runner, input)
