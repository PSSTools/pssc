"""The HAL fixture, end to end (`hal-plugin-requests-plan.md`, phase 6).

`tests/plugins/pssc_hal_fixture` is a target plugin that turns an op-model
component into a stateless C API: plain functions taking the register-window
bases they use, register accesses in the platform's macros, an external
status enum, no lifecycle. It is installed into a temp prefix, as
`test_plugin_integration.py` installs its fixture, and run on its own model
(`model/dev.pss`), the two-window example.

What is held here, in order of how much it proves:

  * DRIVEN: every exported function, run against one tracing memory through
    op-model-c's output and through the fixture's, gives the same bus trace.
    A wrong offset, a lost field or an extra access fails here; a snapshot
    cannot see any of them.
  * every read is the platform's statement macro (`reg_read_form =
    "statement"`), in a loop test, under `&&`, in a later if-chain test, in
    a return and as a call argument, and the trace still matches; three
    misplaced reads fail it;
  * the output compiles with `-Wall -Wextra -Werror` against the platform's
    header, and one function is byte-exact (the fixture's own golden, which
    an in-tree plugin may keep);
  * the fixture differs from op-model-c only in the sections it declares;
  * a function that reaches the component's state is refused, located.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from .test_plugin_integration import discovering, install_plugin

pytestmark = pytest.mark.plugin

_SRC = Path(__file__).resolve().parents[1] / "plugins" / "pssc_hal_fixture"
MODEL = _SRC / "model" / "dev.pss"
PLATFORM = _SRC / "platform"
TARGET = "op-model-hal-fixture"
_CFLAGS = ["-std=c99", "-Wall", "-Wextra", "-Werror"]

#: The two windows' bases, as the platform maps them, and where the status
#: register sits in the second: the oracle for the trace, so not read from
#: anything the fixture generates.
BASES = {"ctl_base": 0x10000, "sts_base": 0x20000}
STAT_ADDR = 0x20000 + 0x8


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return install_plugin(_SRC, tmp_path_factory.mktemp("hal-prefix"))


@pytest.fixture
def hal(installed, monkeypatch):
    with discovering(installed, monkeypatch) as targets:
        assert TARGET in targets.list_targets()
        yield targets


def _compile(target, out, src=MODEL, **opts):
    from pssc import driver
    driver.compile([str(src)], target=target, output_dir=str(out),
                   progseq_root="dev_c", **opts)
    return out


def _cc():
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    return cc


def _build(out, sources, exe):
    r = subprocess.run([_cc(), *_CFLAGS, "-I", str(out), "-I", str(PLATFORM),
                        *map(str, sources), "-o", str(exe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return exe


# -- the output ------------------------------------------------------------

SET_MODE = """\
/* Enable the device in `mode`. */
status_e dev_set_mode(uintptr_t ctl_base, uint8_t mode) {
    uint32_t data;
    DEV_READ32_INTO(ctl_base, CTL_MODE, 0, data);
    DEV_FIELD_SET(CTL_MODE, en, (1), data);
    DEV_FIELD_SET(CTL_MODE, mode, (mode), data);
    DEV_WRITE32(ctl_base, CTL_MODE, 0, data);
    return STATUS_OK;
}
"""


def test_the_api_is_stateless(hal, tmp_path):
    out = _compile(TARGET, tmp_path)
    h, c = (out / "dev.h").read_text(), (out / "dev.c").read_text()
    assert SET_MODE in c
    # Each function takes the windows it uses, and only those.
    assert "status_e dev_wait_idle(uintptr_t sts_base, uint32_t tries);" in h
    assert ("status_e dev_start(uintptr_t ctl_base, uintptr_t sts_base, "
            "uint8_t mode, uint32_t tries);") in h
    # A call passes the callee's windows.
    assert "dev_set_mode(ctl_base, mode);" in c
    assert "return dev_wait_idle(sts_base, tries);" in c
    # An operation the model does not export is the .c's own.
    assert "dev_kick" not in h
    assert "static inline void dev_kick(uintptr_t ctl_base) {" in c
    # No object, no lifecycle, no pssc seam; the platform declares status_e.
    for absent in ("dev_c *", "dev_c;", "_self", "dev_init", "pssc_",
                   "STATUS_OK ="):
        assert absent not in h + c, absent
    # Every read is a statement, its fields taken from the local.
    assert "DEV_READ32(" not in c
    assert ("        DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd0);\n"
            "        if (!(DEV_FIELD_GET(STS_STAT, busy, _pss_rd0) == 1)) {\n"
            "            break;") in c
    assert sorted(p.name for p in out.iterdir()) == [
        "dev.c", "dev.h", "dev_api.json", "dev_map.h"]


def test_the_api_description(hal, tmp_path):
    out = _compile(TARGET, tmp_path)
    api = {f["name"]: f for f in
           json.loads((out / "dev_api.json").read_text())}
    assert api["dev_start"]["bases"] == ["ctl_base", "sts_base"]
    assert api["dev_kick"]["exported"] is False
    assert sorted(n for n, f in api.items() if f["exported"]) == [
        "dev_classify", "dev_copy_len", "dev_set_len", "dev_set_mode",
        "dev_start", "dev_stat_count", "dev_wait_idle"]


def test_it_compiles_against_the_platform(hal, tmp_path):
    out = _compile(TARGET, tmp_path)
    r = subprocess.run([_cc(), *_CFLAGS, "-c", "-I", str(PLATFORM),
                        str(out / "dev.c"), "-o", str(out / "dev.o")],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# -- the differential trace ----------------------------------------------------

#: `(function, arguments)`, or `(None, busy reads)` to make the status
#: register read busy that many times before the next call.
SCENARIO = [
    ("set_mode", (5,)),
    ("set_len", (2, 0x40)),
    (None, 2), ("wait_idle", (5,)),       # idle on the third read
    (None, 3), ("wait_idle", (2,)),       # times out after two reads
    (None, 1), ("start", (3, 4)),
    ("stat_count", ()),                   # a read in a return
    (None, 1), ("classify", (5,)),        # busy: both && reads -> 2
    ("classify", (5,)),                   # idle: && stops, else-if reads
    ("copy_len", (1,)),                   # a read as a call argument
]
RETURNS = {"set_mode", "wait_idle", "start", "stat_count", "classify"}


def _driver(path, call):
    """A main() running SCENARIO, printing a marker before each call and the
    status it returns; ``call(name, args)`` is the C call."""
    lines = ['#include <stdio.h>', '#include "dev.h"', '#include "tb_mem.h"',
             "", "int main(void) {"]
    if "dev_init" in call("init", ()):
        lines.append(f"    {call('init', ())};")
    for name, args in SCENARIO:
        if name is None:
            lines.append(f"    tb_busy(0x{STAT_ADDR:x}, {args});")
            continue
        lines.append(f'    printf("-- {name}\\n");')
        expr = call(name, args)
        lines.append(f'    printf("= %d\\n", (int){expr});'
                     if name in RETURNS else f"    {expr};")
    lines += ["    return 0;", "}", ""]
    path.write_text("\n".join(lines))
    return path


def _run(exe):
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.splitlines()


def _traces(tmp_path, mutate=None):
    """The scenario's bus trace through op-model-c and through the fixture,
    after ``mutate(out)`` has had a go at the fixture's output."""
    base = _compile("op-model-c", tmp_path / "base", c_link_style="direct",
                    c_lifecycle="static")
    out = _compile(TARGET, tmp_path / "hal")
    if mutate is not None:
        mutate(out)

    def base_call(name, args):
        if name == "init":
            return (f"dev_c d;\n    dev_init(&d, 0x{BASES['ctl_base']:x}, "
                    f"0x{BASES['sts_base']:x})")
        return f"dev_{name}(" + ", ".join(["&d", *map(str, args)]) + ")"

    # The bases each function takes, from the fixture's own description.
    api = {f["name"]: f for f in
           json.loads((out / "dev_api.json").read_text())}

    def hal_call(name, args):
        if name == "init":
            return ""
        bases = [f"0x{BASES[b]:x}" for b in api[f"dev_{name}"]["bases"]]
        return f"dev_{name}(" + ", ".join(bases + list(map(str, args))) + ")"

    want = _run(_build(base, [base / "dev.c", PLATFORM / "pssc_seam.c",
                              PLATFORM / "tb_mem.c",
                              _driver(tmp_path / "base_main.c", base_call)],
                       tmp_path / "base_run"))
    got = _run(_build(out, [out / "dev.c", PLATFORM / "tb_mem.c",
                            _driver(tmp_path / "hal_main.c", hal_call)],
                      tmp_path / "hal_run"))
    return want, got


def test_the_bus_trace_is_op_model_cs(hal, tmp_path):
    """Every exported function, through both APIs, against one memory."""
    want, got = _traces(tmp_path)
    assert got == want, "\n".join(
        f"{w:<28} | {g}" for w, g in zip(want + [""] * len(got),
                                         got + [""] * len(want)))
    # ...and the trace says something: the masked write kept the bits it
    # does not own (preset 0xA5A5A5A4, en=1, mode=5), the array element is
    # at its stride, the poll stopped where the memory said.
    assert want[:3] == ["-- set_mode", "R 0x10000 0xa5a5a5a4",
                        "W 0x10000 0xa5a5a5ab"]
    assert "W 0x10030 0x00000040" in want
    assert want.count(f"R 0x{STAT_ADDR:x} 0xa5a5a5a5") == 2 + 2 + 1 + 1
    # ...and the reads in expressions ran in C's order, and only when C
    # evaluates them: both `&&` operands while busy, then the first test
    # and the else-if's.
    stat = f"R 0x{STAT_ADDR:x}"
    first = want.index("-- classify")
    second = want.index("-- classify", first + 1)
    assert want[first:second] == [
        "-- classify", f"{stat} 0xa5a5a5a5", f"{stat} 0xa5a5a5a4", "= 2"]
    assert want[second:second + 4] == [
        "-- classify", f"{stat} 0xa5a5a5a4", f"{stat} 0xa5a5a5a4", "= 0"]
    assert want[-3:] == ["-- copy_len", "R 0x10020 0xa5a5a5a4",
                         "W 0x10028 0xa5a5a5a4"]


def _edit(name, old, new):
    def mutate(out):
        text = (out / name).read_text()
        assert old in text, old
        (out / name).write_text(text.replace(old, new, 1))
    return mutate


@pytest.mark.parametrize("mutate", [
    _edit("dev_map.h", "#define CTL_LEN_STRIDE 0x8u", "#define CTL_LEN_STRIDE 0x4u"),
    _edit("dev.c", "    DEV_FIELD_SET(CTL_MODE, en, (1), data);\n", ""),
    # A loop test read once, before the loop, instead of on each pass.
    _edit("dev.c", "    while (1) {\n"
                   "        uint32_t _pss_rd0;\n"
                   "        DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd0);\n",
          "    uint32_t _pss_rd0;\n"
          "    DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd0);\n"
          "    while (1) {\n"),
    # `a && b` with b read whether or not a holds.
    _edit("dev.c", "    if (_pss_and1) {\n        uint32_t _pss_rd2;",
          "    if (1) {\n        uint32_t _pss_rd2;"),
    # The else-if testing the first read's value instead of reading again.
    _edit("dev.c", "        uint32_t _pss_rd3;\n"
                   "        DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd3);\n"
                   "        if (DEV_FIELD_GET(STS_STAT, count, _pss_rd3) == 0)",
          "        if (DEV_FIELD_GET(STS_STAT, count, _pss_rd0) == 0)"),
], ids=["wrong-stride", "lost-field", "loop-read-once", "and-not-short",
        "else-if-no-read"])
def test_the_trace_sees_what_a_snapshot_cannot(hal, tmp_path, mutate):
    """The check above, run on broken output: it must fail, or it is not
    checking."""
    want, got = _traces(tmp_path, mutate)
    assert got != want


# -- what it changes, and what it refuses --------------------------------------

def test_it_differs_from_op_model_c_only_where_it_says(hal):
    from pssc import testing

    testing.assert_differs_from_baseline(
        TARGET, "op-model-c", sources=[str(MODEL)], root="dev_c",
        expect_changed=["banner", "includes", "api_types", "reg_map", "handles",
                        "decls", "impl", "accessors", "extra_files"])


def test_state_without_a_handle_is_refused_with_its_location(hal, tmp_path):
    """A stateless API has nowhere to keep a field: reading one is the
    model's error, at its line."""
    from pssc import driver

    src = MODEL.read_text().replace(
        "    target function void kick() {",
        "    bit[32] count;\n"
        "    target function bit[32] get_count() { return count; }\n"
        "    target function void kick() {")
    (tmp_path / "m.pss").write_text(src)
    with pytest.raises(driver.CompileError) as ei:
        _compile(TARGET, tmp_path / "out", src=tmp_path / "m.pss")
    assert "no component handle" in "\n".join(map(str, ei.value.errors))
    line = src.splitlines().index(
        "    target function bit[32] get_count() { return count; }") + 1
    assert [m["line"] for m in ei.value.markers] == [line]


SET_LEVEL = """\
void dev_set_level(uintptr_t ctl_base, uintptr_t sts_base) {
    uint32_t data;
    uint32_t _pss_rd0;
    DEV_READ32_INTO(sts_base, STS_STAT, 0, _pss_rd0);
    DEV_READ32_INTO(ctl_base, CTL_MODE, 0, data);
    DEV_FIELD_SET(CTL_MODE, en, (1), data);
    DEV_FIELD_SET(CTL_MODE, level, (DEV_FIELD_GET(STS_STAT, count, _pss_rd0)), data);
    DEV_WRITE32(ctl_base, CTL_MODE, 0, data);
}
"""


def test_a_field_value_that_reads_is_read_once_before_the_write(hal, tmp_path):
    """A field written from another register: that register is read once,
    before the destination's read, and the field names the read's local
    (PC-12). The field values are the write's arguments' own nodes; left
    unsequenced, `DEV_FIELD_SET` re-read `stat` after `mode`."""
    src = MODEL.read_text().replace(
        "    export target function set_mode;",
        "    target function void set_level() {\n"
        "        ctl.mode.write_fields({\"en\", \"level\"},\n"
        "                              {1, sts.stat.read().count});\n"
        "    }\n"
        "    export target function set_level;\n"
        "    export target function set_mode;")
    (tmp_path / "m.pss").write_text(src)
    out = _compile(TARGET, tmp_path / "out", src=tmp_path / "m.pss")
    c = "".join(p.read_text() for p in sorted(out.glob("*.c")))
    assert SET_LEVEL in c


SET_LVL = """\
void dev_set_lvl(uintptr_t ctl_base) {
    uint32_t data;
    uint8_t _pss_fv0;
    uint16_t _pss_fv2;
    _pss_fv0 = dev_lvl(2) & 0x1;
    _pss_fv2 = dev_lvl(3);
    DEV_READ32_INTO(ctl_base, CTL_MODE, 0, data);
    DEV_FIELD_SET(CTL_MODE, en, (_pss_fv0), data);
    DEV_FIELD_SET(CTL_MODE, mode, (2), data);
    DEV_FIELD_SET(CTL_MODE, level, (_pss_fv2), data);
    DEV_WRITE32(ctl_base, CTL_MODE, 0, data);
}
"""


def test_a_field_value_with_a_call_runs_before_the_read(hal, tmp_path):
    """A field value that calls a function: the calls run before the
    destination's read, in source order, each into a temp at the field's
    width; a value without a call stays inline (PC-12). Rendered inside
    `DEV_FIELD_SET`, the calls ran after the read."""
    src = MODEL.read_text().replace(
        "    export target function set_mode;",
        "    function bit[12] lvl(bit[12] v) { return v; }\n"
        "    target function void set_lvl() {\n"
        "        ctl.mode.write_fields({\"en\", \"mode\", \"level\"},\n"
        "                              {lvl(2), 2, lvl(3)});\n"
        "    }\n"
        "    export target function set_lvl;\n"
        "    export target function set_mode;")
    (tmp_path / "m.pss").write_text(src)
    out = _compile(TARGET, tmp_path / "out", src=tmp_path / "m.pss")
    c = "".join(p.read_text() for p in sorted(out.glob("*.c")))
    assert SET_LVL in c


SET_LVL_BLOCK = """\
void dev_set_lvl(uintptr_t ctl_base) {
    {
        uint32_t data;
        uint8_t _pss_fv0;
        uint16_t _pss_fv2;
        _pss_fv0 = dev_lvl(2) & 0x1;
        _pss_fv2 = dev_lvl(3);
        DEV_READ32_INTO(ctl_base, CTL_MODE, 0, data);
        DEV_FIELD_SET(CTL_MODE, en, (_pss_fv0), data);
        DEV_FIELD_SET(CTL_MODE, mode, (2), data);
        DEV_FIELD_SET(CTL_MODE, level, (_pss_fv2), data);
        DEV_WRITE32(ctl_base, CTL_MODE, 0, data);
    }
}
"""


def test_block_scoped_temps_are_declared_in_the_writes_block(
        hal, tmp_path, monkeypatch):
    """`reg_value_local_scope` = "block": the word and the field-value
    temps are declared in a `{ }` of the write's own, not at the top of
    the function (PC-12)."""
    import pssc_hal_fixture.backend as be
    monkeypatch.setattr(be.HalEmitter, "reg_value_local_scope",
                        lambda self, ra, lw: "block", raising=False)
    src = MODEL.read_text().replace(
        "    export target function set_mode;",
        "    function bit[12] lvl(bit[12] v) { return v; }\n"
        "    target function void set_lvl() {\n"
        "        ctl.mode.write_fields({\"en\", \"mode\", \"level\"},\n"
        "                              {lvl(2), 2, lvl(3)});\n"
        "    }\n"
        "    export target function set_lvl;\n"
        "    export target function set_mode;")
    (tmp_path / "m.pss").write_text(src)
    out = _compile(TARGET, tmp_path / "out", src=tmp_path / "m.pss")
    c = "".join(p.read_text() for p in sorted(out.glob("*.c")))
    assert SET_LVL_BLOCK in c


SET_LOW = """\
void dev_set_low(uintptr_t ctl_base, uint16_t v) {
    uint32_t data;
    DEV_READ32_INTO(ctl_base, CTL_MODE, 0, data);
    data = (data & ~(241)) | (((((uint32_t)v & 4095) << 4) | 1) & (241));
    DEV_WRITE32(ctl_base, CTL_MODE, 0, data);
}
"""


def test_a_part_of_a_field_is_merged_from_the_mask(hal, tmp_path):
    """A write of part of a field (`level`'s low 4 bits): `DEV_FIELD_SET`
    cannot say "only these bits", so the fixture's `partial_field_writes =
    False` has pssc spell the modify step from the mask and value
    (`reg_rmw.field_writes`); the fixture itself compares no masks (PC-12)."""
    src = MODEL.read_text().replace(
        "    export target function set_mode;",
        "    target function void set_low(bit[12] v) {\n"
        "        ctl.mode.write_masked({.en=1, .level=0xf}, {.en=1, .level=v});\n"
        "    }\n"
        "    export target function set_low;\n"
        "    export target function set_mode;")
    (tmp_path / "m.pss").write_text(src)
    out = _compile(TARGET, tmp_path / "out", src=tmp_path / "m.pss")
    c = "".join(p.read_text() for p in sorted(out.glob("*.c")))
    assert SET_LOW in c


@pytest.mark.parametrize("target", [TARGET, "op-model-c", "op-model-sv"])
def test_a_call_in_a_field_the_mask_leaves_out_is_refused(hal, tmp_path, target):
    """`level` is in the value literal but not the mask: it is never written,
    and whether `lvl(3)` still ran was each target's choice (the fixture's
    field-wise write dropped it, op-model-c and SV packed and called it).
    `reg_rmw` decides once, for every target: refused, at the call (PC-12)."""
    from pssc import driver

    write = "        ctl.mode.write_masked({.en=1}, {.en=1, .level=lvl(3)});"
    src = MODEL.read_text().replace(
        "    export target function set_mode;",
        "    function bit[12] lvl(bit[12] v) { return v; }\n"
        "    target function void set_om() {\n"
        f"{write}\n"
        "    }\n"
        "    export target function set_om;\n"
        "    export target function set_mode;")
    (tmp_path / "m.pss").write_text(src)
    with pytest.raises(driver.CompileError) as ei:
        _compile(target, tmp_path / "out", src=tmp_path / "m.pss")
    errs = "\n".join(map(str, ei.value.errors))
    assert "field 'level' calls a function but the mask literal leaves it out" \
        in errs
    line = src.splitlines().index(write) + 1
    assert f"m.pss:{line}:" in errs
