// Per-channel hardware-handshake API (spec §3.2.2, §3.8-§3.10). Models the
// dma_req_i / dma_ack_o / dma_nd_i / dma_rest_i sidebands as a class contract the
// DMA engine CONSUMES (holds an fw_port) and a device model PROVIDES (via
// `FW_WB_DMA_HS_IMP). Normal (software) mode never uses this; HW-handshake mode
// (CSR.MODE=1) gates each chunk on wait_req().
// Extends fw_awaitable_if: a chunk request is one of the heterogeneous things the
// engine waits on, so the HS port IS an event source -- its inherited produce_to()
// wires a dma_req into the engine's monitor (the device signals it on each request).
// An HS request is not a register write, so this is how it reaches the engine's wait
// set. wait_req() is the consuming counterpart used to service the request once
// arbitration has picked the channel.
interface class wb_dma_hs_if extends fw_awaitable_if;
    // Block until the device asserts a chunk request (dma_req). Returns the
    // qualifiers latched with the request (outputs lead):
    //   nd   = force-next-descriptor was asserted with/before this request (§3.9)
    //   rest = restart requested (§3.10) -- engine reloads working registers
    pure virtual task wait_req(output bit nd, output bit rest);

    // Pulse dma_ack: the just-requested chunk has completed (§3.8 timing).
    pure virtual function void ack();

    // Non-blocking: is a chunk request currently pending (dma_req asserted, not
    // yet consumed)? The engine's arbiter gates an HS channel on this -- a channel
    // is only "ready" once its device has posted a request, so a stalled HS device
    // does not block service of other channels (no head-of-line blocking).
    pure virtual function bit has_req();
endclass
