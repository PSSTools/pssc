"""`c_names.check_identifier` and the one C keyword list (`C_KEYWORDS`)."""
from __future__ import annotations

import pytest

from pssc.diag import Refusal
from pssc.targets.c import c_names
from pssc.targets.c.lower_progseq import mangle


@pytest.mark.parametrize("name", ["x", "_pss_reg", "_self_1", "a9", "_"])
def test_an_identifier_c_lets_the_code_declare_passes(name):
    assert c_names.check_identifier(name) == name


@pytest.mark.parametrize("name,why", [
    ("while", "C keyword"),
    ("_Bool", "C keyword"),
    ("constexpr", "C keyword"),
    ("__tmp", "reserved by C"),
    ("_Tmp", "reserved by C"),
    ("_self", "takes for itself"),
    ("_bus", "takes for itself"),
    ("9a", "not a C identifier"),
    ("a-b", "not a C identifier"),
    ("", "not a C identifier"),
])
def test_a_name_c_will_not_take_is_refused(name, why):
    with pytest.raises(Refusal, match=why):
        c_names.check_identifier(name)


def test_the_targets_reserved_names_are_refused():
    assert c_names.check_identifier("regs") == "regs"
    with pytest.raises(Refusal, match="reserved by the target"):
        c_names.check_identifier("regs", reserved=("regs", "dev"))


def test_the_refusal_is_located_at_the_node():
    node = object()
    with pytest.raises(Refusal) as ei:
        c_names.check_identifier("int", node)
    assert ei.value.nodes == (node,)


def test_mangle_renames_every_keyword_check_identifier_refuses():
    for kw in c_names.C_KEYWORDS:
        assert mangle(kw) == kw + "_"
        if not kw.startswith("_"):      # `_Bool_` is still C's
            c_names.check_identifier(mangle(kw))
