"""Organize SV IR nodes into output files and write them.

All generated classes (structs, components, actions, import interfaces)
are wrapped in a single ``package zsp_gen_pkg`` that imports
``zsp_rt_pkg``.  The top-level module imports both packages.

| File              | Contents                                      |
|-------------------|-----------------------------------------------|
| zsp_rt_pkg.sv     | Runtime library (copied from share/)          |
| zsp_gen_pkg.sv    | Package with all generated classes            |
| zsp_top.sv        | Top-level module / test harness               |
| zsp_filelist.f    | File list for simulator compilation           |
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from zuspec.be.sv.ir.sv import (
    SVClass,
    SVForwardDecl,
    SVModuleDecl,
    SVPackage,
    SVRawItem,
    SVTypedefEnum,
    SVTypedefStruct,
)
from zuspec.be.sv.ir.sv_emit import SVEmitter


_FILE_DPI_PKG  = "zsp_dpi_pkg.sv"
_FILE_RT       = "zsp_rt_pkg.sv"
_FILE_GEN_PKG  = "zsp_gen_pkg.sv"
_FILE_TOP      = "zsp_top.sv"
_FILE_FILELIST = "zsp_filelist.f"

# Compilation order: DPI pkg first (no dependencies), then rt pkg, then generated code
_COMPILE_ORDER = [
    _FILE_DPI_PKG,
    _FILE_RT,
    _FILE_GEN_PKG,
    _FILE_TOP,
]

# Default path to zsp_dpi_pkg.sv (in zuspec-solver package)
_DEFAULT_DPI_PKG_PATH = (
    Path(__file__).resolve().parents[6]
    / "zuspec-solver" / "src" / "sv" / "zsp_dpi_pkg.sv"
)


def classify_node(node: Any) -> str:
    """Return the output filename for a given SV IR node."""
    if isinstance(node, SVModuleDecl):
        return _FILE_TOP
    return _FILE_GEN_PKG


def classify_nodes(nodes: List[Any]) -> Dict[str, List[Any]]:
    """Classify nodes into per-file buckets."""
    buckets: Dict[str, List[Any]] = {f: [] for f in _COMPILE_ORDER}
    for node in nodes:
        dest = classify_node(node)
        buckets.setdefault(dest, []).append(node)
    return buckets


def emit_files(
    nodes: List[Any],
    output_dir: str,
    runtime_lib_path: Optional[Path] = None,
    top_module_node: Optional[SVModuleDecl] = None,
    dpi_pkg_path: Optional[Path] = None,
    emit_dpi: bool = False,
    reg_pkg_path: Optional[Path] = None,
    package_name: str = "zsp_gen_pkg",
) -> List[Path]:
    """Write SV IR nodes to organized output files.

    All generated classes are wrapped in ``package <package_name>`` which
    imports ``zsp_rt_pkg`` (and ``zsp_dpi_pkg`` only when ``emit_dpi`` is set).

    Args:
        nodes: SV IR nodes from pss_to_sv().
        output_dir: Directory to write output files.
        runtime_lib_path: Path to zsp_rt_pkg.sv (defaults to share/ directory).
        top_module_node: Optional generated test harness module node.
        dpi_pkg_path: Path to zsp_dpi_pkg.sv. Defaults to zuspec-solver/src/sv/.
        emit_dpi: When True, copy/import the DPI solver package
            (``zsp_dpi_pkg.sv``). Defaults to False: the PSS front end lowers
            constraints to SV-native ``randomize()`` and emits no ``zsp_dpi_*``
            calls, so importing the package without linking its C implementation
            would produce undefined-symbol link errors (notably on Verilator).
        package_name: Name of the generated package (and its ``.sv`` filename),
            so the OO export-API projection can emit a user-named package.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    emitter = SVEmitter()
    all_nodes = list(nodes)
    if top_module_node is not None:
        all_nodes.append(top_module_node)

    buckets = classify_nodes(all_nodes)
    written: List[Path] = []

    # Resolve DPI package path
    _dpi_path = dpi_pkg_path or _DEFAULT_DPI_PKG_PATH

    # Copy DPI package (zsp_dpi_pkg.sv) only when the design actually uses the
    # DPI solver -- otherwise its declared imports become undefined symbols at
    # link time (the C implementation is not built/linked here).
    filelist_entries: List[str] = []
    if emit_dpi and _dpi_path and _dpi_path.exists():
        dpi_dst = out / _FILE_DPI_PKG
        # Not copy2: it preserves the source's mode, and package data is
        # commonly read-only -- a second compile into the same output dir
        # would then fail to overwrite its own prior output.
        shutil.copyfile(str(_dpi_path), str(dpi_dst))
        written.append(dpi_dst)
        filelist_entries.append(_FILE_DPI_PKG)

    # Copy runtime library (zsp_rt_pkg.sv)
    if runtime_lib_path and runtime_lib_path.exists():
        rt_dst = out / _FILE_RT
        shutil.copyfile(str(runtime_lib_path), str(rt_dst))
        written.append(rt_dst)
        filelist_entries.append(_FILE_RT)

    # Copy the register runtime (pssc_reg_pkg.sv) when the model has registers
    if reg_pkg_path and reg_pkg_path.exists():
        reg_dst = out / reg_pkg_path.name
        shutil.copyfile(str(reg_pkg_path), str(reg_dst))
        written.append(reg_dst)
        filelist_entries.append(reg_pkg_path.name)

    # Emit generated package (all classes wrapped in a package). The package
    # name (and hence its filename) is configurable so the OO projection can
    # emit a user-named package.
    gen_pkg_file = f"{package_name}.sv"
    gen_nodes = buckets.get(_FILE_GEN_PKG, [])
    if gen_nodes:
        pkg_lines: List[str] = []
        pkg_lines.append(f"package {package_name};")
        if emit_dpi:
            pkg_lines.append("  import zsp_dpi_pkg::*;")
        pkg_lines.append("  import zsp_rt_pkg::*;")
        if reg_pkg_path:
            pkg_lines.append("  import pssc_reg_pkg::*;")
        pkg_lines.append("")
        sv_body = emitter.emit_all(gen_nodes)
        for line in sv_body.splitlines():
            pkg_lines.append(f"  {line}" if line.strip() else "")
        pkg_lines.append("")
        pkg_lines.append(f"endpackage : {package_name}")

        filepath = out / gen_pkg_file
        filepath.write_text("\n".join(pkg_lines) + "\n")
        written.append(filepath)
        filelist_entries.append(gen_pkg_file)

    # Emit top module (needs to import both packages)
    top_nodes = buckets.get(_FILE_TOP, [])
    if top_nodes:
        # Prepend import of generated package to the module body
        for node in top_nodes:
            if isinstance(node, SVModuleDecl):
                # Insert import <package_name>::* after the existing import zsp_rt_pkg::*
                new_body = []
                for line in node.body_lines:
                    new_body.append(line)
                    if "import zsp_rt_pkg::*;" in line:
                        new_body.append(f"import {package_name}::*;")
                node.body_lines = new_body

        sv_text = emitter.emit_all(top_nodes)
        filepath = out / _FILE_TOP
        filepath.write_text(sv_text + "\n")
        written.append(filepath)
        filelist_entries.append(_FILE_TOP)

    # Write filelist
    filelist_path = out / _FILE_FILELIST
    filelist_path.write_text("\n".join(filelist_entries) + "\n")
    written.append(filelist_path)

    return written


def emit_filelist(
    filenames: List[str],
    output_dir: str,
) -> Path:
    """Write a simulator file list."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / _FILE_FILELIST
    path.write_text("\n".join(filenames) + "\n")
    return path
