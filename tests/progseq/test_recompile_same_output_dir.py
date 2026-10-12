"""Compiling the same model into the same ``-o`` directory twice.

Before this, `OpModelTarget.install_core` (`targets/op_model.py`) copied the
runtime core file with ``shutil.copy2``, which preserves the SOURCE's mode. A
read-only-shipped runtime source (common for package data checked out or
installed read-only) then made its first-compile COPY read-only too, so a
second compile into the same directory failed with
``PermissionError: [Errno 13] Permission denied`` trying to overwrite its own
prior output.

This exercises the real resource file (`share/sv/pssc_reg_pkg.sv`), made
read-only for the duration of the test, rather than a stand-in -- that is
exactly the condition that reproduced the bug.
"""
import argparse
import os
import stat

import pytest

from pssc import driver, resources

_MODEL = """
component root_c {
    target function bit[32] op(bit[32] v) {
        return v;
    }

    export target function op;
}
"""


@pytest.fixture
def readonly_sv_core():
    path = resources.core_file("pssc_reg_pkg.sv", "pssc", "sv")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        yield path
    finally:
        os.chmod(path, mode)


def test_a_second_compile_overwrites_a_read_only_shipped_core_file(
    tmp_path, readonly_sv_core
):
    src = tmp_path / "root.pss"
    src.write_text(_MODEL)
    ns = argparse.Namespace(progseq_root="root_c", output_dir=str(tmp_path))
    driver.compile([str(src)], target="op-model-sv", opts=ns)
    driver.compile([str(src)], target="op-model-sv", opts=ns)
    assert (tmp_path / "pssc_reg_pkg.sv").exists()
