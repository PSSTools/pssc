"""The export/import hand-off gives the same verdicts as running pssc in-process.

pss-corpus's HANDOFF.md path is how a tool we cannot run (a vendor's) gets
checked: export a bundle, run the tool over it elsewhere with the bundle's own
``run_jobs.py``, import the results. Here pssc's bc adapter plays the vendor,
run as a separate command exactly as a vendor's adapter would be. Every verdict
must equal the one ``test_compliance_bc.py`` gets in-process, or the hand-off
is adding or hiding something.
"""
import os
import subprocess
import sys

from conftest import CORPUS
from pss_corpus import Selection, check_run, discover, export, import_results, select

from adapters import pssc_bc

_ADAPTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapters", "pssc_bc.py")


def test_bundle_verdicts_match_in_process(tmp_path):
    tests = list(discover(os.path.join(CORPUS, "compliance")))
    sel = Selection()
    chosen, excluded = select(tests, sel, lambda t: True)
    bundle = str(tmp_path / "bundle")
    export(chosen, bundle, sel, excluded=excluded)

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    r = subprocess.run([sys.executable, os.path.join(bundle, "run_jobs.py"),
                        "--adapter", f"{sys.executable} {_ADAPTER}",
                        "-j", str(os.cpu_count() or 1)],
                       env=env, capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout + r.stderr

    report = import_results(bundle, tests)
    assert report.tools and all(t["tool"] == "pssc" and t["target"] == "bc"
                                for t in report.tools), report.tools

    by_id = {t.id: t for t in tests}
    diffs = []
    for res in report.results:
        t = by_id[res["test"]]
        live = tmp_path / "live" / res["job"]
        pssc_bc.run(t.sources, t.root, res["seed"], str(live))
        want = check_run(t, str(live), seed=res["seed"]).verdict
        if res["verdict"] != want:
            diffs.append(f"{res['job']}: bundle {res['verdict']} ({res['reason']}), "
                         f"in-process {want}")
    assert not diffs, "\n".join(diffs)
    assert len(report.results) == sum(len(t.seeds) for t in tests)
