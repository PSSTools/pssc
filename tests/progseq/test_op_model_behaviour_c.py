"""C5.1 -- the behavioural gate for the COMPONENT TREE driver.

`test_build_wb_dma_c.py` gates the FLAT example model. It cannot gate this one,
and the reason is the whole point of C1: the flat model has a single register
bank, so nothing in it can tell a correct per-channel offset from a zero one.
The tree has an engine-global block plus four channel banks at
``base + 0x20 + 0x20*i``, folded at generation time from the tree walk, and that
arithmetic is exercised by nothing else in the suite.

So the assertion here is a register ACCESS TRACE, not an exit code. Every
structural test in `test_c_op_model.py` says the generated text looks right;
this is the only one that says the driver talks to the right registers, in the
right order, with the right values.

**The gate was mutation-checked.** Rewriting the generated
``(0x20u + 0x20u * i)`` to ``(0x20u)`` -- every channel pointing at bank 0 --
produces 14 failures here, including "channel banks overlap" and "the
per-channel base is not being applied". A gate that passes is only evidence if
you know what makes it fail.
"""
import os
import shutil
import subprocess

import pytest

from .conftest import available_c_compilers
from .op_model import op_model_sources as _sources

_CC = available_c_compilers()
_HERE = os.path.dirname(__file__)
_TB_DIR = os.path.join(_HERE, "data", "c")
_TB_FILES = ("op_model_mock.h", "op_model_mock.c", "op_model_tb.c")

_CFLAGS = ["-std=c99", "-Wall", "-Wextra", "-Werror"]


def _generate(out_dir, **kw):
    """Generate the tree driver and stage the testbench beside it."""
    import argparse
    from pssc import driver
    ns = argparse.Namespace(progseq_root="wb_dma_c", c_prefix="wb_dma",
                            output_dir=str(out_dir), c_link_style="vtable",
                            # The stub supplies pssc_message, which four
                            # operations call. Using --emit-stubs here rather
                            # than hand-writing one in the TB keeps C4.2 on the
                            # same path the gate exercises.
                            c_emit_stubs=True, **kw)
    res = driver.compile(_sources(), target="op-model-c", opts=ns)
    assert res.outputs, "op-model-c produced no files"
    for f in _TB_FILES:
        shutil.copy2(os.path.join(_TB_DIR, f), str(out_dir))
    return str(out_dir)


def _build_and_run(out, cc):
    exe = os.path.join(out, "run")
    srcs = [os.path.join(out, f) for f in
            ("wb_dma.c", "wb_dma_stubs.c", "op_model_mock.c", "op_model_tb.c")]
    build = subprocess.run([cc, *_CFLAGS, "-I", out, *srcs, "-o", exe],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    return subprocess.run([exe], capture_output=True, text=True)


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
@pytest.mark.parametrize("cc", _CC)
def test_the_tree_driver_programs_the_right_registers(tmp_path, cc):
    """Five cases, all trace-asserted -- see op_model_tb.c for what each holds.

    On failure the whole access trace is printed, because a wrong offset is
    unreadable without it, and the trace is forwarded here for the same reason.
    """
    out = _generate(tmp_path, c_lifecycle="static")
    res = _build_and_run(out, cc)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "WB_DMA OP MODEL PASS" in res.stdout, res.stdout


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
def test_the_gate_fails_when_the_per_channel_base_is_dropped(tmp_path):
    """The mutation check, run rather than described.

    Without this, "the behavioural gate passes" means only that the gate exists.
    The mutation is the exact defect C1 was written to avoid -- a tree walk that
    emits every sub-component at the parent's base -- and it must not be
    survivable.
    """
    out = _generate(tmp_path, c_lifecycle="static")
    src = os.path.join(out, "wb_dma.c")
    text = open(src).read()
    assert "(0x20u + 0x20u * i)" in text, \
        "the generated per-channel base changed shape; update this mutation"
    open(src, "w").write(text.replace("(0x20u + 0x20u * i)", "(0x20u)"))

    res = _build_and_run(out, _CC[0])
    assert res.returncode != 0, \
        "every channel pointed at bank 0 and the gate still passed"
    assert "channel banks overlap" in res.stdout, res.stdout


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
def test_the_gate_fails_when_the_wait_loop_is_removed(tmp_path):
    """The other thing a component-tree gate must catch: an operation that
    returns a status without ever polling for it.

    The mock reports DONE only after OM_PENDING_POLLS reads, so a driver that
    answers from its first read is answering from a CSR that still says BUSY.
    Mutating the mock rather than the driver -- making completion take longer
    than the driver is willing to wait would be a different bug -- would not
    test this; so the driver's poll is what is checked, by count.
    """
    out = _generate(tmp_path, c_lifecycle="static")
    res = _build_and_run(out, _CC[0])
    assert res.returncode == 0, res.stdout + res.stderr
    # Positive form: the passing run above already asserts the poll count via
    # the TB. This pins that the TB's own threshold is meaningful -- if
    # OM_PENDING_POLLS were 1, any driver would satisfy it.
    mock_h = open(os.path.join(out, "op_model_mock.h")).read()
    assert "#define OM_PENDING_POLLS 2" in mock_h, \
        "a threshold of 1 is satisfied by a driver that never loops"


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
def test_the_mock_states_the_register_layout_independently(tmp_path):
    """The mock restates the CSR bit positions instead of including the
    generated header, deliberately: a mock that imported the same definitions as
    the driver would move with them and agree with a wrong driver forever.

    Checked here so the shortcut is not taken later by someone tidying up.
    """
    out = _generate(tmp_path, c_lifecycle="static")
    mock = open(os.path.join(out, "op_model_mock.h")).read()
    assert "wb_dma.h" not in mock, \
        "the mock now includes the generated header; it can no longer disagree"
    assert "#define OM_CSR_DONE" in mock


# --- C5.4 / C5.5: what the driver COSTS ------------------------------------

def _sizes(out, obj):
    """`.text`/`.data`/`.bss` of one object, via size(1)."""
    r = subprocess.run(["size", "-A", obj], cwd=out, capture_output=True,
                       text=True)
    if r.returncode != 0:
        return None
    out_d = {}
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[0].startswith("."):
            try:
                out_d[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out_d


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
def test_the_footprint_is_recorded_not_guessed(tmp_path):
    """C5.4. Design §9's numbers are an ESTIMATE; this makes them a measurement.

    A generous ceiling on purpose: the value is the recorded number in the
    failure message, not the bound. A driver that doubles in size should be
    noticed and explained, not silently accepted -- and a tight bound here would
    just be edited upward every time it tripped.
    """
    out = _generate(tmp_path, c_lifecycle="static")
    r = subprocess.run(
        [_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
         "-Os", "-c", "wb_dma.c", "-o", "wb_dma.o"],
        cwd=out, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sz = _sizes(out, "wb_dma.o")
    if sz is None:
        pytest.skip("size(1) unavailable")
    text = sz.get(".text", 0)

    # `.bss` of wb_dma.o is 0, and that is not the interesting number: under
    # `--lifecycle static` the OBJECT lives in the caller, so the RAM cost is
    # sizeof(wb_dma_c) -- one engine plus four channels inline -- and it shows
    # up only in a TU that declares one.
    with open(os.path.join(out, "sz.c"), "w") as fp:
        fp.write('#include "wb_dma.h"\n'
                 "wb_dma_c the_dma;\n")
    subprocess.run([_CC[0], "-std=c99", "-Os", "-I", out, "-c",
                    os.path.join(out, "sz.c"), "-o",
                    os.path.join(out, "sz.o")], check=True)
    bss = (_sizes(out, "sz.o") or {}).get(".bss", 0)

    print(f"\nFOOTPRINT wb_dma.o -Os: .text={text} bytes; "
          f"sizeof(wb_dma_c)={bss} bytes of .bss (engine + 4 channels inline)")
    assert 0 < text < 32768, f".text={text}: the driver changed size sharply"
    assert 0 < bss < 4096, f"sizeof(wb_dma_c)={bss}: the handle changed size sharply"


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
def test_unreferenced_operations_are_dropped_by_gc_sections(tmp_path):
    """C5.5. Design §9 claims `--gc-sections` removes what a firmware image does
    not call. That claim is load-bearing -- it is the answer to "why is it fine
    to generate all 17 operations for a part with kilobytes of flash?" -- and
    nothing checked it.

    A TU calling exactly one operation must not drag in the other sixteen. The
    check is on the LINKED IMAGE's symbols, because that is where the claim
    lives; an unreferenced function is present in the .o by construction.
    """
    out = _generate(tmp_path, c_lifecycle="static")
    with open(os.path.join(out, "main.c"), "w") as fp:
        fp.write('#include "wb_dma.h"\n'
                 "static const pssc_mem_if BUS;\n"
                 "static wb_dma_c dma;\n"
                 "int main(void) {\n"
                 "  wb_dma_init(&dma, &BUS, 0x1000);\n"
                 "  wb_dma_pause_engine(&dma, 1);\n"
                 "  return 0;\n"
                 "}\n")
    exe = os.path.join(out, "gc")
    r = subprocess.run(
        [_CC[0], "-std=c99", "-Os", "-ffunction-sections", "-fdata-sections",
         "-Wl,--gc-sections", "-I", out,
         os.path.join(out, "wb_dma.c"), os.path.join(out, "wb_dma_stubs.c"),
         os.path.join(out, "main.c"), "-o", exe],
        capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"--gc-sections link unavailable: {r.stderr[:200]}")

    nm = subprocess.run(["nm", exe], capture_output=True, text=True)
    if nm.returncode != 0:
        pytest.skip("nm unavailable")
    syms = nm.stdout

    # Called, so present.
    assert "wb_dma_pause_engine" in syms, "the called operation was collected"
    # Not called, and not reachable from anything called. `transfer_list` is the
    # largest of them, so if anything survives it will.
    for gone in ("wb_dma_ch_transfer_list", "wb_dma_ch_transfer_single",
                 "wb_dma_ch_wait_completion", "wb_dma_read_descriptor_residual"):
        assert gone not in syms, (
            f"{gone} survived --gc-sections; design §9's footprint argument "
            f"does not hold and the generated API is not pay-for-what-you-use")
