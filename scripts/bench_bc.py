#!/usr/bin/env python3
"""Run a PSS model's exported actions on bc and report how they scale.

    python scripts/bench_bc.py MODEL [MODEL ...] --export A [--export B]
        [--sweep NAME=100,200,500] [--const NAME=V] [--seeds 1-5]
        [--check 'CMD {log} {export} {NAME}'] [--variety] [--tuple A:f,g]
        [--fair-pick] [--trace] [--json OUT] [--log-dir DIR]

MODEL is a ``.pss`` file or a directory (its ``*.pss``, in name order).

**Constants.** ``--const NAME=V`` and each value of ``--sweep NAME=...``
become ``const int NAME = V;`` in a file parsed before the model. A model
that declares its own default under ``compile if (!compile has(NAME))``
takes that value instead; nothing is copied or edited. A model without the
guard gets a duplicate-declaration error.

**One process per configuration.** Each (export, sweep value) is compiled
once and run for every seed in a worker process of its own, so the peak RSS
reported is that configuration's alone.

**What is reported.** Per configuration: the time of each stage (parse and
link, ast2ir, scenario, bc lower, and the run, per seed), peak RSS, the
solves made, the solves a cone's last solution answered, the problems
compiled, and the shape of each cone of the export's action tree (nodes, variables,
constraints by kind). With ``--check``, the command is run on each seed's
log (``{log}``, ``{export}``, ``{seed}`` and each constant's ``{NAME}`` are
substituted), and its last output line containing ``PASS`` is a pass.

**The trace.** A run's event trace is dropped (a ``NullSink``): it holds
every event of the run, so it is what a run's memory grows with, and nothing
here reads it. ``--trace`` keeps it, to measure what keeping it costs.

**Variety.** ``--variety`` reports the distribution of every ``rand`` leaf
of every action, as each traversal solved it; ``--tuple ACTION:a,b,c`` the
joint distribution of those leaves of one action (a dotted leaf path, as in
``io.op``). Values are collected through ``run_model(on_solve=...)``.

``--fair-pick`` solves with dv-solve's fair decision tie-break.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# -- arguments -------------------------------------------------------------------

def _seeds(text):
    """``5`` is seeds 1..5; ``3-7`` is 3..7; ``1,4,9`` is those."""
    if "," in text:
        return [int(x) for x in text.split(",")]
    if "-" in text:
        a, b = text.split("-")
        return list(range(int(a), int(b) + 1))
    return list(range(1, int(text) + 1))


def _assign(text):
    name, _, val = text.partition("=")
    if not name or not val:
        raise argparse.ArgumentTypeError("expected NAME=VALUE: %r" % text)
    return name.strip(), val.strip()


def _tuple(text):
    action, _, leaves = text.rpartition(":")
    if not action or not leaves:
        raise argparse.ArgumentTypeError("expected ACTION:leaf,leaf: %r" % text)
    return action, leaves.split(",")


def _parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("model", nargs="+", help=".pss files or directories")
    p.add_argument("--export", action="append", required=True,
                   help="exported action to run (repeatable)")
    p.add_argument("--root", default="pss_top", help="root component")
    p.add_argument("--sweep", type=_assign, metavar="NAME=V1,V2",
                   help="a constant, and the values to run it at")
    p.add_argument("--const", type=_assign, action="append", default=[],
                   metavar="NAME=V", help="a constant held for every run")
    p.add_argument("--seeds", type=_seeds, default=[1],
                   help="N (1..N), A-B, or a,b,c (default 1)")
    p.add_argument("--check", help="checker command, run on each seed's log")
    p.add_argument("--variety", action="store_true",
                   help="report every rand leaf's distribution")
    p.add_argument("--tuple", type=_tuple, action="append", default=[],
                   metavar="ACTION:a,b", help="report a joint distribution")
    p.add_argument("--fair-pick", action="store_true",
                   help="dv-solve's fair decision tie-break")
    p.add_argument("--trace", action="store_true",
                   help="keep each run's event trace (dropped by default)")
    p.add_argument("--json", help="write every result here")
    p.add_argument("--log-dir", help="keep each seed's log here")
    p.add_argument("--worker", help=argparse.SUPPRESS)
    return p


def _files(models):
    out = []
    for m in models:
        m = Path(m)
        out.extend(sorted(m.glob("*.pss")) if m.is_dir() else [m])
    return [str(f.resolve()) for f in out]


# -- the worker: one configuration ------------------------------------------------

class _Variety:
    """Each action's rand leaves, as each of its traversals solved them."""

    def __init__(self, module, model, tuples):
        self.by_coro = {}               # bc coroutine name -> [(leaf, slot)]
        for name, coro in module.coroutines.items():
            leaves = [(f.name, f.slot) for f in getattr(coro, "fields", []) if f.rand]
            if leaves:
                self.by_coro[name] = leaves
        self.leaf = collections.defaultdict(collections.Counter)
        self.tuples = []                # (action, [slot], Counter)
        for action, names in tuples:
            coro = self._coro(module, action)
            slots = {f.name: f.slot for f in coro.fields}
            missing = [n for n in names if n not in slots]
            if missing:
                raise SystemExit("--tuple %s: no leaf %s (has %s)" % (
                    action, ", ".join(missing), ", ".join(sorted(slots))))
            self.tuples.append((coro.name, names, [slots[n] for n in names],
                                collections.Counter()))

    @staticmethod
    def _coro(module, action):
        for name, coro in module.coroutines.items():
            if name == action or getattr(coro, "type_qname", None) == action:
                return coro
        raise SystemExit("--tuple: no action %r (have %s)" % (
            action, ", ".join(sorted(module.coroutines))))

    def __call__(self, frame):
        obj, base, name = frame.obj, frame.base, frame.coro.name
        if obj is None:
            return
        for leaf, slot in self.by_coro.get(name, ()):
            self.leaf[name + "." + leaf][obj.get_field(base + slot)] += 1
        for coro, _, slots, counter in self.tuples:
            if coro == name:
                counter[tuple(obj.get_field(base + s) for s in slots)] += 1

    def report(self):
        leaves = {}
        for key, c in sorted(self.leaf.items()):
            n = sum(c.values())
            leaves[key] = {"n": n, "distinct": len(c), "min": min(c), "max": max(c),
                           "top": [[v, k] for v, k in c.most_common(8)]}
        tuples = [{"action": coro, "leaves": names, "n": sum(c.values()),
                   "counts": [[list(k), v] for k, v in sorted(c.items())]}
                  for coro, names, _, c in self.tuples]
        return {"leaves": leaves, "tuples": tuples}


def _cones(tree):
    out = []
    for c in tree.cones:
        kinds = collections.Counter(getattr(k.kind, "name", str(k.kind))
                                    for k in c.constraints)
        out.append({"nodes": len(c.nodes), "vars": len(c.vars),
                    "rand": sum(1 for v in c.vars if v.rand),
                    "constraints": dict(sorted(kinds.items()))})
    return out


def _work(cfg):
    import pssc
    from pssc.ast2ir import AstToIrTranslator
    from zuspec.ir.core.xf import PSSToScenarioPass
    from zuspec.be.bc.lower import lower_module
    from zuspec.be.bc.interp import NativeBlobBackend, run_model
    from zuspec.be.bc.interp.solve_cache import SolveCache
    from zuspec.be.bc.trace.sink import MemorySink, NullSink

    export, consts = cfg["export"], cfg["consts"]
    res = {"export": export, "consts": consts, "stages": {}, "seeds": []}
    with tempfile.TemporaryDirectory() as tmp:
        files = list(cfg["files"])
        if consts:
            cf = os.path.join(tmp, "consts.pss")
            with open(cf, "w") as f:
                for name, val in consts.items():
                    f.write("const int %s = %s;\n" % (name, val))
            files.insert(0, cf)

        t = time.perf_counter()
        parser = pssc.Parser()
        parser.parse(files)
        linked = parser.link()
        res["stages"]["parse_link"] = time.perf_counter() - t

        t = time.perf_counter()
        ctx = AstToIrTranslator().translate(linked, files=parser.file_map)
        res["stages"]["ast2ir"] = time.perf_counter() - t
        if ctx.errors:
            res["error"] = "ast2ir: %s" % "; ".join(map(str, ctx.errors[:5]))
            return res

        t = time.perf_counter()
        module = PSSToScenarioPass(root=cfg["root"], exports=[export]).lower(ctx)
        res["stages"]["scenario"] = time.perf_counter() - t
        tree = module.trees.get(export)
        if tree is not None:
            res["tree"] = {"nodes": len(tree.nodes), "slots": tree.size,
                           "pools": len(tree.pools), "cones": _cones(tree)}

        t = time.perf_counter()
        model = lower_module(module, entry_action=export, solve_unconstrained=True)
        res["stages"]["lower"] = time.perf_counter() - t

        variety = None
        if cfg["variety"] or cfg["tuples"]:
            variety = _Variety(module, model, cfg["tuples"])

        for seed in cfg["seeds"]:
            cache = SolveCache(fair_pick=cfg["fair_pick"])
            lines = []
            rec = {"seed": seed}
            t = time.perf_counter()
            try:
                run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                          out=lines.append, verbosity=2, solve_cache=cache,
                          on_solve=variety,
                          sink=MemorySink() if cfg["trace"] else NullSink())
            except Exception as e:      # a located error is a result, not a crash
                rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
            finally:
                cache.clear()
            rec["run"] = time.perf_counter() - t
            rec.update(solves=cache.solves, reused=cache.reused,
                       compiles=cache.compiles, hits=cache.hits,
                       lcg_retries=cache.lcg_retries)
            log = os.path.join(cfg["log_dir"] or tmp, "%s_%s_s%d.log" % (
                export, "_".join("%s%s" % kv for kv in consts.items()), seed))
            with open(log, "w") as f:
                f.write("\n".join(lines) + "\n")
            if cfg["check"] and "error" not in rec:
                rec["check"] = _check(cfg["check"], log, export, seed, consts)
            res["seeds"].append(rec)
        if variety is not None:
            res["variety"] = variety.report()
    res["rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return res


def _check(cmd, log, export, seed, consts):
    subst = dict(consts, log=log, export=export, seed=seed)
    r = subprocess.run(cmd.format(**subst), shell=True, capture_output=True, text=True)
    lines = (r.stdout + r.stderr).strip().splitlines()
    verdict = next((ln for ln in reversed(lines) if "PASS" in ln or "FAIL" in ln),
                   lines[-1] if lines else "?")
    return {"pass": "PASS" in verdict and r.returncode == 0, "verdict": verdict.strip()}


# -- the driver -------------------------------------------------------------------

def _configs(args):
    files = _files(args.model)
    held = dict(args.const)
    sweep = [None]
    if args.sweep:
        name, vals = args.sweep
        sweep = [(name, v) for v in vals.split(",")]
    for export in args.export:
        for s in sweep:
            consts = dict(held)
            if s is not None:
                consts[s[0]] = s[1]
            yield {"files": files, "root": args.root, "export": export,
                   "consts": consts, "seeds": args.seeds, "check": args.check,
                   "variety": args.variety, "tuples": args.tuple,
                   "fair_pick": args.fair_pick, "log_dir": args.log_dir,
                   "trace": args.trace}


def _spawn(cfg):
    r = subprocess.run([sys.executable, __file__, "-", "--export", cfg["export"],
                        "--worker", json.dumps(cfg)],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"export": cfg["export"], "consts": cfg["consts"], "seeds": [],
                "stages": {}, "error": "worker failed: %s" % r.stderr.strip()[-800:]}


def _fmt_row(res):
    seeds = res["seeds"]
    n = max(len(seeds), 1)
    passed = [s for s in seeds if "error" not in s and s.get("check", {}).get("pass", True)]
    st = res["stages"]
    cones = res.get("tree", {}).get("cones", [])
    return [res["export"], " ".join("%s=%s" % kv for kv in res["consts"].items()),
            "%d/%d" % (len(passed), len(seeds))] + [
            "%.2f" % st.get(k, 0) for k in ("parse_link", "ast2ir", "scenario", "lower")] + [
            "%.2f" % (sum(s["run"] for s in seeds) / n),
            "%.0f" % res.get("rss_mb", 0),
            str(sum(s.get("solves", 0) for s in seeds) // n),
            str(sum(s.get("reused", 0) for s in seeds) // n),
            str(sum(s.get("compiles", 0) for s in seeds) // n),
            "%d / %d" % (len(cones), max((c["vars"] for c in cones), default=0))]


def _table(rows):
    head = ["export", "consts", "pass", "parse", "ast2ir", "scenario", "lower",
            "run", "RSS MB", "solves", "reused", "compiled", "cones / max vars"]
    rows = [head] + rows
    w = [max(len(r[i]) for r in rows) for i in range(len(head))]
    return "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip()
                     for r in rows)


def _print_variety(res):
    v = res.get("variety")
    if not v:
        return
    print("\n%s %s: rand leaves" % (res["export"],
                                    " ".join("%s=%s" % kv for kv in res["consts"].items())))
    for key, d in v["leaves"].items():
        top = ", ".join("%s:%d" % (val, k) for val, k in d["top"][:6])
        print("  %-40s n=%-6d distinct=%-6d [%s..%s]  %s" % (
            key, d["n"], d["distinct"], d["min"], d["max"], top))
    for t in v["tuples"]:
        print("\n  (%s) of %s, n=%d, %d distinct" % (
            ", ".join(t["leaves"]), t["action"], t["n"], len(t["counts"])))
        for k, n in t["counts"][:40]:
            print("    %-30s %6d  %5.1f%%" % (tuple(k), n, 100.0 * n / t["n"]))


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.worker:
        print(json.dumps(_work(json.loads(args.worker))))
        return 0
    if args.log_dir:
        os.makedirs(args.log_dir, exist_ok=True)
    results, rows, ok = [], [], True
    for cfg in _configs(args):
        res = _spawn(cfg)
        results.append(res)
        if "error" in res:
            print("%s %s: %s" % (res["export"], res["consts"], res["error"]))
            ok = False
            continue
        rows.append(_fmt_row(res))
        for s in res["seeds"]:
            if "error" in s or not s.get("check", {}).get("pass", True):
                ok = False
                print("  seed %d: %s" % (s["seed"], s.get("error") or s["check"]["verdict"]))
    print(_table(rows))
    for res in results:
        _print_variety(res)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
