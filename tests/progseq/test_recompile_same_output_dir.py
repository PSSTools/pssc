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


# --- A read-only copy an older release left behind --------------------------
#
# `copyfile` keeps a NEW copy writable, but an output directory compiled by a
# release that still used `copy2` holds read-only copies, and `copyfile` opens
# its destination for writing. The install replaces the file instead
# (`op_model._install_copy`); a failure that remains is a diagnostic.

_TARGETS = ["op-model-c", "op-model-cpp", "op-model-py", "op-model-sv"]
_LANGS = {"op-model-c": "c", "op-model-cpp": "cpp", "op-model-py": "py",
          "op-model-sv": "sv"}


def _compile(tmp_path, target, out):
    src = tmp_path / "root.pss"
    src.write_text(_MODEL)
    ns = argparse.Namespace(progseq_root="root_c", output_dir=str(out))
    return driver.compile([str(src)], target=target, opts=ns)


def _runtime(target, out):
    """(copy, shipped source) for each runtime file in *out*."""
    pairs = []
    for p in sorted(out.iterdir()):
        try:
            pairs.append((p, resources.core_file(p.name, "pssc",
                                                 _LANGS[target])))
        except resources.ResourceError:
            pass
    return pairs


@pytest.mark.parametrize("target", _TARGETS)
def test_a_read_only_copy_from_an_older_release_is_replaced(tmp_path, target):
    out = tmp_path / "out"
    _compile(tmp_path, target, out)
    runtime = _runtime(target, out)
    assert runtime
    for copy, _ in runtime:
        copy.write_bytes(b"stale\n")
        os.chmod(copy, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    shipped_modes = {src: os.stat(src).st_mode for _, src in runtime}
    _compile(tmp_path, target, out)
    for copy, src in runtime:
        assert copy.read_bytes() == src.read_bytes(), copy.name
        assert os.stat(copy).st_mode & stat.S_IWUSR, copy.name
    assert {src: os.stat(src).st_mode for _, src in runtime} == shipped_modes
    assert not [p.name for p in out.iterdir() if p.name.startswith(".")], \
        "a temp file was left behind"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes anywhere")
def test_an_unwritable_output_directory_is_a_diagnostic(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    os.chmod(out, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(driver.CompileError) as ei:
            _compile(tmp_path, "op-model-c", out)
    finally:
        os.chmod(out, stat.S_IRWXU)
    assert "cannot write the generated files" in str(ei.value)
    assert str(out) in str(ei.value)
