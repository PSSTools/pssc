/* pssc_env.h -- the non-memory part of the platform seam. Hand-written once.
 *
 * `pssc_mem.h` covers how the generated code reaches the DEVICE. This covers
 * the little it needs from the ENVIRONMENT: today, a way to say something.
 *
 * WHY THIS IS SEPARATE FROM pssc_mem.h. Memory access is required by every
 * generated model and is the thing the link style varies. What is here is
 * optional, is not performance-critical, and is what a firmware integrator is
 * most likely to want to redirect at a real logging facility. Mixing the two
 * would make the required seam look bigger than it is.
 *
 * WHAT THE PLATFORM MUST SUPPLY
 * -----------------------------
 *
 *   void pssc_message(const char *fmt, ...);
 *
 * printf-style. The generated code calls it for `message(...)` in a PSS exec
 * body, and (unless `--match-default none`) for an unmatched `match`. The
 * formats it passes contain only `%s` and `%%`: every number and enum name is
 * formatted before the call (see PSS FORMATTING below), so a minimal printf
 * suffices.
 *
 * The PSS `message(verbosity, fmt, ...)` VERBOSITY ARGUMENT IS DROPPED, exactly
 * as the SystemVerilog projection drops it. PSS verbosity levels are a
 * simulation-runtime concept with no counterpart here, and inventing a mapping
 * would give the platform a number it has no basis for interpreting. Filter by
 * content, or generate with `--message-style none` and pay nothing.
 *
 * IF YOU WANT NONE OF THIS: generate with `--message-style none`. That drops
 * the calls AND their format strings, so nothing needs to be implemented and
 * no `.rodata` is spent -- which on a part with kilobytes of it is the point.
 *
 * A weak empty definition can be emitted with `--emit-stubs`, so a link
 * succeeds before the platform side exists. Note that no memory primitive is
 * ever stubbed: a silently-succeeding bus access would be a driver that
 * reports the device is fine because it never talked to it.
 */
#ifndef PSSC_ENV_H
#define PSSC_ENV_H

#ifdef __cplusplus
extern "C" {
#endif

/* Implemented by the platform. Declared here so a generated body that calls it
 * is a compile error when it is missing, rather than an implicit declaration
 * that links to whatever `pssc_message` happens to be in scope. */
void pssc_message(const char *fmt, ...);

/* Marks a point the model says cannot be reached (`--match-default
 * unreachable`). Deliberately NOT a no-op by default: an unmatched `match` is
 * an error in PSS (3.1 §22.7.9), so silence here would convert a stated
 * impossibility into a silent fall-through. Override before including the
 * generated header if the platform has a fault handler worth calling. */
#ifndef PSSC_UNREACHABLE
#  if defined(__GNUC__) || defined(__clang__)
#    define PSSC_UNREACHABLE() __builtin_unreachable()
#  else
#    define PSSC_UNREACHABLE() ((void)0)
#  endif
#endif

/* On a definition that may legitimately have no caller in its translation
 * unit. The generated register accessors are the case: the set is derived from
 * the register map, so it covers registers no operation in this model happens
 * to touch, and when they live in the .c (the default layout) that is a
 * translation-unit-local `static inline` nobody calls -- which clang reports
 * under -Wunused-function. The accessors are the API's vocabulary for the
 * device, not a list of what today's operations use, so pruning them to the
 * called set would make a model edit silently remove a symbol. */
#ifndef PSSC_MAYBE_UNUSED
#  if defined(__GNUC__) || defined(__clang__)
#    define PSSC_MAYBE_UNUSED __attribute__((unused))
#  else
#    define PSSC_MAYBE_UNUSED
#  endif
#endif

/* PSS FORMATTING (LRM 21.1.1). A PSS format is not a printf format: `%d`
 * prints a value at its PSS width (an unsigned value converted to signed, rule
 * c), `%b` exists, and `%n` prints an enum's item name -- where printf's `%n`
 * STORES through a pointer argument. So the generated code formats every
 * integer and enum field itself, with the two functions below, and hands
 * `pssc_message` a format containing only `%s` and `%%`. That is the whole
 * contract on the platform's printf: no length modifiers, no `%lld` (which
 * newlib-nano lacks), nothing locale-dependent. No libc is used here.
 *
 * Each writes into BUF, which the call site supplies (a compound literal, sized
 * for the field when the model is generated) and returns BUF. They are the C
 * twins of `_pss_int` and the `%n` arm of `_pss_fmt` in pssc's
 * `targets/py/pss_format.py`, and are tested against them. */

/* FLAGS of a conversion, as pssc_fmt_int takes them. */
#define PSSC_FMT_MINUS 1
#define PSSC_FMT_PLUS  2
#define PSSC_FMT_SPACE 4
#define PSSC_FMT_HASH  8
#define PSSC_FMT_ZERO  16

/* V, read at WIDTH bits (1..64), formatted by CONV (one of "duxXobB") with
 * FLAGS, field width FWIDTH (0: none) and precision PREC (-1: none). BUF holds
 * max(FWIDTH, PREC + 3, 67) + 1 bytes: a sign, a prefix, 64 digits. */
PSSC_MAYBE_UNUSED static inline const char *pssc_fmt_int(
        char *buf, unsigned long long v, int width, char conv, int flags,
        int fwidth, int prec) {
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
        if (mag == 0) mag = 1ull << (width - 1);  /* the most negative value */
    } else if (conv == 'd' && (flags & PSSC_FMT_PLUS)) {
        sign = '+';
    } else if (conv == 'd' && (flags & PSSC_FMT_SPACE)) {
        sign = ' ';
    }
    if ((flags & PSSC_FMT_HASH) && mag != 0 && base != 10u)
        prefix = conv == 'o' ? "0" : conv == 'x' ? "0x" : conv == 'X' ? "0X"
               : conv == 'b' ? "0b" : "0B";
    if (!(prec == 0 && mag == 0))
        do { digits[nd++] = alpha[mag % base]; mag /= base; } while (mag);
    for (nprefix = 0; prefix[nprefix]; nprefix++) ;
    ndig = nd > prec ? nd : prec;
    len = (sign ? 1 : 0) + nprefix + ndig;
    /* `0` pads in front of the prefix, as `_pss_int` (and bc) do. */
    if ((flags & PSSC_FMT_ZERO) && !(flags & PSSC_FMT_MINUS) && prec < 0
            && fwidth > len) {
        zeros = fwidth - len;
        len = fwidth;
    }
    if (!(flags & PSSC_FMT_MINUS)) for (; len < fwidth; len++) buf[o++] = ' ';
    if (sign) buf[o++] = sign;
    for (i = 0; i < zeros; i++) buf[o++] = '0';
    for (i = 0; i < nprefix; i++) buf[o++] = prefix[i];
    for (i = nd; i < ndig; i++) buf[o++] = '0';
    while (nd) buf[o++] = digits[--nd];
    if (flags & PSSC_FMT_MINUS) for (; len < fwidth; len++) buf[o++] = ' ';
    buf[o] = 0;
    return buf;
}

/* The name of the item of value V among the N items (VALUES[i], NAMES[i]) of
 * an enum, or V in decimal if it is none of them. BUF holds 21 bytes. */
PSSC_MAYBE_UNUSED static inline const char *pssc_enum_name(
        char *buf, long long v, int n, const long long *values,
        const char *const *names) {
    int i;
    for (i = 0; i < n; i++)
        if (values[i] == v) return names[i];
    return pssc_fmt_int(buf, (unsigned long long)v, 64, 'd', 0, 0, -1);
}

#ifdef __cplusplus
}
#endif

#endif /* PSSC_ENV_H */
