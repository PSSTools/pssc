"""The real WB DMA operation model (examples/op_model/pss), driven through
operations that only golden snapshots covered (00-plan.md §5 P3; 03 §5.1).

A golden snapshot proves the generated text did not change; it cannot say an
address or a value is right. So each operation below is called from a small
`pss_top` and its access trace is held, on all four targets, to a trace
worked out by hand from the model's register package
(`wb_dma_regs_pkg.pss`) and the operation bodies -- never taken from a
backend. The model's files are given in `files.f` order, as one source.

Addresses, with the base at 0x1000: the engine-global bank at 0x1000
(`int_msk_a` +0x4, `int_msk_b` +0x8) and channel `i`'s bank at
0x1000 + 0x20 + 0x20*i (`csr` +0x0, `desc` +0x18, `swptr` +0x1c).
Descriptors are authored at 0x1400.

See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from pathlib import Path

from ._matrix import Case, define_tests
from ..op_model import op_model_sources

#: The model, one source in dependency order.
MODEL = "\n".join(Path(p).read_text() for p in op_model_sources())

DRIVER = """
component pss_top {
  wb_dma_c dma;
  addr_handle_t mem;
  solve function void initialize(addr_handle_t base) {
    dma.initialize(base);
    mem = make_handle_from_handle(base, 0x400);
  }
  target function void run() {
@BODY@
  }
}
"""


def _case(body, want, mem=None, covers=()):
    return Case(MODEL + DRIVER.replace("@BODY@", body), want, mem=mem,
                covers=covers)


CASES = {
    # Two descriptors, the second linked from the first (`write_descriptor`):
    # each is CSR word, ADR0, ADR1, NEXT (cleared), and linking the second
    # writes its address into the first's NEXT (+0xC). The CSR word packs
    # tot_sz [11:0] and eol [20]: 8 | 1 << 20 = 0x100008, then 4. Each call
    # returns the next free slot, 16 bytes on. `read_descriptor_residual`
    # reads the CSR word back and keeps its low 12 bits: 8.
    "write_descriptor and read_descriptor_residual": _case("""
    wb_dma_desc_s d;
    addr_handle_t none;
    addr_handle_t d0 = mem;
    d.csr.tot_sz = 8;
    d.csr.eol = 1;
    d.adr0 = 0x2000;
    d.adr1 = 0x3000;
    d.next = 0x55;
    addr_handle_t d1 = dma.write_descriptor(d0, none, d);
    d.csr.tot_sz = 4;
    d.csr.eol = 0;
    addr_handle_t d2 = dma.write_descriptor(d1, d0, d);
    message(LOW, "%x %x", addr_value(d1), addr_value(d2));
    message(LOW, "%d", dma.read_descriptor_residual(d0));
""", ["write 32 0x1400 0x100008", "write 32 0x1404 0x2000",
      "write 32 0x1408 0x3000", "write 32 0x140c 0x0",
      "write 32 0x1410 0x4", "write 32 0x1414 0x2000",
      "write 32 0x1418 0x3000", "write 32 0x141c 0x0",
      "write 32 0x140c 0x1410",
      "1410 1420",
      "read 32 0x1400 0x100008", "8"],
        covers=("LIB-MEM", "DT-STRUCT", "DT-ADDR", "FN-CALL",
                "DT-ADDR.4")),

    # `configure_interrupt_routing` writes the mask into the bank's register:
    # B at +0x8, A at +0x4. `set_software_pointer` on channel 1 packs ptr
    # [30:0] and en [31] into SWPTR at 0x1040 + 0x1C.
    "configure_interrupt_routing and set_software_pointer": _case("""
    dma.configure_interrupt_routing(WB_DMA_INT_B, 0x5);
    dma.configure_interrupt_routing(WB_DMA_INT_A, 0x7FFFFFFF);
    dma.ch[1].set_software_pointer(0x123, 1);
""", ["write 32 0x1008 0x5", "write 32 0x1004 0x7fffffff",
      "write 32 0x105c 0x80000123"],
        covers=("LIB-REG", "ST-MATCH", "DT-ENUM")),

    # `transfer_list` on channel 2 (bank 0x1060): DESC <- the head, then
    # USE_ED [7] and CH_EN [0] set by read-modify-write, then the CSR polled
    # until DONE [11] -- preloaded, so the first poll ends it.
    # `check_completion` on channel 0 after `transfer_list_start`: CSR has
    # neither DONE nor ERR, so PENDING, and the operation stays in flight. On
    # channel 3, with nothing in flight, it says so and answers PENDING.
    "transfer_list and check_completion": _case("""
    wb_dma_status_e st = dma.ch[2].transfer_list(mem);
    message(LOW, "%n", st);
    dma.ch[0].transfer_list_start(mem);
    message(LOW, "%n", dma.ch[0].check_completion());
    message(LOW, "%n", dma.ch[3].check_completion());
""", ["write 32 0x1078 0x1400",
      "read 32 0x1060 0x800", "write 32 0x1060 0x880",
      "read 32 0x1060 0x880", "write 32 0x1060 0x881",
      "read 32 0x1060 0x881", "WB_DMA_DONE",
      "write 32 0x1038 0x1400",
      "read 32 0x1020 0x0", "write 32 0x1020 0x80",
      "read 32 0x1020 0x80", "write 32 0x1020 0x81",
      "read 32 0x1020 0x81", "WB_DMA_PENDING",
      "wb_dma: check_completion() with no operation in progress on this "
      "channel -- either no *_start() was called, or a previous completion "
      "was already consumed",
      "WB_DMA_PENDING"],
        mem={0x1060: 0x800},
        covers=("LIB-REG", "LIB-CHAN", "ST-WHILE", "ST-RET")),
}

test_construct = define_tests(CASES)
