"""The pssc DFM package loads via the dv_flow.mgr entry point."""
from .conftest import requires_dfm

EXPECTED_TASKS = {
    "pssc.RegPkg", "pssc.CoreC", "pssc.CoreCpp",
    "pssc.PySource", "pssc.SvNative", "pssc.SvDpi", "pssc.SvDpiBridge",
    "pssc.CHost", "pssc.CHostPresolved", "pssc.CEmbedded",
    "pssc.CEmbeddedPresolved", "pssc.SvProgSeq", "pssc.CProgSeq",
    "pssc.CppProgSeq",
    "pssc.OpModelSv", "pssc.OpModelC", "pssc.OpModelCpp", "pssc.OpModelPy",
}


@requires_dfm
def test_pssc_package_loads_with_all_tasks():
    from dv_flow.mgr import PackageLoader

    errors = []
    pkg = PackageLoader(
        marker_listeners=[lambda m: errors.append(str(m.msg))]
    ).load_rgy(["std", "pssc"])

    assert not errors, f"load produced markers: {errors}"
    p = pkg.pkg_m.get("pssc")
    assert p is not None
    assert EXPECTED_TASKS.issubset(set(p.task_m.keys()))


@requires_dfm
def test_entry_point_registered():
    from importlib.metadata import entry_points
    names = {e.name: e.value for e in entry_points(group="dv_flow.mgr")}
    assert names.get("pssc") == "pssc.dvflow.__ext__"
