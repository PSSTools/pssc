"""`message()` formatting in op-model-c and -cpp (LRM 21.1.1).

The generated C and C++ format every integer, bool and enum field themselves
(`targets/c/message_format.py`, with `pssc_fmt_int`/`pssc_enum_name` in the
seam headers) and hand the platform's printf-style function only `%s` and
`%%`. Passing the PSS format through was wrong three ways: `%d` printed at the
host type's width, `%b` is not printf's, and printf's `%n` STORES through its
argument.

The helpers are held to op-model-py's `_pss_fmt` -- itself held to bc's
formatter -- over the same grid of specifiers, widths and values.
"""
from __future__ import annotations

import itertools
import subprocess

import pytest

from pssc.targets.c.message_format import CFormat, CppFormat, _str_spec
from pssc.targets.py import pss_format as pf

from .conftest import (C_CORE_DIR, CPP_CORE_DIR, available_c_compilers,
                       available_cpp_compilers)
from .test_op_model_py_message import _INTS, _SPECS, _VALUES

_ITEMS = (("OFF", 0), ("SLOW", 5), ("NEG", -3))
_ENUM_CASES = [("%n", 5), ("%n", -3), ("%n", 7), ("[%-6n]", 5), ("%.2n", 5),
               ("%8n", 0)]


def _int_cases():
    """``(spec, width, value) -> expected`` for every integer case."""
    for spec, (width, signed), v in itertools.product(_SPECS, _INTS, _VALUES):
        spec = spec.rstrip("|")
        yield spec, width, v & ((1 << width) - 1), \
            pf._pss_fmt(spec, (v, ("i", width, signed)))


def _field_lines(spell, printf):
    """Program lines printing each case, and the lines Python expects."""
    body, want = [], []
    for spec, width, v, expect in _int_cases():
        (_, _, flags, fw, prec, conv), = pf.parse_format(spec)
        bits = sum({"-": 1, "+": 2, " ": 4, "#": 8, "0": 16}[f] for f in flags)
        call = spell.int_field(f"{v}ull", width, conv, bits, fw, prec)
        body.append(f'    {printf}("%s\\n", {call});')
        want.append(expect)
    for fmt, v in _ENUM_CASES:
        (start, end, flags, fw, prec, _), = pf.parse_format(fmt)
        text = fmt[:start] + _str_spec(flags, fw, prec) + fmt[end:]
        call = spell.enum_field(f"{v}", _ITEMS)
        body.append(f'    {printf}("{text}\\n", {call});')
        want.append(pf._pss_fmt(fmt, (v, ("e", _ITEMS))))
    return body, want


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return r.stdout


@pytest.mark.parametrize("cc", available_c_compilers()[:1] or
                         [pytest.param(None, marks=pytest.mark.skip(
                             reason="no C compiler"))])
def test_c_fields_format_as_op_model_py_formats_them(tmp_path, cc):
    body, want = _field_lines(CFormat(), "printf")
    (tmp_path / "t.c").write_text(
        "#include <stdio.h>\n#include \"pssc_env.h\"\n"
        "void pssc_message(const char *fmt, ...) { (void)fmt; }\n"
        "int main(void) {\n" + "\n".join(body) + "\n    return 0;\n}\n")
    _run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", C_CORE_DIR,
          "t.c", "-o", "t"], tmp_path)
    assert _run(["./t"], tmp_path).split("\n")[:-1] == want


@pytest.mark.parametrize("cxx", available_cpp_compilers()[:1] or
                         [pytest.param(None, marks=pytest.mark.skip(
                             reason="no C++ compiler"))])
def test_cpp_fields_format_as_op_model_py_formats_them(tmp_path, cxx):
    body, want = _field_lines(CppFormat(), "std::printf")
    (tmp_path / "t.cpp").write_text(
        "#include <cstdio>\n#include \"pssc_env.hpp\"\n"
        "namespace pssc { void message(const char *, ...) {} }\n"
        "int main() {\n" + "\n".join(body) + "\n    return 0;\n}\n")
    _run([cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I",
          CPP_CORE_DIR, "t.cpp", "-o", "t"], tmp_path)
    assert _run(["./t"], tmp_path).split("\n")[:-1] == want


@pytest.mark.parametrize("cc", available_c_compilers()[:1] or
                         [pytest.param(None, marks=pytest.mark.skip(
                             reason="no C compiler"))])
def test_the_c_helpers_need_no_library(tmp_path, cc):
    """A freestanding part has no printf to lean on; the helpers use none."""
    (tmp_path / "t.c").write_text(
        '#include "pssc_env.h"\n'
        "const char *f(unsigned v) {\n"
        "    return pssc_fmt_int((char[68]){0}, v, 8, 'b', 0, 0, -1);\n}\n")
    _run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
          "-nostdlib", "-I", C_CORE_DIR, "-c", "t.c", "-o", "t.o"], tmp_path)
