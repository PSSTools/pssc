"""S9: the sv-native target exposes the oo_api projection by default.

Drives the CLI (`compile -t sv-native`) and checks the package shape selected by
`--projection`, plus that `--export-action` names the export task.
"""
from pathlib import Path

from pssc.cli import main

ATOMIC = (
    "import std_pkg::*;\n"
    "component pss_top {\n"
    "    action Entry { exec post_solve { print(\"hi\"); } }\n"
    "}\n"
)


def _gen(tmp_path, *extra):
    src = tmp_path / "m.pss"
    src.write_text(ATOMIC)
    out = tmp_path / "out"
    code = main(["compile", str(src), "-t", "sv-native", "-o", str(out), "-q", *extra])
    assert code == 0
    pkg = out / "zsp_gen_pkg.sv"
    return out, pkg.read_text()


def test_default_projection_is_oo_api(tmp_path):
    out, pkg = _gen(tmp_path)
    assert "interface class export_api_if" in pkg
    assert "pure virtual task Entry();" in pkg
    assert "class export_api_impl extends pss_top implements export_api_if;" in pkg
    # oo_api emits no standalone harness module and no unlinked DPI import
    assert not (out / "zsp_top.sv").exists()
    assert "import zsp_dpi_pkg" not in pkg


def test_harness_projection_emits_test_top(tmp_path):
    out, pkg = _gen(tmp_path, "--projection", "harness")
    assert (out / "zsp_top.sv").exists()
    top = (out / "zsp_top.sv").read_text()
    assert "module zsp_test_top" in top
    assert "interface class export_api_if" not in pkg


def test_export_action_names_the_task(tmp_path):
    out, pkg = _gen(tmp_path, "--export-action", "Entry")
    assert "pure virtual task Entry();" in pkg


def test_custom_package_name(tmp_path):
    src = tmp_path / "m.pss"
    src.write_text(ATOMIC)
    out = tmp_path / "out"
    code = main(["compile", str(src), "-t", "sv-native", "-o", str(out), "-q",
                 "--package-name", "my_pkg"])
    assert code == 0
    assert (out / "my_pkg.sv").exists()
    assert "package my_pkg;" in (out / "my_pkg.sv").read_text()
