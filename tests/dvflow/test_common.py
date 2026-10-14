"""Unit tests for pssc.dvflow.common helpers (no DFM runtime required)."""
import logging
import os
import types
from pathlib import Path

import pytest

from pssc.dvflow import common


def _fs(filetype, basedir, files):
    """Duck-typed stand-in for a dv_flow FileSet/DataItem."""
    return types.SimpleNamespace(filetype=filetype, basedir=str(basedir),
                                 files=list(files))


def _input(inputs=(), **params):
    return types.SimpleNamespace(
        inputs=list(inputs),
        params=types.SimpleNamespace(**params))


def test_gather_pss_sources_orders_and_joins(tmp_path):
    inp = _input(inputs=[
        _fs("pssSource", tmp_path, ["a.pss", "b.pss"]),
        _fs("systemVerilogSource", tmp_path, ["ignore.sv"]),
        _fs("pssSource", tmp_path / "sub", ["c.pss"]),
    ])
    got = common.gather_pss_sources(inp)
    assert got == [
        str(tmp_path / "a.pss"),
        str(tmp_path / "b.pss"),
        str(tmp_path / "sub" / "c.pss"),
    ]


def test_gather_pss_sources_absolute_passthrough(tmp_path):
    abs_p = str(tmp_path / "x.pss")
    inp = _input(inputs=[_fs("pssSource", "/nonexistent/base", [abs_p])])
    assert common.gather_pss_sources(inp) == [abs_p]


def _dfm():
    import importlib.util
    return importlib.util.find_spec("dv_flow.mgr") is not None


@pytest.mark.skipif(not _dfm(), reason="dv-flow-mgr not installed")
def test_classify_outputs_groups_by_filetype_and_incdirs(tmp_path):
    # Create representative output files.
    names = ["zsp_gen_pkg.sv", "lib.so", "exec.c", "exec.h",
             "model.cpp", "model.hpp", "zsp_filelist.f"]
    for n in names:
        (tmp_path / n).write_text("x")
    paths = [tmp_path / n for n in names]

    filesets = common.classify_outputs(paths, "task", str(tmp_path))
    by_ft = {fs.filetype: fs for fs in filesets}

    assert set(by_ft) == {"systemVerilogSource", "systemVerilogDPI",
                          "cSource", "cppSource"}
    # .f filelist dropped
    assert "zsp_filelist.f" not in by_ft["systemVerilogSource"].files
    assert by_ft["systemVerilogSource"].files == ["zsp_gen_pkg.sv"]
    # .sv contributes an incdir; .so does not
    assert by_ft["systemVerilogSource"].incdirs == [str(tmp_path)]
    assert by_ft["systemVerilogDPI"].incdirs == []
    # cSource groups .c (no incdir) + .h (incdir)
    assert set(by_ft["cSource"].files) == {"exec.c", "exec.h"}
    assert by_ft["cSource"].incdirs == [str(tmp_path)]
    # cppSource groups .cpp + .hpp
    assert set(by_ft["cppSource"].files) == {"model.cpp", "model.hpp"}
    assert by_ft["cppSource"].incdirs == [str(tmp_path)]


@pytest.mark.skipif(not _dfm(), reason="dv-flow-mgr not installed")
def test_classify_outputs_claims_a_backend_extra_file(tmp_path):
    """P6b.T4. A file from `emit_extra_files` must reach a downstream task.

    The list here is what `COpModelBackend.generate` returns for a subclass
    emitting a register-map header, in that order. Classification is by
    extension, so the failure this guards against is not a wrong fileset but
    silence: an unmapped extension is warned about and DROPPED, and the build
    stays green with the extra file in nothing.
    """
    names = ["wb_dma.h", "wb_dma.c", "wb_dma_map.h"]
    for n in names:
        (tmp_path / n).write_text("x")

    filesets = common.classify_outputs([tmp_path / n for n in names],
                                       "task", str(tmp_path))
    assert len(filesets) == 1
    fs = filesets[0]
    assert fs.filetype == "cSource"
    # Emission order preserved -- it is a compilation order, and the extra
    # comes after the API it accompanies.
    assert fs.files == names
    assert fs.incdirs == [str(tmp_path)]


@pytest.mark.skipif(not _dfm(), reason="dv-flow-mgr not installed")
def test_classify_outputs_skips_outside_basedir(tmp_path):
    inside = tmp_path / "in"
    inside.mkdir()
    f_in = inside / "a.sv"
    f_in.write_text("x")
    outside = tmp_path / "out.sv"
    outside.write_text("x")

    filesets = common.classify_outputs([f_in, outside], "task", str(inside))
    assert len(filesets) == 1
    assert filesets[0].files == ["a.sv"]


def test_compute_memento_is_stable_and_sensitive(tmp_path):
    a = tmp_path / "a.pss"
    a.write_text("component c {}")
    opts = {"export_actions": ["E"], "x": 1}

    m1 = common.compute_memento([str(a)], "sv-native", opts)
    m2 = common.compute_memento([str(a)], "sv-native", opts)
    assert m1 == m2

    # changed source content
    a.write_text("component c { action A {} }")
    assert common.compute_memento([str(a)], "sv-native", opts) != m1

    # changed target
    assert common.compute_memento([str(a)], "c-host", opts) != m1
    # changed option
    assert common.compute_memento([str(a)], "sv-native",
                                  {"export_actions": ["E"], "x": 2}) != m1


def test_ext_filetype_excludes_filelist():
    types = common.registered_filetypes()
    assert ".f" not in types
    assert types[".sv"] == ("systemVerilogSource", True)
    assert types[".so"] == ("systemVerilogDPI", False)
    assert common.filetype_for(".h")[1] is True   # header -> incdir
    assert common.filetype_for("c")[1] is False   # leading dot optional


def test_svh_is_an_include_not_a_source(tmp_path):
    """op-model-sv's package includes one `.svh` per class. A `.svh` in a
    source fileset is compiled on its own, outside the package: an include
    fileset gives the compile its directory and nothing else."""
    names = ["pssc_reg_pkg.sv", "p_pkg.sv", "a_c.svh", "p_regs.svh"]
    for n in names:
        (tmp_path / n).write_text("")
    by_ft = {fs.filetype: fs for fs in common.classify_outputs(
        [str(tmp_path / n) for n in names], "src", str(tmp_path))}
    assert by_ft["systemVerilogSource"].files == ["pssc_reg_pkg.sv", "p_pkg.sv"]
    assert by_ft["systemVerilogInclude"].files == ["a_c.svh", "p_regs.svh"]
    assert by_ft["systemVerilogInclude"].incdirs == [str(tmp_path)]


# --- Phase 4: plugin provenance and filetype registration -------------------

def test_memento_changes_with_target_version(tmp_path, monkeypatch):
    """The hole P4.T3 closes: upgrade the plugin, keep the sources, and the
    cached memento used to match -- so dv-flow skipped the build and the OLD
    generated output went into the next simulation with nothing reporting it."""
    a = tmp_path / "a.pss"
    a.write_text("component c {}")
    opts = {"x": 1}

    monkeypatch.setattr(common, "target_provenance", lambda t: "acme 1.0")
    m1 = common.compute_memento([str(a)], "acme-c", opts)
    assert m1 == common.compute_memento([str(a)], "acme-c", opts)

    monkeypatch.setattr(common, "target_provenance", lambda t: "acme 1.1")
    assert common.compute_memento([str(a)], "acme-c", opts) != m1


def test_unknown_provenance_forces_rebuild(tmp_path):
    """A target nobody can attribute must not hash to a stable value: an empty
    string would make every such build look cached forever."""
    a = tmp_path / "a.pss"
    a.write_text("component c {}")
    prov = common.target_provenance("no-such-target-anywhere")
    assert prov.startswith("unknown:")
    assert prov != ""
    # ...and it is what actually reaches the hash
    m = common.compute_memento([str(a)], "no-such-target-anywhere", {})
    assert m != common.compute_memento([str(a)], "no-such-target-anywhere-2", {})


def test_a_builtin_target_has_a_stable_provenance():
    """Built-ins resolve through pssc's own version, not through distribution
    metadata: a source checkout has no metadata, and hashing 'unknown' there
    would rebuild everything on every dv-flow invocation."""
    from pssc.__version__ import version
    prov = common.target_provenance("op-model-c")
    assert prov == f"pssc {version}"
    assert common.target_provenance("c-progseq") == prov     # through an alias


def test_provenance_reaches_the_memento(tmp_path, monkeypatch):
    a = tmp_path / "a.pss"
    a.write_text("component c {}")
    seen = []
    monkeypatch.setattr(common, "target_provenance",
                        lambda t: seen.append(t) or "v1")
    common.compute_memento([str(a)], "op-model-c", {})
    assert seen == ["op-model-c"]


@pytest.fixture
def filetypes():
    saved = common.registered_filetypes()
    yield
    common._EXT_FILETYPE.clear()
    common._EXT_FILETYPE.update(saved)


def test_register_filetype(filetypes, tmp_path):
    common.register_filetype(".pyi", "pythonSource")
    assert common.filetype_for(".pyi") == ("pythonSource", False)

    stub = tmp_path / "m.pyi"
    stub.write_text("def f() -> int: ...")
    filesets = common.classify_outputs([str(stub)], "task", str(tmp_path))
    assert [fs.filetype for fs in filesets] == ["pythonSource"]
    assert filesets[0].files == ["m.pyi"]


def test_register_filetype_normalises_and_marks_incdirs(filetypes, tmp_path):
    common.register_filetype("VHD", "vhdlSource", is_incdir=True)
    assert common.filetype_for(".vhd") == ("vhdlSource", True)

    src = tmp_path / "a.vhd"
    src.write_text("-- vhdl")
    filesets = common.classify_outputs([str(src)], "task", str(tmp_path))
    assert filesets[0].incdirs == [str(tmp_path)]


def test_register_filetype_rejects_a_missing_filetype(filetypes):
    with pytest.raises(ValueError):
        common.register_filetype(".pyi", "")
    with pytest.raises(ValueError):
        common.register_filetype("", "pythonSource")


def test_unmapped_output_warns(tmp_path, caplog):
    """It used to be _log.debug, so a generated file could be dropped from
    every fileset with nothing on screen and the build still green."""
    orphan = tmp_path / "notes.xyz"
    orphan.write_text("x")
    with caplog.at_level(logging.WARNING, logger="pssc.dvflow"):
        filesets = common.classify_outputs([str(orphan)], "task", str(tmp_path))
    assert filesets == []
    text = caplog.text
    assert "notes.xyz" in text and ".xyz" in text
    assert "register_filetype" in text        # says what to do about it
