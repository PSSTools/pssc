"""Tests for how a PSS syntax error reaches the user.

Two properties, and they are separable:

  1. A syntax error is a USER error. `driver.translate` raises `CompileError`,
     the CLI prints it and exits 1 -- no traceback, and not the exit-2
     "internal error" path that `docs/cli.md` reserves for a compiler bug.
     The parser's own API is unchanged: `Parser`/`load_pss` still raise
     `ParseException` (see `test_load_pss_errors.py`).

  2. The diagnostic is actionable: `file:line:col: severity: message`, a source
     line, a caret under the offending token, and -- where pssc recognises the
     mistake -- a note saying what to write instead.
"""
import re

import pytest

from pssc import Parser, ParseException, driver
from pssc import cli, diag


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


_TOP = "component pss_top {\n}\n"
_ACTION = """\
extend component pss_top {
    action entry_a {
        exec body {
            message(LOW, "Hello World!");
        }
    }
}
"""


# ---------------------------------------------------------------------------
# 1 -- a syntax error is a user error, not an internal one
# ---------------------------------------------------------------------------

def test_syntax_error_raises_compile_error(tmp_path):
    """translate() converts ParseException into the user-facing error type."""
    src = _write(tmp_path, "bad.pss", "component c { action a { rand bit[8 x; } }")
    with pytest.raises(driver.CompileError) as exc:
        driver.translate([src])
    assert exc.value.errors, "the markers must survive the conversion"


def test_parser_api_still_raises_parse_exception(tmp_path):
    """The conversion is the DRIVER's, not the parser's: load_pss is unaffected."""
    with pytest.raises(ParseException):
        Parser().parses([("t.pss", "struct S { rand bit[8 x; }")])


def test_cli_exits_1_without_a_traceback(tmp_path, capsys):
    """A user's typo must not be reported as `pssc: internal error` (exit 2)."""
    src = _write(tmp_path, "bad.pss", "component c { action a { rand bit[8 x; } }")
    rc = cli.main(["parse", src])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Traceback" not in err
    assert "internal error" not in err
    assert "pssc: error:" in err


def test_compile_reports_parse_failure_in_the_result(tmp_path):
    """`raise_on_error=False` promises errors in the result -- including these."""
    src = _write(tmp_path, "bad.pss", "component c { action a { rand bit[8 x; } }")
    res = driver.compile([src], target="sv-native", output_dir=str(tmp_path),
                         raise_on_error=False)
    assert not res.ok and res.errors


# ---------------------------------------------------------------------------
# 2 -- the diagnostic carries a location, a caret and (where known) a fix
# ---------------------------------------------------------------------------

def test_diagnostic_leads_with_file_line_col(tmp_path):
    """`file:line:col:` first -- the form every editor and log filter parses."""
    src = _write(tmp_path, "bad.pss",
                 "component c {\n    action a { rand bit[8 x; }\n}\n")
    with pytest.raises(driver.CompileError) as exc:
        driver.translate([src])
    head = exc.value.errors[0].split("\n")[0]
    assert head.startswith(f"{src}:2:")
    assert ": error: " in head


def test_diagnostic_carries_a_caret_under_the_token(tmp_path):
    src = _write(tmp_path, "bad.pss",
                 "component c {\n    action a { rand bit[8 x; }\n}\n")
    with pytest.raises(driver.CompileError) as exc:
        driver.translate([src])
    lines = exc.value.errors[0].split("\n")
    assert len(lines) >= 3
    caret = lines[2]
    assert "^" in caret
    # The caret column must agree with the column the header reports.
    col = int(re.search(r":(\d+):(\d+): error:", lines[0]).group(2))
    assert caret.index("^") == len(diag._SNIPPET_INDENT) + col - 1


def test_export_action_keyword_is_named_and_fixed(tmp_path):
    """`export action X();` -- the mistake pssc can recognise, so it says so."""
    files = [_write(tmp_path, "pss_top.pss", _TOP),
             _write(tmp_path, "entry_a.pss", _ACTION),
             _write(tmp_path, "exp.pss", "export action pss_top::entry_a();\n")]
    with pytest.raises(driver.CompileError) as exc:
        driver.translate(files)
    text = "\n".join(exc.value.errors)
    assert "'export action' is not PSS syntax" in text
    assert "note:" in text and "export <action_type>(<params>);" in text
    assert "20.10" in text


def test_export_without_parameter_list_is_named_and_fixed(tmp_path):
    """The other half of the same mistake: the parameter list is not optional."""
    files = [_write(tmp_path, "pss_top.pss", _TOP),
             _write(tmp_path, "entry_a.pss", _ACTION),
             _write(tmp_path, "exp.pss", "export pss_top::entry_a;\n")]
    with pytest.raises(driver.CompileError) as exc:
        driver.translate(files)
    text = "\n".join(exc.value.errors)
    assert "parameter list" in text
    assert "write 'pss_top::entry_a();'" in text


def test_the_correct_export_form_parses(tmp_path):
    """The guard must not reject what the LRM permits."""
    files = [_write(tmp_path, "pss_top.pss", _TOP),
             _write(tmp_path, "entry_a.pss", _ACTION),
             _write(tmp_path, "exp.pss", "export pss_top::entry_a();\n")]
    driver.translate(files)    # must not raise


def test_exported_function_is_left_alone(tmp_path):
    """`export target function f;` is an exported FUNCTION (20.9), not an action."""
    src = _write(tmp_path, "p.pss",
                 "package p {\n    export target function f;\n}\n")
    driver.translate([src])    # must not raise


def test_export_in_a_comment_is_not_diagnosed(tmp_path):
    """The guard scans source, so it must skip comments and strings."""
    files = [_write(tmp_path, "pss_top.pss", _TOP),
             _write(tmp_path, "entry_a.pss", _ACTION),
             _write(tmp_path, "c.pss",
                    "// export action pss_top::entry_a();\n"
                    "/* export action pss_top::entry_a(); */\n")]
    driver.translate(files)    # must not raise


# ---------------------------------------------------------------------------
# The renderer itself
# ---------------------------------------------------------------------------

def test_caret_is_placed_against_tab_expanded_text():
    """A tab-indented line must still put the caret under its token."""
    m = diag.marker("boom", file="t.pss", line=1, col=2, extent=3)
    out = diag.format_marker(m, sources={"t.pss": "\tabcdef"})
    body, caret = out.split("\n")[1], out.split("\n")[2]
    assert caret.index("^") == body.index("a")


def test_marker_without_a_source_still_renders():
    """No file on disk and no source text: header only, never a crash."""
    m = diag.marker("boom", file="nowhere.pss", line=3, col=5)
    assert diag.format_marker(m) == "nowhere.pss:3:5: error: boom"


def test_indent_block_indents_every_line():
    """Indenting only the first line would shift the caret off its token."""
    assert diag.indent_block("a\nb", "  ") == "  a\n  b"
