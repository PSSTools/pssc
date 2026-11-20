// Top behavioral component of the DMA model. A PURE component: it instances the
// register file, the data-mover engine, and the interrupt-propagation block and
// owns their structure, but knows nothing about signal-level transactors or pins.
// The engine's fw_mem_if / handshake endpoints are bound to the Wishbone world by
// the module wrapper (wb_dma_spl). The interrupt LEVEL path is self-contained: the
// propagation block (irqc) recomputes inta/intb from rf's interrupt-state seam and
// drives the two level outputs, so only irq_a/irq_b leave the model. The engine's
// per-channel CAUSE port (de.irq) is a SEPARATE, OPTIONAL seam -- an external cause
// scoreboard may connect it, or it is left open (the engine treats it as optional).
// Binding endpoints:
//   rf.host  -- host (CPU) register access (fw_mem_if slave)
//   de.mif0  -- WISHBONE IF0 master (fw_mem_if)
//   de.mif1  -- WISHBONE IF1 master (fw_mem_if)
//   de.hs[i] -- channel i HW handshake
//   de.irq   -- per-channel interrupt causes (OPTIONAL; for a scoreboard)
//   irq_a    -- aggregate interrupt A level output (gpio_drive_if; optional)
//   irq_b    -- aggregate interrupt B level output (gpio_drive_if; optional)
class wb_dma extends fw_component;
    int unsigned n_ch;
    wb_dma_rf    rf;
    wb_dma_de    de;
    wb_dma_irq   irqc;                            // interrupt propagation (de.irq -> levels)

    // Aggregate interrupt level outputs. The model drives these (calls set()), so
    // they are consumer ports; a boundary bridge provides the gpio_drive_if.
    fw_port #(gpio_drive_if #(1)) irq_a, irq_b;

    function new(string name, fw_component parent, int unsigned n_ch = 4);
        super.new(name, parent);
        this.n_ch = n_ch;
    endfunction

    function void build();
        // Just create the immediate children; do_build() recurses top-down, so no
        // manual child.build() calls. de.build() reads rf.ch/rf.n_ch (populated in
        // the rf constructor, before any build()), so ordering is safe. The output
        // ports are created before irqc so it can capture their handles.
        rf    = new("rf", this, n_ch);
        de    = new("de", this, rf);
        irq_a = new("irq_a", this);
        irq_b = new("irq_b", this);
        irqc  = new("irqc", this, rf, irq_a, irq_b);
    endfunction

    function void connect();
        // Subscribe the propagation block to rf's interrupt-state seam. That seam
        // fires on every event that can move inta/intb -- a source SET (a CSR int
        // bit), a mask write, or a CSR read-clear -- so the LEVEL path is fully
        // internal. de.irq is deliberately NOT wired here: it is the OPTIONAL cause
        // seam an external scoreboard may bind.
        rf.add_int_sink(irqc);
    endfunction
endclass
