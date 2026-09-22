"""``_dvsolve_share()`` must resolve against whatever dv-solve is installed.

``tests/unit/test_target_sw.py`` already builds and runs a ``--runtime-solve``
program end to end, which covers this indirectly. These tests pin the contract
directly, because the failure mode it guards against is specific and easy to
reintroduce: the helper used to derive ``<root>/src/c`` and ``<root>/build``
from ``dv_solve.__file__``'s grandparent, which is a SOURCE-TREE layout. With
dv-solve installed as a wheel that grandparent is inside site-packages,
neither path exists, and the runtime-solve bridge could only be built from a
checkout -- failing for everyone on released wheels with

    fatal error: zsp_block_alloc.h: No such file or directory

A test that only builds from this monorepo would keep passing throughout.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from pssc.targets.sw_tgt import _dvsolve_share

dv_solve = pytest.importorskip("dv_solve")


def test_returns_dirs_that_actually_hold_the_artifacts():
    """Not "directories that exist" -- directories with the goods in them.

    An unbuilt checkout has a ``src/c`` and a configured-but-unbuilt CMake
    tree has a ``build/lib``; both satisfy an existence check and neither can
    compile or link anything.
    """
    incs, libdir = _dvsolve_share()
    assert any((Path(d) / "zsp_block_alloc.h").is_file() for d in incs), \
        "no include dir holds zsp_block_alloc.h: %s" % incs
    assert (Path(libdir) / "libdv_solve.so").is_file(), \
        "reported lib dir has no linkable libdv_solve.so: %s" % libdir


def test_agrees_with_the_library_the_python_api_loads():
    """The invariant that matters across the whole stack: generated C must
    link against the SAME installation the Python solver runs out of. When
    they diverge the ABI mismatch shows up as a crash inside
    ``solver_compile``, nowhere near anything naming dv-solve."""
    from dv_solve import lib as dv_lib

    loaded = dv_lib._find_library()
    if loaded is None:
        pytest.skip("no dv-solve library on this host")
    _incs, libdir = _dvsolve_share()
    assert Path(loaded).parent == Path(libdir)


def test_reports_every_include_dir_not_just_the_first():
    """An installed wheel needs the namespaced ``dv_solve/`` subdirectory as
    well as the base: dv-solve's headers include each other unqualified, and
    the generated ``pssc_solve.c`` emits unqualified includes too. Returning a
    single directory could not express that, so the caller takes a list."""
    incs, _libdir = _dvsolve_share()
    assert isinstance(incs, list) and incs


@pytest.mark.c_toolchain
def test_generated_solver_tu_compiles_against_the_reported_dirs(tmp_path):
    """Compile exactly what pssc emits, with exactly the include set the
    helper reports. This is the check that would have caught the wheel
    breakage: it fails if the reported dirs cannot satisfy the unqualified
    includes the generator produces.
    """
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    incs, _libdir = _dvsolve_share()
    src = tmp_path / "pssc_solve.c"
    src.write_text(
        '#include <stdint.h>\n'
        '#include <string.h>\n'
        '#include "zsp_block_alloc.h"\n'
        '#include "zsp_problem.h"\n'
        '#include "zsp_ctx.h"\n'
        '#include "zsp_search.h"\n'
        'int probe(void) { return 0; }\n')
    r = subprocess.run(
        ["gcc", "-c", "-fPIC", "-w", *["-I%s" % i for i in incs],
         str(src), "-o", str(tmp_path / "pssc_solve.o")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_solver_includes_stay_separate_from_the_backend_set():
    """dv-solve and zuspec-be-sw both ship a ``zsp_alloc.h`` declaring an
    incompatible ``struct zsp_alloc_s``. The two include sets must never be
    merged into one ``-I`` list; this pins that they are in fact distinct, so
    a future refactor that "simplifies" them into one is a test failure and
    not a debugging session.
    """
    besw = pytest.importorskip("zuspec.be.sw")
    incs, _libdir = _dvsolve_share()
    backend_inc = Path(besw.__file__).parent / "share" / "include"
    if not backend_inc.is_dir():
        pytest.skip("be-sw include dir not present")
    assert backend_inc not in [Path(d) for d in incs]
    # And both really do ship the colliding header.
    assert (backend_inc / "zsp_alloc.h").is_file()
    assert any((Path(d) / "zsp_alloc.h").is_file() for d in incs)
