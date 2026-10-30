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


_PSS = """
component pss_top {
    function int f(int _pss_t, int goto) {
        int _pss_t_1 = 1;
        foreach (a[i]) { _pss_t_1 += i; }
        return _pss_t + _pss_t_1;
    }
    int a[4];
}
"""


def _emitter(tmp_path):
    from pssc import driver
    from pssc.targets.c.lower_progseq import CBodyEmitter
    src = tmp_path / "m.pss"
    src.write_text(_PSS)
    comp = driver.translate(str(src)).type_map["pss_top"]
    fn = [f for f in comp.functions if f.name == "f"][0]
    return CBodyEmitter(fn, comp, "pss_top")


def test_fresh_local_takes_no_name_the_body_sees(tmp_path):
    em = _emitter(tmp_path)
    # `_pss_t` is a parameter, `_pss_t_1` a local.
    assert em.fresh_local("_pss_t", "uint32_t") == "_pss_t_2"
    assert em.fresh_local("_pss_t", "uint32_t") == "_pss_t_3"
    assert em.fresh_local("goto_", "int") == "goto__1"   # the parameter `goto`
    assert em.fresh_local("i", "int") == "i_1"           # the loop variable
    assert em.fresh_local("_self", "int") == "_self_1"   # the handle
    assert em.function_local_lines(1) == []


def test_a_function_scope_fresh_local_is_declared_and_not_shared(tmp_path):
    em = _emitter(tmp_path)
    a = em.fresh_local("_pss_w", "uint32_t", scope="function")
    b = em.fresh_local("_pss_w", "uint32_t", scope="function")
    assert (a, b) == ("_pss_w", "_pss_w_1")
    assert em.function_local_lines(1) == ["    uint32_t _pss_w;",
                                          "    uint32_t _pss_w_1;"]
    assert em.fresh_local("_pss_w", "int") == "_pss_w_2"


def test_fresh_local_refuses_a_stem_c_will_not_take(tmp_path):
    em = _emitter(tmp_path)
    with pytest.raises(Refusal, match="reserved by C"):
        em.fresh_local("__t", "int")
    with pytest.raises(ValueError, match="scope"):
        em.fresh_local("t", "int", scope="file")


def test_fresh_local_avoids_the_signatures_names(tmp_path):
    """In an operation, the parameters and operations as the backend's
    `signature` names them."""
    from pssc.targets.c.lower_progseq import CBodyEmitter, OpCtx

    class Api:
        def signature(self, fn, ctx):
            return ("int", f"dev_{fn.name}", [("int", "_pss_p")])

    em = _emitter(tmp_path)
    em.op_ctx = OpCtx(comp=em.comp, prefix="dev", qual="",
                      emitter_cls=CBodyEmitter, be_kw={}, api=Api())
    assert em.fresh_local("_pss_p", "int") == "_pss_p_1"
    assert em.fresh_local("dev_f", "int") == "dev_f_1"
