"""`OpModelTarget.reg_read_form = "statement"` on op-model-c (PC-10d).

Every rendered body is sequenced (`effect_seq.sequence` with
`effect_seq.reg_reads(comp)`) before the checks, and the C emitter renders
each read that is a declaration's or local assignment's whole value through
`reg_read_into`: no read is ever part of a larger expression. The default,
"expression", is what the goldens pin.
"""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest

MODEL = """
package dev_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;

    struct mode_s : packed_s<> {
        bit[1] en; bit[3] mode; bit[12] level; bit[16] spare;
    }
    pure component mode_r : reg_c<mode_s, READWRITE, 32> {}
    pure component word_r : reg_c<bit[32], READWRITE, 32> {}

    pure component ctl_c : reg_group_c {
        mode_r mode;
        word_r len[4];
        function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["mode"]: return 0x0;
                default: return -1;
            }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) {
                ["len"]: return 0x20 + index * 8;
                default: return -1;
            }
        }
    }
}

import addr_reg_pkg::*;

component dev_c {
    dev_pkg::ctl_c ctl;

    solve function void initialize(addr_handle_t base) {
        ctl.set_handle(base);
    }

    target function bit[32] total() {
        return ctl.len[0].read_val() + ctl.len[1].read_val();
    }

    target function bool ready(bit[32] lim) {
        if (ctl.mode.read().en == 1 && ctl.len[0].read_val() < lim) {
            return true;
        }
        return false;
    }

    target function bit[32] wait_level(bit[12] lv) {
        bit[32] n = 0;
        while (ctl.mode.read().level != lv) {
            n = n + 1;
        }
        return n;
    }

    target function bit[8] narrow() {
        bit[8] b = ctl.len[2].read_val();
        bit[4] q = ctl.len[0].read_val();
        bit[32] w;
        w = ctl.len[3].read_val();
        ctl.len[1].read_val();
        return b + q + w;
    }
}
"""

#: A read as a whole statement: `x = <read>;`, `<read>;`, the value-struct
#: form `x = (mode_s){ .raw = <read> };`.
_WHOLE = re.compile(r"^(\w+ = )?(\(\w+\)\{ \.raw = )?"
                    r"pssc_read32\([^()]*\)( \})?;$")


def _gen(tmp_path, monkeypatch, form):
    from pssc import driver
    from pssc.targets.c_progseq_tgt import CProgSeqTarget
    monkeypatch.setattr(CProgSeqTarget, "reg_read_form", form)
    src = tmp_path / "m.pss"
    src.write_text(MODEL)
    out = tmp_path / form
    driver.compile([str(src)], target="op-model-c", output_dir=str(out),
                   progseq_root="dev_c")
    return out


def _reads(text):
    return [ln.strip() for ln in text.splitlines() if "pssc_read32(" in ln
            and "static" not in ln and "#define" not in ln]


def test_every_read_is_a_statement(tmp_path, monkeypatch):
    c = (_gen(tmp_path, monkeypatch, "statement") / "dev.c").read_text()
    reads = _reads(c)
    assert len(reads) == 9, reads
    assert [r for r in reads if not _WHOLE.match(r)] == []
    # Typed by `reg_reads(comp)`: the value struct for `read()`, the
    # register's width for `read_val()`.
    assert "mode_s _pss_rd0;" in c and "uint32_t _pss_rd0;" in c


def test_the_default_is_unchanged(tmp_path, monkeypatch):
    c = (_gen(tmp_path, monkeypatch, "expression") / "dev.c").read_text()
    assert "return pssc_read32(&_self->ctl->len[0].v) + " \
           "pssc_read32(&_self->ctl->len[1].v);" in c
    assert "_pss_rd" not in c


def test_a_read_after_and_is_read_only_when_the_left_is_true(
        tmp_path, monkeypatch):
    c = (_gen(tmp_path, monkeypatch, "statement") / "dev.c").read_text()
    body = c[c.index("dev_ready("):]
    body = body[:body.index("\n}\n")]
    guard = body.index("if (_pss_and1) {")
    assert body.index("pssc_read32(&_self->ctl->len[0].v)") > guard
    assert body.index("pssc_read32(&_self->ctl->mode)") < guard


def test_a_loop_test_is_reread_each_iteration(tmp_path, monkeypatch):
    c = (_gen(tmp_path, monkeypatch, "statement") / "dev.c").read_text()
    body = c[c.index("dev_wait_level("):]
    lines = [ln.strip() for ln in body[:body.index("\n}\n")].splitlines()]
    k = lines.index("while (1) {")
    assert lines[k + 1:k + 4] == [
        "mode_s _pss_rd0;",
        "_pss_rd0 = (mode_s){ .raw = pssc_read32(&_self->ctl->mode) };",
        "if (!(_pss_rd0.level != lv)) {"]


def test_a_converted_read_goes_through_the_registers_local(
        tmp_path, monkeypatch):
    c = (_gen(tmp_path, monkeypatch, "statement") / "dev.c").read_text()
    body = c[c.index("dev_narrow("):]
    lines = [ln.strip() for ln in body[:body.index("\n}\n")].splitlines()]
    k = lines.index("_pss_reg = pssc_read32(&_self->ctl->len[0].v);")
    assert lines[k + 1].startswith("q = ") and "_pss_reg" in lines[k + 1]
    assert "uint32_t _pss_reg;" in lines


@pytest.mark.skipif(shutil.which("gcc") is None, reason="no gcc")
@pytest.mark.parametrize("form", ["expression", "statement"])
def test_it_compiles(tmp_path, monkeypatch, form):
    out = _gen(tmp_path, monkeypatch, form)
    r = subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "-c", str(out / "dev.c"), "-I", str(out),
                        "-o", str(tmp_path / "dev.o")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_an_unknown_form_is_refused(tmp_path, monkeypatch):
    with pytest.raises(Exception, match="reg_read_form"):
        _gen(tmp_path, monkeypatch, "inline")
