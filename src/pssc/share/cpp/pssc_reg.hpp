// pssc_reg.hpp -- core runtime for the cpp-progseq backend. Hand-written once.
//
// Provides the memory-access seam (pssc::mem_if, which the user subclasses), the
// register-access mode enum, the reg<T,ACC> template (1:1 with SystemVerilog's
// reg_c #(T,ACC)), and a stock concrete seam for bare-metal MMIO (pssc::mmio_mem).
//
// Reads return by value (T v = csr.read();) and writes take the value -- no
// output arguments, no status outs. The value<->raw round-trip is a byte copy of
// the value-union, which the generated value structs are (trivially copyable).
//
// Design: docs/design/pss-c-cpp-progseq-gen-design.md (§4.2).
#ifndef PSSC_REG_HPP
#define PSSC_REG_HPP

#include <cstdint>
#include <cstring>
#include <type_traits>

namespace pssc {

using addr_t = std::uint64_t;             // PSS addr_handle_t

// THE seam -- the user subclasses this and overrides the primitives actually
// exercised by the register model (commonly just read32/write32).
struct mem_if {
    virtual ~mem_if() = default;
    virtual void          write8 (addr_t, std::uint8_t ) = 0;
    virtual std::uint8_t  read8  (addr_t)                = 0;
    virtual void          write16(addr_t, std::uint16_t) = 0;
    virtual std::uint16_t read16 (addr_t)                = 0;
    virtual void          write32(addr_t, std::uint32_t) = 0;
    virtual std::uint32_t read32 (addr_t)                = 0;
    virtual void          write64(addr_t, std::uint64_t) = 0;
    virtual std::uint64_t read64 (addr_t)                = 0;
};

enum class access { rw, ro, wo };

namespace detail {
template <class R, class T> inline R to_raw(const T &v) {
    R r = 0; std::memcpy(&r, &v, sizeof(T)); return r;
}
template <class R, class T> inline T from_raw(R raw) {
    T v; std::memcpy(&v, &raw, sizeof(T)); return v;
}
}  // namespace detail

// Generic register handle, parameterized by value type. Selects the bus
// primitive from the value width; all widths fold at compile time.
template <class T, access ACC = access::rw>
class reg {
    // A POINTER, not a reference, and the difference is load-bearing: a
    // reference member makes this class non-assignable, and a register group
    // has to be REBINDABLE. `regs.set_handle(h)` in a PSS constructor binds a
    // group at a handle the model computes, and a sub-component's handle is
    // computed by its parent's constructor body -- which runs after the
    // sub-component (and so its register members) already exists. Binding at
    // construction alone would work only for a single-component model.
    //
    // Never null: the only constructor takes a reference.
    mem_if *bus_;
    addr_t  addr_;
    static constexpr unsigned W = sizeof(T) * 8;
public:
    reg(mem_if &bus, addr_t addr) : bus_(&bus), addr_(addr) {}
    addr_t addr() const { return addr_; }

    T read() const {
        if constexpr (W <= 8)       return detail::from_raw<std::uint8_t,  T>(bus_->read8 (addr_));
        else if constexpr (W <= 16) return detail::from_raw<std::uint16_t, T>(bus_->read16(addr_));
        else if constexpr (W <= 32) return detail::from_raw<std::uint32_t, T>(bus_->read32(addr_));
        else                        return detail::from_raw<std::uint64_t, T>(bus_->read64(addr_));
    }
    void write(T v) {
        if constexpr (W <= 8)       bus_->write8 (addr_, detail::to_raw<std::uint8_t,  T>(v));
        else if constexpr (W <= 16) bus_->write16(addr_, detail::to_raw<std::uint16_t, T>(v));
        else if constexpr (W <= 32) bus_->write32(addr_, detail::to_raw<std::uint32_t, T>(v));
        else                        bus_->write64(addr_, detail::to_raw<std::uint64_t, T>(v));
    }

    // Raw accessors. read()/write() are typed; the masked form works in bits,
    // so it needs the untyped pair underneath it.
    using raw_t = std::conditional_t<(W <=  8), std::uint8_t,
                  std::conditional_t<(W <= 16), std::uint16_t,
                  std::conditional_t<(W <= 32), std::uint32_t, std::uint64_t>>>;

    raw_t read_val() const {
        if constexpr (W <= 8)       return bus_->read8 (addr_);
        else if constexpr (W <= 16) return bus_->read16(addr_);
        else if constexpr (W <= 32) return bus_->read32(addr_);
        else                        return bus_->read64(addr_);
    }
    void write_val(raw_t v) {
        if constexpr (W <= 8)       bus_->write8 (addr_, v);
        else if constexpr (W <= 16) bus_->write16(addr_, v);
        else if constexpr (W <= 32) bus_->write32(addr_, v);
        else                        bus_->write64(addr_, v);
    }

    // Masked write -- PSS 3.1 §21.14.1:
    //
    //     REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)
    //
    // THIS READS THE REGISTER, and that is the LRM's definition rather than an
    // implementation choice: on a register whose read has side effects -- a
    // channel CSR that clears its status and interrupt-source bits -- a masked
    // write has them too.
    //
    // One method covers all four spellings the LRM offers. write_field,
    // write_fields and write_masked are resolved and folded to a (mask, value)
    // constant pair by the compiler, so no field name and no per-register
    // generation reaches here.
    void write_val_masked(raw_t mask, raw_t val) {
        write_val(static_cast<raw_t>((read_val() & ~mask) | (val & mask)));
    }
};

// Stock concrete seam for bare-metal MMIO: volatile load/store against a real
// CPU address (the C++ analogue of C --link-style mmio). Shipped, not generated.
struct mmio_mem : mem_if {
    void          write8 (addr_t a, std::uint8_t  d) override { *reinterpret_cast<volatile std::uint8_t  *>(a) = d; }
    std::uint8_t  read8  (addr_t a)                  override { return *reinterpret_cast<volatile std::uint8_t  *>(a); }
    void          write16(addr_t a, std::uint16_t d) override { *reinterpret_cast<volatile std::uint16_t *>(a) = d; }
    std::uint16_t read16 (addr_t a)                  override { return *reinterpret_cast<volatile std::uint16_t *>(a); }
    void          write32(addr_t a, std::uint32_t d) override { *reinterpret_cast<volatile std::uint32_t *>(a) = d; }
    std::uint32_t read32 (addr_t a)                  override { return *reinterpret_cast<volatile std::uint32_t *>(a); }
    void          write64(addr_t a, std::uint64_t d) override { *reinterpret_cast<volatile std::uint64_t *>(a) = d; }
    std::uint64_t read64 (addr_t a)                  override { return *reinterpret_cast<volatile std::uint64_t *>(a); }
};

}  // namespace pssc

#endif  // PSSC_REG_HPP
