// pssc_env.hpp -- the non-memory part of the platform seam. Hand-written once.
//
// `pssc_reg.hpp` covers how the generated code reaches the DEVICE. This covers
// the little it needs from the ENVIRONMENT: today, a way to say something.
// Separate from the memory seam for the same reason as in C -- memory access is
// required by every generated model, and this is optional, so mixing them would
// make the required seam look bigger than it is.
//
// WHAT THE PLATFORM MUST SUPPLY
// -----------------------------
//
//     void pssc::message(const char *fmt, ...);
//
// printf-style. The generated code calls it for `message(...)` in a PSS exec
// body, and (unless `--match-default none`) for an unmatched `match`. The
// formats it passes contain only `%s` and `%%`: every number and enum name is
// formatted before the call (see PSS FORMATTING below).
//
// THE PSS `message(verbosity, fmt, ...)` VERBOSITY ARGUMENT IS DROPPED, exactly
// as the SystemVerilog and C projections drop it. PSS verbosity levels are a
// simulation-runtime concept with no counterpart here, and inventing a mapping
// would hand the platform a number it has no basis for interpreting.
//
// IF YOU WANT NONE OF THIS: generate with `--message-style none`, which drops
// the calls and their format strings, so nothing needs implementing.
#ifndef PSSC_ENV_HPP
#define PSSC_ENV_HPP

#include <initializer_list>

namespace pssc {

// Implemented by the platform. DECLARED here so that a generated body calling
// it fails to link with this name when it is missing, rather than resolving to
// whatever else is in scope.
void message(const char *fmt, ...);

// PSS FORMATTING (LRM 21.1.1). A PSS format is not a printf format: `%d`
// prints a value at its PSS width (an unsigned value converted to signed, rule
// c), `%b` exists, and `%n` prints an enum's item name -- where printf's `%n`
// STORES through a pointer argument. So the generated code formats every
// integer and enum field itself, with the two functions below, and hands
// `pssc::message` a format containing only `%s` and `%%`. No library is used.
//
// Each returns a buffer of N chars by value; the call site takes `.s`, which
// lives to the end of the full expression -- the `message` call. They are the
// twins of `pssc_fmt_int`/`pssc_enum_name` in the C seam (`pssc_env.h`) and of
// `_pss_int` in pssc's `targets/py/pss_format.py`, and are tested against it.

template <int N> struct fmt_buf { char s[N]; };

// FLAGS of a conversion.
enum : int { FMT_MINUS = 1, FMT_PLUS = 2, FMT_SPACE = 4, FMT_HASH = 8,
             FMT_ZERO = 16 };

// V, read at WIDTH bits (1..64), formatted by CONV (one of "duxXobB") with
// FLAGS, field width FWIDTH (0: none) and precision PREC (-1: none). N is at
// least max(FWIDTH, PREC + 3, 67) + 1: a sign, a prefix, 64 digits.
template <int N>
inline fmt_buf<N> fmt_int(unsigned long long v, int width, char conv,
                          int flags, int fwidth, int prec) {
    fmt_buf<N> out{};
    char *buf = out.s;
    char digits[64];
    const char *prefix = "";
    char sign = 0;
    unsigned long long mask = width >= 64 ? ~0ull : ((1ull << width) - 1);
    unsigned long long mag;
    unsigned base = (conv == 'x' || conv == 'X') ? 16u
                  : conv == 'o' ? 8u : (conv == 'b' || conv == 'B') ? 2u : 10u;
    const char *alpha = conv == 'X' ? "0123456789ABCDEF" : "0123456789abcdef";
    int nd = 0, nprefix, ndig, zeros = 0, len, i, o = 0;
    v &= mask;
    mag = v;
    if (conv == 'd' && ((v >> (width - 1)) & 1u)) {
        sign = '-';
        mag = (~v + 1u) & mask;
        if (mag == 0) mag = 1ull << (width - 1);  // the most negative value
    } else if (conv == 'd' && (flags & FMT_PLUS)) {
        sign = '+';
    } else if (conv == 'd' && (flags & FMT_SPACE)) {
        sign = ' ';
    }
    if ((flags & FMT_HASH) && mag != 0 && base != 10u)
        prefix = conv == 'o' ? "0" : conv == 'x' ? "0x" : conv == 'X' ? "0X"
               : conv == 'b' ? "0b" : "0B";
    if (!(prec == 0 && mag == 0))
        do { digits[nd++] = alpha[mag % base]; mag /= base; } while (mag);
    for (nprefix = 0; prefix[nprefix]; nprefix++) {}
    ndig = nd > prec ? nd : prec;
    len = (sign ? 1 : 0) + nprefix + ndig;
    // `0` pads in front of the prefix, as `_pss_int` (and bc) do.
    if ((flags & FMT_ZERO) && !(flags & FMT_MINUS) && prec < 0
            && fwidth > len) {
        zeros = fwidth - len;
        len = fwidth;
    }
    if (!(flags & FMT_MINUS)) for (; len < fwidth; len++) buf[o++] = ' ';
    if (sign) buf[o++] = sign;
    for (i = 0; i < zeros; i++) buf[o++] = '0';
    for (i = 0; i < nprefix; i++) buf[o++] = prefix[i];
    for (i = nd; i < ndig; i++) buf[o++] = '0';
    while (nd) buf[o++] = digits[--nd];
    if (flags & FMT_MINUS) for (; len < fwidth; len++) buf[o++] = ' ';
    buf[o] = 0;
    return out;
}

struct enum_item { long long value; const char *name; };

// The name of the item of value V among ITEMS, or V in decimal if it is none
// of them. N is at least 21 and one more than the longest name.
template <int N>
inline fmt_buf<N> enum_name(long long v, std::initializer_list<enum_item> items) {
    for (const enum_item &it : items) {
        if (it.value == v) {
            fmt_buf<N> out{};
            for (int i = 0; it.name[i] && i < N - 1; i++) out.s[i] = it.name[i];
            return out;
        }
    }
    return fmt_int<N>(static_cast<unsigned long long>(v), 64, 'd', 0, 0, -1);
}

}  // namespace pssc

// Marks a point the model says cannot be reached (`--match-default
// unreachable`). Deliberately NOT a no-op by default: an unmatched `match` is
// an error in PSS (3.1 §22.7.9), so silence here would convert a stated
// impossibility into a silent fall-through. Override before including the
// generated header if the platform has a fault handler worth calling.
#ifndef PSSC_UNREACHABLE
#  if defined(__GNUC__) || defined(__clang__)
#    define PSSC_UNREACHABLE() __builtin_unreachable()
#  else
#    define PSSC_UNREACHABLE() ((void)0)
#  endif
#endif

#endif  // PSSC_ENV_HPP
