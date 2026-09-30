"""`exec file` parses natively and is refused where it is (detox C2, O6).

The ``exec file "name" = <triple-quoted-template>;`` form is grammar-valid in
pssparser (``target_file_exec_block``). The old ``_strip_exec_file_blocks``
source rewrite (which deleted it before parsing) is gone; the construct flows
through ``parse -> link -> translate``. While pssparser's builder was a stub no
node reached ast2ir and it was ignored; the builder now produces an
``ExecTargetTemplateBlock``, which no target lowers, so it is refused with its
line rather than dropped -- a file the user asked for would otherwise be
missing without a word.
"""
from __future__ import annotations
import os
import tempfile
import pytest

from pssc import Parser, AstToIrTranslator

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_TQ = '"""'

EXEC_FILE_PSS = """\
component pss_top {
    action A {
        rand bit[8] x;
        exec file "out.txt" = %sgenerated %s;
    }
}
""" % (_TQ, _TQ)


def _translate(src: str):
    with tempfile.NamedTemporaryFile(suffix='.pss', mode='w', delete=False) as f:
        f.write(src)
        fname = f.name
    try:
        p = Parser()
        p.parse([fname])
        root = p.link()
        return AstToIrTranslator().translate(root)
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def test_exec_file_parses_and_is_refused_where_it_is():
    """It parses and links; translation names it, and its line, as
    unsupported. The rest of the action still reaches the IR."""
    ctx = _translate(EXEC_FILE_PSS)
    assert ctx.errors == ["line 4: a target-template exec block in action "
                          "'pss_top::A' is not supported yet"], ctx.errors
    action = ctx.type_map.get("pss_top::A") or ctx.type_map.get("A")
    assert action is not None
    # `x` is a real field; `exec file` contributes nothing.
    assert "x" in [f.name for f in action.fields]
