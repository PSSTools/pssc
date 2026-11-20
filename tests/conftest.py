"""Root conftest for pssc tests.

Stubs pssparser if the native library is not available so that
pure-Python tests (e.g. SV lowering) can still run.
"""
import os
import sys
import types

import pytest

# pssc's c-host target uses dv-solve directly (pssc.targets.sw_solve). Making
# dv-solve importable for that also makes zuspec-dataclasses prefer its native
# (dv-solve) randomization backend, which is currently incomplete for several
# constraint kinds. The Python-path integration tests have always exercised the
# pure-Python backend, so pin it here to keep their behavior unchanged.
os.environ.setdefault("ZSP_SOLVER_BACKEND", "python")

# `pssc test` / `pssc model` (and `python -m pssc` in a subprocess) would
# otherwise start a workspace daemon; tests/unit/test_devenv_daemon.py turns
# this off for its own runs.
os.environ.setdefault("PSSC_NO_DAEMON", "1")

if "pssparser" not in sys.modules:
    try:
        import pssparser  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        _stub = types.ModuleType("pssparser")
        _stub.Parser = None
        _stub.ParseException = Exception
        sys.modules["pssparser"] = _stub

        _ast = types.ModuleType("pssparser.ast")
        sys.modules["pssparser.ast"] = _ast

        _core = types.ModuleType("pssparser.core")

        class _FakeFactory:
            @staticmethod
            def inst():
                return _FakeFactory()
            def getDebugMgr(self):
                class _Dbg:
                    def enable(self, v): pass
                return _Dbg()

        _core.Factory = _FakeFactory
        sys.modules["pssparser.core"] = _core


@pytest.fixture(autouse=True)
def _pssunit_lastfailed(tmp_path_factory, monkeypatch):
    """Keep `pssc test`'s ``--lf`` record out of the directory pytest runs in:
    many tests run the command from the repo root, and some of them fail."""
    try:
        from pssc.testing import cmd
    except ImportError:
        return
    monkeypatch.setattr(cmd, "LAST_FAILED",
                        str(tmp_path_factory.getbasetemp() / "lastfailed"))
