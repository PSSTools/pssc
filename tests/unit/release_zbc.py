"""Release-profile ZBC images of the compliance and matrix models (T-G1).

Debug lowering (``lower_module(debug=True)``) must leave the release image
alone: every byte of a release ``.zbc`` is the same as before the debug work.
:func:`collect` lowers each pss-corpus compliance test (as the ``pssc-bc``
adapter does) and each construct-matrix case (root ``pss_top``) in release and
maps it to the sha256 of ``model.to_bytes()``, or to ``error:<type>`` when it
does not lower. ``data/release_zbc_sha256.json`` holds the snapshot, taken
with zuspec-be-bc and zuspec-ir-core at their HEAD before the debug work.

Regenerate only for an intentional change to release bytecode::

    PYTHONPATH=./src python tests/unit/release_zbc.py --write
"""
import argparse
import glob
import hashlib
import importlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_TESTS = os.path.dirname(_HERE)
SNAPSHOT = os.path.join(_HERE, "data", "release_zbc_sha256.json")


def _hash(model) -> str:
    return hashlib.sha256(model.to_bytes()).hexdigest()


def _lower(sources, comp, action=None) -> str:
    import pssc
    from pssc.ast2ir import AstToIrTranslator
    from zuspec.ir.core.xf import PSSToScenarioPass
    from zuspec.be.bc.lower import lower_module

    try:
        parser = pssc.Parser()
        parser.parse(list(sources))
        linked = parser.link()
        ctx = AstToIrTranslator().translate(linked, files=parser.file_map)
        if ctx.errors:
            return "error:ctx"
        exports = [action] if action else None
        module = PSSToScenarioPass(root=comp or None, exports=exports).lower(ctx)
        model = lower_module(module, entry_action=action, solve_unconstrained=True)
    except Exception as e:
        return f"error:{type(e).__name__}"
    return _hash(model)


def _compliance():
    # The compliance conftest locates pss-corpus and puts its checker on
    # sys.path; loaded by path, since under pytest ``conftest`` is taken.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_compliance_conftest", os.path.join(_TESTS, "compliance", "conftest.py"))
    cft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cft)
    from pss_corpus import discover
    CORPUS = cft.CORPUS

    for t in discover(os.path.join(CORPUS, "compliance")):
        comp, _, action = t.root.rpartition("::")
        yield f"compliance/{t.id}", _lower(t.sources, comp, action)


def _matrix():
    if _TESTS not in sys.path:
        sys.path.insert(0, _TESTS)
    from progseq import trace_harness as th
    from progseq.matrix import _matrix as mx

    for path in sorted(glob.glob(os.path.join(_TESTS, "progseq", "matrix", "test_m_*.py"))):
        mod = os.path.splitext(os.path.basename(path))[0]
        cases = getattr(importlib.import_module(f"progseq.matrix.{mod}"), "CASES", {})
        for name, case in sorted(cases.items()):
            # The image may name its source file, so the model is read through a
            # relative path: the temporary directory's name must not reach it.
            cwd = os.getcwd()
            with tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, "m.pss"), "w") as fp:
                    fp.write(th._PRELUDE + mx.REGS + case.pss)
                os.chdir(d)
                try:
                    h = _lower(["m.pss"], "pss_top")
                finally:
                    os.chdir(cwd)
            yield f"matrix/{mod}/{name}", h


def collect() -> dict:
    out = dict(_compliance())
    out.update(_matrix())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite the snapshot")
    ap.add_argument("--out", help="write the hashes to this file instead")
    args = ap.parse_args(argv)
    hashes = collect()
    path = SNAPSHOT if args.write else args.out
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fp:
            json.dump(hashes, fp, indent=1, sort_keys=True)
            fp.write("\n")
    n_err = sum(v.startswith("error:") for v in hashes.values())
    print(f"{len(hashes)} models, {n_err} do not lower")


if __name__ == "__main__":
    main()
