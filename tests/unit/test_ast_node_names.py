"""
Every ``pss_ast.<Name>`` this translator mentions must be a class the parser
actually defines.

A name the parser has dropped or renamed is not a dead branch: an
``isinstance(node, pss_ast.Gone)`` raises ``AttributeError`` for *every* node
that reaches it, so a stale branch takes out every construct dispatched after
it. That is how a bare `int n = pkg::CONST;` field came to abort the whole
translation with ``module 'pssparser.ast' has no attribute 'ExprSubscript'`` --
the static-reference branch sat below the stale one and was never reached.

This is a source scan rather than a behavioural test on purpose: it fails on
the branch that is never exercised, which is exactly the branch that rots.
"""
import pathlib
import re

import pssparser.ast as pss_ast

import pssc

_REF = re.compile(r'\bpss_ast\.([A-Za-z_][A-Za-z0-9_]*)')


def test_every_referenced_parser_ast_class_exists():
    src_root = pathlib.Path(pssc.__file__).parent
    defined = set(dir(pss_ast))

    missing = {}
    for path in sorted(src_root.rglob("*.py")):
        for match in _REF.finditer(path.read_text(errors="ignore")):
            name = match.group(1)
            if name not in defined:
                missing.setdefault(name, set()).add(path.name)

    assert not missing, (
        "pssparser.ast does not define: "
        + ", ".join(f"{n} (in {', '.join(sorted(f))})"
                    for n, f in sorted(missing.items())))
