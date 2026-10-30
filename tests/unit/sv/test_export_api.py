"""S7: structural tests for the OO export-API projection (lower_export_api).

Sim-agnostic: feed synthetic ``ExportAction`` specs (and fake import funcs)
through ``build_oo_api_nodes`` and assert the emitted SystemVerilog has the
interface-class API, the factory augmentation, and the impl that runs the
action lifecycle with ``comp == this``.
"""
from types import SimpleNamespace

from zuspec.be.sv.ir.sv import SVClass
from zuspec.be.sv.ir.sv_emit import SVEmitter

from pssc.targets.sv.lower_export_api import build_oo_api_nodes, ExportAction


def _emit(prefix, nodes, suffix):
    return SVEmitter().emit_all(prefix + nodes + suffix)


def test_atomic_export_api_shape():
    root = SVClass(name="pss_top")
    nodes = [root]
    specs = [ExportAction("Entry", "pss_top__Entry", "pss_top", has_activity=False)]
    prefix, suffix = build_oo_api_nodes(nodes, specs, ctx=None, import_funcs=None)
    txt = _emit(prefix, nodes, suffix)

    assert "interface class import_api_if" in txt
    assert "interface class export_api_if" in txt
    assert "pure virtual task Entry();" in txt
    assert "interface class factory_if" in txt
    assert "implements factory_if" in txt
    assert "static function pss_top type_id();" in txt
    assert "class export_api_impl extends pss_top implements export_api_if;" in txt
    # the impl IS the component: comp == this, atomic -> body()
    assert "root.comp = this;" in txt
    assert "root.body();" in txt


def test_compound_export_calls_activity():
    root = SVClass(name="pss_top")
    nodes = [root]
    specs = [ExportAction("Run", "pss_top__Run", "pss_top", has_activity=True)]
    prefix, suffix = build_oo_api_nodes(nodes, specs, import_funcs=None)
    txt = _emit(prefix, nodes, suffix)
    assert "pure virtual task Run();" in txt
    assert "root.activity();" in txt


class _Ctx:
    """Minimal stand-in exposing pss_type_to_sv_type_str."""
    def pss_type_to_sv_type_str(self, dtype):
        return "int"


def _imp(name, is_target, is_solve, returns):
    return SimpleNamespace(
        name=name, is_target=is_target, is_solve=is_solve, returns=returns,
        args=SimpleNamespace(args=[SimpleNamespace(arg="i", annotation=object())]),
    )


def test_import_api_surface_target_task_solve_function():
    root = SVClass(name="pss_top")
    nodes = [root]
    specs = [ExportAction("Entry", "pss_top__Entry", "pss_top", has_activity=False)]
    imports = [
        _imp("doit", is_target=True, is_solve=False, returns=None),     # target void -> task
        _imp("getval", is_target=False, is_solve=True, returns=object()),  # solve -> function
    ]
    prefix, suffix = build_oo_api_nodes(nodes, specs, ctx=_Ctx(), import_funcs=imports)
    txt = _emit(prefix, nodes, suffix)

    # import_api_if: target->task, solve->function
    assert "pure virtual task doit(" in txt
    assert "pure virtual function int getval(" in txt
    # base impl stubs each with $fatal for the testbench to extend
    assert "class import_api_base implements import_api_if;" in txt
    assert 'doit not implemented' in txt
    # root component gains an import_if handle and wires it in the impl ctor
    assert "import_if = imp;" in txt
    assert "set_import_if(imp_if);" in txt


def test_missing_root_component_raises():
    import pytest
    nodes = []  # no SVClass named pss_top
    specs = [ExportAction("Entry", "pss_top__Entry", "pss_top")]
    with pytest.raises(ValueError):
        build_oo_api_nodes(nodes, specs)


def test_action_runner_proxies_emitted():
    """One <action>_runner per export action, implementing pss_action_run_if."""
    root = SVClass(name="pss_top")
    nodes = [root]
    specs = [
        ExportAction("Hello", "pss_top__Hello", "pss_top", has_activity=False),
        ExportAction("World", "pss_top__World", "pss_top", has_activity=False),
    ]
    prefix, suffix = build_oo_api_nodes(nodes, specs)
    txt = _emit(prefix, nodes, suffix)

    for act in ("Hello", "World"):
        assert f"class {act}_runner implements pss_action_run_if;" in txt
        # static create() + run() forwarding to the bound export handle
        assert f"static function pss_action_run_if create(input export_api_if ep);" in txt
        assert f"m_api.{act}();" in txt
