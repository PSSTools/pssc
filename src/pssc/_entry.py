"""The `pssc` console entry: routes to the workspace daemon or runs in-process.

`pssc test`, `pssc model` and `pssc dbg` go to the daemon (:mod:`pssc.devenv.client`),
which keeps the pipeline imported (`pssc test --watch` loops here and sends each
round there); `pssc dap` relays to its DAP socket and `pssc sim` asks it
about server-mode simulations;
everything else, and every command when
``PSSC_NO_DAEMON=1`` or no daemon can be started, runs :func:`pssc.cli.main`
here. This module must import nothing heavy at module scope (T-G6).
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd = argv[0] if argv else None
    if cmd == "daemon":
        from .devenv import client
        return client.cmd_daemon(argv[1:])
    if cmd == "sim":
        from .devenv import sim_cmd
        return sim_cmd.main(argv[1:])
    if cmd == "dap":
        from .devenv import dap_cmd
        return dap_cmd.main(argv[1:])
    from .devenv import protocol
    # `--platform sim:S` asks the daemon itself (build, reload, run): here
    sim = cmd == "test" and _sim_platform(argv)
    if cmd == "test" and "--watch" in argv:
        from .devenv import client
        from .testing import watch
        return watch.main(argv, (lambda a: None) if sim or client.disabled()
                          else client.run)
    if cmd in protocol.SERVED and not sim:
        from .devenv import client
        if not client.disabled():
            rc = client.run(argv)
            if rc is not None:
                return rc
    from .cli import main as cli_main
    return cli_main(argv)


def _sim_platform(argv) -> bool:
    """``--platform sim:S`` (as in ``testing.sim_platform.argv_is_sim``,
    not imported: this module stays light)."""
    for i, a in enumerate(argv):
        v = (argv[i + 1] if a == "--platform" and i + 1 < len(argv) else
             a.partition("=")[2] if a.startswith("--platform=") else "")
        if v.startswith("sim:"):
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
