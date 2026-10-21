"""PC-6: an EXTERNAL type (`StylePolicy.is_extern_type`; ruling R-H6 (a)) is
used by the generated C, never defined: its definition is left out, an
enum's items keep their PSS names, and its names still take part in the
clash check (`c_names.occupied_names`).

The policy here decides from the type's own annotation, as a plugin would.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pssc import annotations, driver, targets
from pssc.targets.c.backend import COpModelBackend
from pssc.targets.c.style import CStylePolicy

MODEL = """
package plat_pkg {
    annotation extern_s { }
    @extern_s
    enum result_e { RC_OK = 0, RC_FAIL = 1 }
    enum mode_e { M_OFF, M_ON }
}
component top_c {
    target function plat_pkg::result_e go(plat_pkg::mode_e m) {
        if (m == plat_pkg::mode_e::M_ON) {
            return plat_pkg::result_e::RC_OK;
        }
        return plat_pkg::result_e::RC_FAIL;
    }
}
"""

#: The platform's own declaration of the external enum.
PLATFORM_H = """\
#ifndef PLAT_H
#define PLAT_H
typedef enum { RC_OK = 0, RC_FAIL = 1 } result_e;
#endif
"""


class _ExternStyle(CStylePolicy):
    def is_extern_type(self, dtype):
        return annotations.of(dtype, "plat_pkg::extern_s") is not None


class _ExternBackend(COpModelBackend):
    style_cls = _ExternStyle


def _gen(tmp_path, monkeypatch, src=MODEL, **opts):
    tgt = targets.get("op-model-c")
    monkeypatch.setattr(type(tgt), "backend_cls", _ExternBackend)
    p = tmp_path / "m.pss"
    p.write_text(src)
    out = tmp_path / "out"
    (tmp_path / "plat.h").write_text(PLATFORM_H)
    driver.compile([str(p)], target="op-model-c", output_dir=str(out),
                   progseq_root="top_c", c_include=["plat.h"], **opts)
    return out, (out / "top.h").read_text(), (out / "top.c").read_text()


def test_an_external_enum_is_used_and_not_defined(tmp_path, monkeypatch):
    out, h, c = _gen(tmp_path, monkeypatch)
    # Not defined here; the platform's header is included instead.
    assert "RC_OK =" not in h
    assert '#include "plat.h"' in h
    assert "result_e top_go(top_c *_self, mode_e m);" in h
    # Its items keep their PSS names; another enum's are prefixed as ever.
    assert "return RC_OK;" in c
    assert "MODE_M_ON" in c and "MODE_M_ON = 1" in h
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    r = subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-c",
                        "-I", str(tmp_path), str(out / "top.c"), "-o",
                        str(out / "top.o")],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_an_external_name_still_clashes(tmp_path, monkeypatch):
    """A generated name equal to an external one is refused: the platform
    declares it, so C would see it twice."""
    src = MODEL.replace("enum mode_e { M_OFF, M_ON }",
                        "enum mode_e { M_OFF, M_ON }\n"
                        "    enum rc_e { OK }")
    src = src.replace("target function plat_pkg::result_e go(",
                      "target function plat_pkg::rc_e k() "
                      "{ return plat_pkg::rc_e::OK; }\n"
                      "    target function plat_pkg::result_e go(")
    with pytest.raises(driver.CompileError) as ei:
        _gen(tmp_path, monkeypatch, src)
    # (An enum's IR name is not yet qualified; phase 3 left it.)
    assert ("'RC_OK' is both item 'OK' of enum "
            "'rc_e' and item 'RC_OK' of "
            "external enum 'result_e'") in "\n".join(
                map(str, ei.value.errors))


def test_the_manifest_marks_an_external_enum(tmp_path, monkeypatch):
    man = tmp_path / "m.json"
    _gen(tmp_path, monkeypatch, progseq_manifest=str(man))
    enums = {e["name"]: e for e in json.loads(man.read_text())["enums"]}
    assert enums["result_e"]["extern"] is True
    assert "extern" not in enums["mode_e"]
    assert [i["symbol"] for i in enums["result_e"]["items"]] == [
        "RC_OK", "RC_FAIL"]
