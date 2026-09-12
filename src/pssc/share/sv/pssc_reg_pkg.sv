// pssc_reg_pkg -- core runtime for pssc-generated programming-sequence models.
//
// IP-independent, hand-written ONCE, shipped with pssc and reused verbatim by
// every generated package (see `pssc compile -t sv-progseq` and
// `pssc sv-core-path`). Holds:
//   * addr_handle_t   -- PSS addr_handle_t mapped to a 64-bit SV address
//   * pss_mem_if   -- the memory-access interface (THE seam to the DUT)
//   * reg_access_e    -- register access mode
//   * reg_c           -- generic register handle (address folded at build,
//                        width-based transaction sizing)
//   * channel_c       -- PSS `sync_pkg::channel_c<Te, DEPTH>` (§21.9.1)
//
// Design: docs/design/pss-programming-seq-gen-design.md (§5).

package pssc_reg_pkg;

  // PSS addr_handle_t -> 64-bit SV address.
  typedef bit [63:0] addr_handle_t;

  // ----- The memory-access interface. -----
  // Pure-virtual, stateless API -> interface class (suffix `_if`). The user (or
  // an adapter generated next to the model) supplies the implementation. Tasks,
  // not functions: a front-door access can consume time. Reads return through
  // an output argument.
  interface class pss_mem_if;
    pure virtual task write8 (addr_handle_t addr, bit [7:0]  data);
    pure virtual task read8  (addr_handle_t addr, output bit [7:0]  data);
    pure virtual task write16(addr_handle_t addr, bit [15:0] data);
    pure virtual task read16 (addr_handle_t addr, output bit [15:0] data);
    pure virtual task write32(addr_handle_t addr, bit [31:0] data);
    pure virtual task read32 (addr_handle_t addr, output bit [31:0] data);
    pure virtual task write64(addr_handle_t addr, bit [63:0] data);
    pure virtual task read64 (addr_handle_t addr, output bit [63:0] data);
  endclass

  // ----- Register access mode. -----
  typedef enum {READWRITE, READONLY, WRITEONLY} reg_access_e;

  // ----- Where one declared field sits in its register. -----
  // The generated package emits one `localparam reg_field_t` per field of each
  // register value struct, named `<VALUE_STRUCT>_<field>` -- so a masked write
  // reads as `csr.write_field(WB_DMA_CH_CSR_ars, 1)` rather than as the pair of
  // magic numbers it folds to. The constants are also the only way a hand-
  // written testbench can name a register bit without re-deriving the layout.
  //
  // NOT an enum, and the reason is correctness rather than taste. An SV enum
  // with no explicit base type is `int` -- signed, 32 bits -- so a mask on bit
  // 31 would be negative and a 64-bit register's mask would not fit at all.
  // The two members also want different types: a mask is a sized vector, a
  // shift is an int, and one enum cannot be both.
  //
  // `mask` is 64 bits because that is the widest register `reg_c` supports; it
  // is narrowed to the register's own `data_t` at each point of use. The field
  // bits are held IN PLACE (already shifted), so `mask` alone is enough to test
  // or clear a field, and `shift` is only needed to align a value.
  typedef struct packed {
    bit [63:0] mask;    // the field's bits, in place
    int        shift;   // the field's LSB
  } reg_field_t;

  // ----- Generic register handle, parameterized by value type. -----
  // Holds its absolute address, folded at build time. data_t tracks $bits(T);
  // the bus primitive is selected from ACC_W (register width rounded up to the
  // nearest legal transaction size: 8/16/32/64). All widths fold at elaboration.
  class reg_c #(type T = bit [31:0], reg_access_e ACC = READWRITE);
    localparam int          WIDTH = $bits(T);
    localparam int          POW2  = 1 << $clog2(WIDTH);          // nearest pow2 >= WIDTH
    localparam int          ACC_W = POW2 < 8 ? 8 : (POW2 > 64 ? 64 : POW2);
    typedef bit [WIDTH-1:0] data_t;                             // raw value-vector, width of T

    protected pss_mem_if m_bus;
    protected addr_handle_t m_addr;

    function new(pss_mem_if bus, addr_handle_t addr);
      m_bus  = bus;
      m_addr = addr;
    endfunction

    function addr_handle_t addr(); return m_addr; endfunction

    // Struct-typed accessors -- legible field-level access at call sites.
    task write(T v);
      data_t raw = v;   // bitstream pack
      write_val(raw);
    endtask
    task read(output T v);
      data_t raw;
      read_val(raw);
      v = T'(raw);
    endtask

    // Raw accessors -- value width tracks T; transaction sized to ACC_W.
    task write_val(data_t v);
      if      (ACC_W == 8)  m_bus.write8 (m_addr,  8'(v));
      else if (ACC_W == 16) m_bus.write16(m_addr, 16'(v));
      else if (ACC_W == 32) m_bus.write32(m_addr, 32'(v));
      else                  m_bus.write64(m_addr, 64'(v));
    endtask
    task read_val(output data_t v);
      bit [7:0]  d8;
      bit [15:0] d16;
      bit [31:0] d32;
      bit [63:0] d64;
      if      (ACC_W == 8)  begin m_bus.read8 (m_addr, d8);  v = data_t'(d8);  end
      else if (ACC_W == 16) begin m_bus.read16(m_addr, d16); v = data_t'(d16); end
      else if (ACC_W == 32) begin m_bus.read32(m_addr, d32); v = data_t'(d32); end
      else                  begin m_bus.read64(m_addr, d64); v = data_t'(d64); end
    endtask

    // Masked write -- PSS 3.1 §21.14.1:
    //
    //   REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)
    //
    // THIS READS THE REGISTER, and that is the LRM's definition rather than an
    // implementation choice. On a device whose reads have side effects -- a
    // channel CSR that clears its status and interrupt-source bits when read --
    // a masked write has those side effects too. A caller reaching for this
    // instead of read/modify/write is choosing a shorter spelling, not fewer
    // bus transactions.
    //
    // One task covers all four spellings the LRM offers. write_field,
    // write_fields and write_masked are resolved and folded to a (mask, value)
    // constant pair by the compiler, so no field name and no per-register
    // generation reaches here; this stays type-generic.
    //
    // It is also the single place a platform read-modify-write instruction or a
    // byte-enable write would go. §21.14.1 permits that optimisation but does
    // not require it, and nothing above this line would have to change.
    task write_val_masked(data_t mask, data_t val);
      data_t cur;
      read_val(cur);
      write_val((cur & ~mask) | (val & mask));
    endtask

    // ----- Field-wise spellings of the same operation. -----
    // Thin wrappers over write_val_masked, NOT a second primitive: the read
    // stays in exactly one place, and everything but the value folds at
    // elaboration. These take a `reg_field_t` constant rather than a name, so
    // this class still knows nothing about any particular register.
    //
    // The value is given UNSHIFTED -- `write_field(F_prio, 3)` means "prio is
    // 3", not "OR in 0x6000" -- and is truncated to the field, so an over-wide
    // value cannot bleed into a neighbouring field.
    //
    // NOTE on widths: a value argument narrower than data_t is an implicit
    // widening, which Verilator reports as WIDTHEXPAND and treats as fatal by
    // default. Cast at the call site -- `write_field(F_ars, 32'(enable))` --
    // which is what the generated code emits.
    task write_field(reg_field_t f, data_t v);
      write_val_masked(data_t'(f.mask), (v << f.shift) & data_t'(f.mask));
    endtask

    // Several fields of ONE register, in one read-modify-write. Two lists
    // rather than a list of pairs, mirroring PSS's own write_fields(names,
    // values) (§21.14.1). Coalescing is the point: writing the fields one at a
    // time would be N read/write pairs where the device sees one.
    task write_fields(reg_field_t f[], data_t v[]);
      data_t mask = '0;
      data_t val  = '0;
      if (f.size() != v.size()) begin
        $error("write_fields: %0d field(s) but %0d value(s)", f.size(), v.size());
        return;
      end
      foreach (f[i]) begin
        mask |= data_t'(f[i].mask);
        val  |= (v[i] << f[i].shift) & data_t'(f[i].mask);
      end
      write_val_masked(mask, val);
    endtask

    // The output is 64 bits -- the width of reg_field_t.mask -- rather than
    // data_t, so that one fixed width works against every register. Declaring
    // it data_t would make the caller's variable width part of the API, and a
    // natural `bit [31:0]` against a 16-bit register would be a fatal
    // WIDTHTRUNC.
    //
    // The extraction is done at data_t width and widened afterwards, in two
    // steps ON PURPOSE. Written as one expression under a `64'(...)` cast, the
    // cast's width propagates INTO the `&`, so both operands are extended to 64
    // bits and Verilator reports the mismatch as a fatal WIDTHEXPAND.
    task read_field(reg_field_t f, output bit [63:0] v);
      data_t raw;
      data_t val;
      read_val(raw);
      val = (raw & data_t'(f.mask)) >> f.shift;
      v = 64'(val);
    endtask
  endclass

  // ----- PSS sync_pkg::channel_c<Te, DEPTH> (LRM 3.1 §21.9.1). -----
  // A bounded FIFO with blocking and non-blocking access at both ends. Backed
  // by `mailbox`, which is the SV construct that already has these exact
  // semantics: bounded, thread-safe, blocking `get`/`put`, non-blocking
  // `try_get`/`try_put`.
  //
  // DEPTH IS PART OF THE MODEL, not a buffer-tuning knob. `channel_c<T,1>` is a
  // coalescing binary semaphore -- `try_put` failing while an item is pending
  // is the behaviour a wake-up channel relies on -- so the parameter is passed
  // straight to `new()` and never rounded up.
  //
  // NOTE the guard below. `mailbox #(T) m = new(0)` is UNBOUNDED in SV, so a
  // DEPTH that reached here as 0 would produce a channel that never coalesces
  // and never blocks a writer: a behavioural change with no error anywhere. PSS
  // requires a positive depth and the front end rejects 0, so this is the
  // backstop for a channel that arrives by some other path.
  class channel_c #(type Te = bit, int DEPTH = 1);
    localparam int BOUND = (DEPTH < 1) ? 1 : DEPTH;

    protected mailbox #(Te) m_mbx;

    function new();
      m_mbx = new(BOUND);
    endfunction

    // Blocking. Tasks, because they consume time -- and because a target exec
    // blocked here must not stall other execs, which is the whole reason PSS
    // has channels rather than a polling loop (§20.8).
    task get(output Te t);
      m_mbx.get(t);
    endtask

    task put(Te t);
      m_mbx.put(t);
    endtask

    // Non-blocking. Functions, so they are callable from contexts that cannot
    // consume time -- which is what makes `try_put` legal from an interrupt
    // observer or any other simulator thread outside the model's own process.
    //
    // `mailbox::try_get`/`try_put` return an int: > 0 on success, 0 when the
    // operation cannot proceed, and < 0 for a type mismatch (only reachable
    // with an untyped mailbox -- this one is parameterized). PSS specifies a
    // bool, so anything not positive is false.
    function bit try_get(output Te t);
      return (m_mbx.try_get(t) > 0);
    endfunction

    function bit try_put(Te t);
      return (m_mbx.try_put(t) > 0);
    endfunction

    // Not PSS API. For a testbench that needs to see whether a wake is already
    // pending without consuming it -- diagnostics, not stimulus.
    function int num();
      return m_mbx.num();
    endfunction
  endclass

endpackage
