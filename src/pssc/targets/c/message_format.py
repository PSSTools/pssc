"""`message(verbosity, fmt, args...)` for C and C++ (LRM 21.1.1).

A PSS format is not a printf format. `%d` prints a value at its PSS width
(an unsigned one converted to signed, rule c), `%b` exists, and `%n` prints a
bool as `true`/`false` and an enum as its item name -- where printf's `%n`
STORES the character count through a pointer argument, so passing the format
through wrote to the address an enum's value happened to be.

So every integer, bool and enum field is formatted by the generated code
itself, with the seam's `pssc_fmt_int`/`pssc_enum_name` (C, `pssc_env.h`) or
`pssc::fmt_int`/`pssc::enum_name` (C++, `pssc_env.hpp`), and the platform's
printf-style function receives a format holding only `%s` and `%%`. A string
argument's `%s` keeps its field width, precision and `-`, which printf reads
as PSS does. The output is held to op-model-py's `_pss_fmt`, which is held to
bc's formatter.
"""
from __future__ import annotations

from typing import List

from ..py.pss_format import check_args

__all__ = ["CFormat", "CppFormat", "lower_format"]

_FLAG_BITS = {"-": 1, "+": 2, " ": 4, "#": 8, "0": 16}


def _flag_bits(flags: str) -> int:
    bits = 0
    for f in flags:
        bits |= _FLAG_BITS[f]
    return bits


def _int_buf(fwidth, prec) -> int:
    """Bytes a formatted integer field needs: a sign, a prefix, 64 digits,
    the field width and the precision, and the NUL."""
    return max(fwidth or 0, (prec if prec is not None else 0) + 3, 67) + 1


def _str_spec(flags: str, fwidth, prec) -> str:
    """`%s` with what printf and PSS both read of a text field."""
    return ("%" + ("-" if "-" in flags else "")
            + (str(fwidth) if fwidth is not None else "")
            + (f".{prec}" if prec is not None else "") + "s")


class CFormat:
    """C's spelling of a formatted field."""

    def int_field(self, x: str, width: int, conv: str, flags: int,
                  fwidth, prec) -> str:
        return (f"pssc_fmt_int((char[{_int_buf(fwidth, prec)}]){{0}}, "
                f"(unsigned long long)({x}), {width}, '{conv}', {flags}, "
                f"{fwidth or 0}, {prec if prec is not None else -1})")

    def enum_field(self, x: str, items) -> str:
        values = ", ".join(f"{v}" for _, v in items)
        names = ", ".join(f'"{n}"' for n, _ in items)
        return (f"pssc_enum_name((char[21]){{0}}, (long long)({x}), "
                f"{len(items)}, (const long long[]){{{values}}}, "
                f"(const char *const[]){{{names}}})")


class CppFormat:
    """C++'s spelling of a formatted field."""

    def int_field(self, x: str, width: int, conv: str, flags: int,
                  fwidth, prec) -> str:
        return (f"pssc::fmt_int<{_int_buf(fwidth, prec)}>("
                f"static_cast<unsigned long long>({x}), {width}, '{conv}', "
                f"{flags}, {fwidth or 0}, "
                f"{prec if prec is not None else -1}).s")

    def enum_field(self, x: str, items) -> str:
        n = max([21] + [len(k) + 1 for k, _ in items])
        pairs = ", ".join(f'{{{v}, "{k}"}}' for k, v in items)
        return (f"pssc::enum_name<{n}>(static_cast<long long>({x}), "
                f"{{{pairs}}}).s")


def lower_format(name: str, fmt: str, vals, types, render, spell,
                 literal) -> List[str]:
    """The arguments of the platform call for ``name(fmt, *vals)``: the
    rewritten format as a literal, then one argument per field. ``render``
    spells an expression, ``spell`` is a `CFormat`/`CppFormat` and
    ``literal`` makes a string literal. Raises ValueError on a format LRM
    21.1.1 refuses (`check_args`)."""
    checked = check_args(name, fmt, vals, types.type_of)
    out, args, pos = [], [], 0
    for ((start, end, flags, fwidth, prec, conv), t), v in zip(checked, vals):
        out.append(fmt[pos:start])
        pos = end
        x = render(v)
        if conv == "s":
            out.append(_str_spec(flags, fwidth, prec))
            args.append(x)
        elif conv == "n":
            out.append(_str_spec(flags, fwidth, prec))
            args.append(f'(({x}) ? "true" : "false")' if t.kind == "bool"
                        else spell.enum_field(x, t.items))
        else:
            # A bool is a one-bit value and an enum a 32-bit one, as
            # `_pss_fmt` reads them.
            width = (1 if t.kind == "bool" else 32 if t.kind == "enum"
                     else t.width)
            out.append("%s")
            args.append(spell.int_field(x, width, conv, _flag_bits(flags),
                                        fwidth, prec))
    out.append(fmt[pos:])
    return [literal("".join(out))] + args
