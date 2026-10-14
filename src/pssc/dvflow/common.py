"""Shared helpers for the ``pssc`` DFM build tasks.

The heavy lifting that every per-output-style build task shares lives here:

  * :func:`gather_pss_sources` — pull ``pssSource`` files out of task inputs.
  * :func:`classify_outputs` — group ``pssc.compile`` outputs into typed filesets.
  * :func:`compute_memento` — content hash for incremental-rebuild skipping.
  * :func:`run_build` — the full build-task body (gather → compile → classify).

:mod:`dv_flow.mgr` symbols are imported lazily inside the functions so that
importing this module never hard-requires DFM.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

_log = logging.getLogger("pssc.dvflow")

#: The fileset ``filetype`` PSS sources are tagged with (authored via
#: ``std.FileSet { type: pssSource }``).
PSS_SOURCE_FILETYPE = "pssSource"

#: Dataset ``type`` carrying shared build options (see ``flow.yaml``).
PSSC_ARGS_TYPE = "pssc.PsscArgs"

#: extension -> (fileset filetype, contributes-its-dir-as-an-incdir)
#: ``.f`` filelists are intentionally absent: DFM tracks file membership itself,
#: so the generated filelist is dropped (design §7.5).
#:
#: PRIVATE. Extend it with :func:`register_filetype`, read it with
#: :func:`filetype_for`. A plugin emitting an extension nobody mapped had its
#: output silently dropped from every fileset -- the file is written, the build
#: is green, and it simply never reaches a compiler.
_EXT_FILETYPE: Dict[str, Tuple[str, bool]] = {
    ".sv": ("systemVerilogSource", True),
    # Included by a package, never compiled on its own (op-model-sv writes
    # one per class): an include, whose directory the compile searches.
    ".svh": ("systemVerilogInclude", True),
    ".so": ("systemVerilogDPI", False),
    ".c": ("cSource", False),
    ".h": ("cSource", True),
    ".cpp": ("cppSource", False),
    ".cc": ("cppSource", False),
    ".hpp": ("cppSource", True),
    ".hh": ("cppSource", True),
    ".py": ("pythonSource", False),
    ".txt": ("pythonSource", False),
    ".pkl": ("pythonSource", False),
}


def register_filetype(ext: str, filetype: str, is_incdir: bool = False) -> None:
    """Map a file extension onto a DFM fileset ``filetype``.

    For a target emitting something pssc does not ship a mapping for -- a
    plugin's ``.pyi`` stubs, a vendor's ``.vhd``. ``is_incdir`` marks the
    extension as one whose directory belongs on the include path, which is what
    headers need and what sources do not.

    ``ext`` is normalised (leading dot optional, case-insensitive). Re-mapping
    an extension is allowed and last wins: a project overriding how ``.txt`` is
    classified is a legitimate thing to want, and refusing it would only push
    the override into monkey-patching the dict.
    """
    if not ext:
        raise ValueError("register_filetype: extension must not be empty")
    if not filetype:
        raise ValueError(
            f"register_filetype: no filetype given for '{ext}'; a fileset "
            f"without a filetype reaches no compiler")
    key = ext if ext.startswith(".") else "." + ext
    _EXT_FILETYPE[key.lower()] = (filetype, bool(is_incdir))


def filetype_for(ext: str):
    """The ``(filetype, is_incdir)`` mapped to ``ext``, or ``None``."""
    key = ext if ext.startswith(".") else "." + ext
    return _EXT_FILETYPE.get(key.lower())


def registered_filetypes() -> Dict[str, Tuple[str, bool]]:
    """A copy of the extension -> ``(filetype, is_incdir)`` map."""
    return dict(_EXT_FILETYPE)


def gather_pss_sources(input) -> List[str]:
    """Return absolute paths of every ``pssSource`` file in ``input.inputs``.

    Order is preserved (fileset order, then file order within each fileset).
    """
    sources: List[str] = []
    for fs in input.inputs:
        if getattr(fs, "filetype", None) != PSS_SOURCE_FILETYPE:
            continue
        basedir = getattr(fs, "basedir", "") or ""
        for f in getattr(fs, "files", []) or []:
            sources.append(f if os.path.isabs(f) else os.path.join(basedir, f))
    return sources


def gather_export_actions(input) -> List[str]:
    """Collect export-action names from task params and ``pssc.PsscArgs`` inputs."""
    actions: List[str] = list(getattr(input.params, "export_action", None) or [])
    for ds in input.inputs:
        if getattr(ds, "type", None) == PSSC_ARGS_TYPE:
            actions.extend(getattr(ds, "export_action", None) or [])
    return actions


def gather_extra_args(input) -> List[str]:
    """Collect raw pass-through ``args`` from task params and ``pssc.PsscArgs``."""
    args: List[str] = list(getattr(input.params, "args", None) or [])
    for ds in input.inputs:
        if getattr(ds, "type", None) == PSSC_ARGS_TYPE:
            args.extend(getattr(ds, "args", None) or [])
    return args


def classify_outputs(paths, src_name: str, basedir: str) -> List[Any]:
    """Group ``pssc.compile`` output ``paths`` into typed DFM filesets.

    One :class:`~dv_flow.mgr.FileSet` per distinct filetype, each with
    ``basedir`` and relative ``files``; directories of incdir-eligible files
    (headers, SV) are added to that fileset's ``incdirs``. Files outside
    ``basedir`` or with an unknown extension are logged and skipped.
    """
    from dv_flow.mgr import FileSet

    base = os.path.abspath(basedir)
    # filetype -> (files[], incdirs set)
    groups: Dict[str, Tuple[List[str], set]] = {}
    for p in paths:
        ap = os.path.abspath(str(p))
        ext = os.path.splitext(ap)[1].lower()
        mapping = _EXT_FILETYPE.get(ext)
        if mapping is None:
            # WARNING, not debug. The file was generated and then dropped on
            # the floor: it is in no fileset, so no downstream task sees it,
            # and the build stays green. At debug level nobody found out.
            _log.warning(
                "classify_outputs: %s was generated but no fileset claims it "
                "-- extension '%s' is not mapped to a DFM filetype. Call "
                "pssc.dvflow.common.register_filetype(%r, '<filetype>') if a "
                "downstream task needs it", ap, ext or "(none)", ext or "")
            continue
        filetype, is_incdir = mapping
        try:
            rel = os.path.relpath(ap, base)
        except ValueError:
            rel = None
        if rel is None or rel.startswith(".."):
            _log.warning("classify_outputs: output %s outside basedir %s; skipping",
                         ap, base)
            continue
        files, incdirs = groups.setdefault(filetype, ([], set()))
        files.append(rel)
        if is_incdir:
            incdirs.add(os.path.dirname(ap) or base)

    filesets = []
    for filetype, (files, incdirs) in groups.items():
        # Preserve pssc's emission order: it returns files in dependency order
        # (e.g. zsp_rt_pkg.sv before zsp_gen_pkg.sv), which SV compilation needs.
        filesets.append(FileSet(
            src=src_name,
            filetype=filetype,
            basedir=base,
            files=files,
            incdirs=sorted(incdirs),
        ))
    return filesets


def _read_text(path: str) -> str:
    try:
        with open(path, "r", errors="replace") as fp:
            return fp.read()
    except OSError as e:
        _log.warning("memento: cannot read %s: %s", path, e)
        return ""


#: What an unknown-provenance target contributes to the memento. Unique per
#: process, so the hash never matches a cached one and the build re-runs.
_UNKNOWN_PROVENANCE = "unknown:" + uuid.uuid4().hex


def target_provenance(target: str) -> str:
    """Who provides ``target``, and at what version, as a memento ingredient.

    THE BUG THIS CLOSES. The memento covered source contents, the target NAME
    and the options -- everything except the code doing the generating. Upgrade
    the plugin that provides `acme-c`, rebuild, and dv-flow finds a matching
    memento and skips: the old generated output stays on disk and is compiled
    into the next simulation. Nothing reports it, because from the cache's point
    of view nothing changed.

    Three answers, in order:

    * a built-in -> pssc's own version. Deterministic in a source checkout,
      where the distribution metadata may not exist at all and where hashing
      "unknown" would mean rebuilding on every single dv-flow invocation.
    * a plugin -> the version of the distribution providing its module.
    * anything else -> :data:`_UNKNOWN_PROVENANCE`, which is unique per process
      and therefore forces the rebuild. The safe direction: a needless rebuild
      costs seconds, a skipped one ships stale generated code.
    """
    from .. import targets as _targets
    from ..__version__ import version as _pssc_version

    try:
        _targets.discover()
        tgt = _targets.get(target)
    except Exception:
        # An unknown target name is diagnosed by the driver, not here; it must
        # not be quietly hashed as if it were a known one.
        return _UNKNOWN_PROVENANCE

    if _targets.is_builtin(target):
        return f"pssc {_pssc_version}"

    module = type(tgt).__module__ or ""
    top = module.split(".")[0]
    try:
        from importlib.metadata import packages_distributions, version
        dists = packages_distributions().get(top) or []
        if dists:
            name = sorted(dists)[0]
            return f"{name} {version(name)}"
    except Exception:
        pass
    return _UNKNOWN_PROVENANCE


def compute_memento(sources: List[str], target: str,
                    options: Dict[str, Any]) -> str:
    """Stable md5 over source *contents*, the target, and the resolved options.

    Used to skip recompilation when nothing relevant changed (mirrors the
    ``std.CreateFile`` md5-memento pattern).

    The target's *provenance* is hashed alongside its name -- see
    :func:`target_provenance` -- so upgrading the plugin that generates the
    output invalidates the cache the output sits in.
    """
    h = hashlib.md5()
    h.update(("target=" + target).encode())
    h.update(("|by=" + target_provenance(target)).encode())
    for key in sorted(options):
        h.update(f"|opt:{key}={options[key]!r}".encode())
    for src in sorted(sources):
        h.update(("|src:" + os.path.basename(src) + ":").encode())
        h.update(_read_text(src).encode())
    return h.hexdigest()


def _missing_cached_files(filesets) -> List[str]:
    """Files a cached fileset names that are no longer on disk.

    Existence only, not content: the memento hash already covers "would the
    output differ", and re-hashing every product on the up-to-date path would
    cost more than the compile it is avoiding for a large fileset. What is not
    covered by the hash, and is what actually happens, is the file being
    removed -- a partial clean, an interrupted run, an `rm` aimed at forcing a
    rebuild.
    """
    missing: List[str] = []
    for fs in filesets:
        basedir = getattr(fs, "basedir", None) or ""
        for f in (getattr(fs, "files", None) or []):
            path = f if os.path.isabs(f) else os.path.join(basedir, f)
            if not os.path.exists(path):
                missing.append(path)
    return missing


async def run_build(ctxt, input, *, target: str,
                    overrides_from_params: Callable[[Any], Dict[str, Any]]):
    """Body shared by every ``pssc`` build task.

    Gathers PSS sources and options, optionally skips via the memento, runs
    ``pssc.compile`` (off-thread), converts PSS errors into DFM markers, and
    returns typed output filesets.
    """
    from dv_flow.mgr import TaskDataResult
    import pssc
    from pssc.driver import CompileError

    sources = gather_pss_sources(input)
    if not sources:
        ctxt.error("pssc.%s: no 'pssSource' inputs found (needs a "
                   "std.FileSet with type: pssSource)" % target)
        return TaskDataResult(status=1, changed=True)

    export_actions = gather_export_actions(input)
    overrides = dict(overrides_from_params(input.params))
    extra_args = gather_extra_args(input)

    # Option set that participates in the memento / change detection.
    option_sig: Dict[str, Any] = dict(overrides)
    option_sig["export_actions"] = sorted(export_actions)
    option_sig["args"] = list(extra_args)

    memento = compute_memento(sources, target, option_sig)
    prev = input.memento if isinstance(input.memento, dict) else None
    if (not input.changed and prev is not None
            and prev.get("hash") == memento and prev.get("output") is not None):
        from dv_flow.mgr import FileSet
        cached = [FileSet(**fs) for fs in prev["output"]]
        # The hash answers "would recompiling produce the same files?". It does
        # NOT answer "are those files still there". Handing back a fileset that
        # names a deleted file makes this task report success while its consumer
        # fails to find the source -- the error surfaces one task downstream,
        # which is the wrong place to debug it from.
        #
        # This is a SECOND cache: dv-flow-mgr has its own up-to-date check and
        # has already decided to invoke us. It can decide to run and we can
        # still short-circuit, so the existence check has to exist in both
        # layers or the outer one is unenforceable.
        missing = _missing_cached_files(cached)
        if missing:
            _log.debug("pssc.%s: memento hit but %d cached file(s) missing "
                       "(%s); recompiling", target, len(missing),
                       ", ".join(missing[:3]))
        else:
            _log.debug("pssc.%s: up to date; reusing %d cached fileset(s)",
                       target, len(cached))
            return TaskDataResult(changed=False, output=cached,
                                  memento=prev)

    out_dir = input.rundir
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "pssc_compile.log")

    def _do_compile():
        # Some targets print a report to stdout; capture it to a logfile.
        with open(log_path, "w") as logf, contextlib.redirect_stdout(logf):
            return pssc.compile(
                sources, target=target, output_dir=out_dir,
                export_actions=export_actions or None, **overrides)

    try:
        res = await asyncio.to_thread(_do_compile)
    except CompileError as e:
        errs = e.errors or [str(e)]
        for msg in errs:
            ctxt.error("pssc.%s: %s" % (target, msg))
        return TaskDataResult(status=1, changed=True)
    except Exception as e:  # noqa: BLE001 — surface any target failure as a marker
        _log.exception("pssc.%s failed", target)
        ctxt.error("pssc.%s: %s: %s" % (target, type(e).__name__, e))
        return TaskDataResult(status=1, changed=True)

    output = classify_outputs(res.outputs, input.name, out_dir)
    new_memento = {"hash": memento,
                   "output": [fs.model_dump() for fs in output]}
    return TaskDataResult(status=0, changed=True, output=output,
                          memento=new_memento)
