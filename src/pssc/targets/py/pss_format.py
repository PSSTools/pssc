"""PSS string formatting (LRM 21.1.1) for the generated Python module.

ONE source, used twice. The generators import `check_args` to check a
`message()` format when it is lowered -- a bad `%`, a count mismatch or a
specifier the argument's type cannot take is a build error, not a run-time one.
And the text between the two `# ---- inline` markers is copied into every
generated module that formats a message, because that module imports nothing
outside the standard library (see `backend.py`). Everything inside the markers
is therefore standard-library Python with no reference to pssc.

A value is rendered at its DECLARED type, carried beside it as a small tuple:

    ("i", width, signed)       an integer
    ("b",)                     a bool
    ("s",)                     a string
    ("e", ((name, value), ...)) an enum

* `%d` prints the value as signed at its width. Rule c) says an unsigned value
  "is converted to signed type before being formatted"; this takes it at the
  value's own width, as the bc backend does.
* `%u %x %X %o %b %B` print it as unsigned at its width.
* `%n` prints a bool as `true`/`false` and an enum as its item name.
* `%s` prints a string.

Floating-point formats and `%p` are refused: the operation-model family has no
floats or chandles.
"""

# ---- inline: begin
import re as _re

_PSS_SPEC = _re.compile(
    r"%(?P<flags>[-+ #0]*)(?P<width>[0-9]+)?(?:\.(?P<prec>[0-9]*))?"
    r"(?P<conv>[duxXobBnsp%efgEG])?")


def _pss_parse(fmt):
    """`[(start, end, flags, width, prec, conv)]` for each specifier; `%%` is
    literal text. Raises ValueError on a `%` that starts no specifier."""
    out = []
    i = 0
    while True:
        j = fmt.find("%", i)
        if j < 0:
            return out
        m = _PSS_SPEC.match(fmt, j)
        conv = m.group("conv")
        if conv is None:
            raise ValueError(
                "'%%' at offset %d does not start a valid format specifier" % j)
        if conv == "%":
            if m.group("flags") or m.group("width") or m.group("prec") is not None:
                raise ValueError("malformed '%%%%' at offset %d" % j)
            i = m.end()
            continue
        if conv in "efgEGp":
            raise ValueError("%%%s is not supported: this model has no "
                             "floating-point or chandle values" % conv)
        prec = m.group("prec")
        out.append((j, m.end(), m.group("flags"),
                    int(m.group("width")) if m.group("width") else None,
                    (int(prec) if prec else 0) if prec is not None else None,
                    conv))
        i = m.end()


def _pss_pad(width, flags, s):
    if width is None or len(s) >= width:
        return s
    return s.ljust(width) if "-" in flags else s.rjust(width)


def _pss_int(spec, value, width):
    _, _, flags, fwidth, prec, conv = spec
    value &= (1 << width) - 1
    if conv == "d" and value >> (width - 1):
        value -= 1 << width
    neg = value < 0
    mag = -value if neg else value
    digits = {"d": "%d", "u": "%d", "x": "%x", "X": "%X",
              "o": "%o"}.get(conv)
    digits = digits % mag if digits else bin(mag)[2:]
    if prec is not None:
        if prec == 0 and mag == 0:
            digits = ""
        digits = digits.rjust(prec, "0")
    prefix = ""
    if "#" in flags and mag != 0 and conv in "oxXbB":
        prefix = {"o": "0", "x": "0x", "X": "0X", "b": "0b", "B": "0B"}[conv]
    sign = "-" if neg else ("+" if "+" in flags and conv == "d"
                            else (" " if " " in flags and conv == "d" else ""))
    body = prefix + digits
    if fwidth and "0" in flags and "-" not in flags and prec is None:
        body = body.rjust(fwidth - len(sign), "0")
    return _pss_pad(fwidth, flags, sign + body)


def _pss_text(spec, text):
    _, _, flags, fwidth, prec, _ = spec
    if prec is not None:
        text = text[:prec]
    return _pss_pad(fwidth, flags, text)


def _pss_fmt(fmt, *args):
    """`fmt` with each `(value, type)` in `args` rendered at its PSS type."""
    out = []
    pos = 0
    for spec, (value, ty) in zip(_pss_parse(fmt), args):
        out.append(fmt[pos:spec[0]].replace("%%", "%"))
        pos = spec[1]
        conv = spec[5]
        if conv == "n":
            if ty[0] == "b":
                out.append(_pss_text(spec, "true" if value else "false"))
            else:
                name = [n for n, v in ty[1] if v == value]
                out.append(_pss_text(spec, name[0] if name else str(value)))
        elif conv == "s":
            out.append(_pss_text(spec, value))
        elif ty[0] == "i":
            out.append(_pss_int(spec, value, ty[1]))
        elif ty[0] == "b":
            out.append(_pss_int(spec, 1 if value else 0, 1))
        else:
            out.append(_pss_int(spec, value, 32))
    out.append(fmt[pos:].replace("%%", "%"))
    return "".join(out)
# ---- inline: end


def parse_format(fmt: str):
    """The specifiers of ``fmt``; raises ValueError for LRM 21.1.1 rule a)."""
    return _pss_parse(fmt)


def check_args(name: str, fmt: str, vals, type_of):
    """``[(spec, PssType)]`` for ``name(fmt, *vals)``, LRM 21.1.1 a)-c)
    checked: the format parses, there is one argument per specifier, and each
    argument's type is one its conversion can print. ``type_of`` gives an
    argument's `PssType` (or None). Raises ValueError naming the call."""
    try:
        specs = _pss_parse(fmt)
    except ValueError as e:
        raise ValueError(f"{name}({fmt!r}): {e} (LRM 21.1.1 a)") from None
    if len(specs) != len(vals):
        raise ValueError(
            f"{name}({fmt!r}): {len(specs)} format specifier(s) for "
            f"{len(vals)} argument(s) (LRM 21.1.1 b)")
    out = []
    for k, (spec, v) in enumerate(zip(specs, vals), 1):
        conv, t = spec[5], type_of(v)
        where = f"{name}({fmt!r}), argument {k}"
        if t is None:
            raise ValueError(
                f"{where}: cannot determine its PSS type, which %{conv} needs "
                f"to print it")
        ok = {"n": ("bool", "enum"), "s": ("string",)}.get(
            conv, ("int", "bool", "enum"))
        if t.kind not in ok:
            raise ValueError(
                f"{where}: %{conv} cannot format a {t.kind} (LRM 21.1.1 c)")
        out.append((spec, t))
    return out


def inline_source() -> str:
    """The text copied into a generated module, markers excluded."""
    import inspect
    import sys
    src = inspect.getsource(sys.modules[__name__])
    begin = src.index("# ---- inline: begin\n") + len("# ---- inline: begin\n")
    end = src.index("# ---- inline: end")
    return src[begin:end].rstrip() + "\n"
