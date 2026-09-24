"""``op-model-py``: a PSS operation model as a plain Python module.

The third language in the operation-model family, and the first one written
after the shared layer existed. That is its second job: the walk
(`body_walker.py`), the call dispatch (`call_legality.py`), the elaborated model
(`op_model.py`), the folded register layout (`reg_layout.py`) and the API-type
collection (`sv/lower_api_types.py`) are all consumed here rather than
reimplemented, so what is left -- `targets/py/` -- is the part that is actually
about Python.

Its first job is to be useful: a generated module drives a duck-typed bus, which
is what a cocotb bring-up, a socket-attached debugger or a pure-Python device
model already has.

Plan: P8.T1.
"""
from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
from typing import Dict, List

from .call_legality import BOTH, SOLVE_ONLY, TARGET_ONLY, Disposition, Entry
from .op_model import OpModelTarget


def _e(name, disp, contexts, lrm="", unsupported=""):
    return Entry(name, disp, contexts, lrm, unsupported)


#: Why the two string built-ins are not renderable here. Python HAS strings,
#: which is exactly why this needs stating: the obstacle is not the type, it is
#: that PSS format specifiers (19) are not Python's and this backend implements
#: no translation between them. Emitting the string with the specifiers intact
#: would produce output that looks formatted and is not.
_NO_PY_FORMAT = (
    "it takes a PSS format string (LRM 19), whose specifiers are not Python's, "
    "and this backend implements no translation of them")

#: Likewise the PRNG. `random` is in the standard library, so this is a choice:
#: a generated module that imports nothing is one that runs wherever it is
#: copied, and seeding/determinism is a policy this backend has no way to state.
_NO_PY_PRNG = (
    "it needs a seeded PRNG, and how a generated driver is seeded is a policy "
    "decision this backend cannot make for its caller. Pass the values in")

#: Only in the SYNC form, which is why the text names the form. A plain method
#: has no scheduler to suspend to, so lowering `get()` there would have to
#: invent a spin -- and a `get()` that returned whatever was in the slot reports
#: a completion nobody signalled. The async form has an answer and gets one.
_NO_PY_BLOCKING_CHANNEL = (
    "get()/put() suspend, and a model generated with --py-await sync is plain "
    "methods with no scheduler to suspend to (HAVE_EVENT_WAIT=false). Generate "
    "with --py-await async, where blocking has a meaning, or use "
    "try_get/try_put -- which this form does render (share/py/pssc_rt.py)")


class PyProgSeqTarget(OpModelTarget):
    name = "op-model-py"
    description = "Python operation-model API generated from a component tree"
    language = "Python"
    supports_entries = True
    native_inheritance = True
    supports_package_functions = True
    supports_init_blocks = True
    supports_executor_delegation = True

    #: The DEFAULT form's capabilities -- what a bare command line gets. The
    #: form is a per-run choice (`--py-await`), so the answer that actually
    #: reaches the model comes from `resolved_target_cfg_for` below; this is
    #: what `pssc targets` reports and it is truthful because sync is the
    #: default.
    #:
    #: Sync's `HAVE_EVENT_WAIT=false` is the C target's position and holds for
    #: the same reason: plain methods, no scheduler, no solver, so a caller
    #: cannot suspend until another party posts an event. A POLLING wait needs
    #: no runtime support and is available either way -- `yield` lowers to
    #: nothing here and the surrounding loop becomes a poll.
    target_cfg = {
        "HAVE_EVENT_WAIT": False,
        "HAVE_RUNTIME_SOLVER": False,
    }

    #: The sync form's Tier-2 set. `--py-await async` amends it; see
    #: `legality_entries_for`.
    legality_entries = (
        _e("print", Disposition.UTILITY, BOTH, "21.1.2"),
        _e("format",        Disposition.UTILITY, BOTH, "21.1.2", _NO_PY_FORMAT),
        _e("format_string", Disposition.UTILITY, BOTH, "19", _NO_PY_FORMAT),
        _e("urandom",       Disposition.UTILITY, BOTH, "21.4", _NO_PY_PRNG),
        _e("urandom_range", Disposition.UTILITY, BOTH, "21.4", _NO_PY_PRNG),
        _e("get", Disposition.CHANNEL, TARGET_ONLY, "21.9.1",
           _NO_PY_BLOCKING_CHANNEL),
        _e("put", Disposition.CHANNEL, TARGET_ONLY, "21.9.1",
           _NO_PY_BLOCKING_CHANNEL),
        # Renderable: Chan1.try_get / try_put (DEPTH > 1 is rejected).
        _e("try_get", Disposition.CHANNEL, TARGET_ONLY, "21.9.1"),
        _e("try_put", Disposition.CHANNEL, TARGET_ONLY, "21.9.1"),
        # Renderable for a TRANSPARENT region: its handle is its `addr`
        # (21.10.3.3), which the model states. Solve-only, as the LRM has it:
        # regions are part of the static component hierarchy (21.10.1.2.1).
        # Any other region is refused where it is lowered -- where the tool
        # would place it is exactly what an operation model cannot know.
        _e("add_region",                Disposition.ADDR, SOLVE_ONLY, "21.10"),
        _e("add_nonallocatable_region", Disposition.ADDR, SOLVE_ONLY, "21.10"),
        # Consumed by the lowering, like `set_handle`: it records the
        # component's executor, and the tree's assignment is resolved once it
        # is built. Rendered only in `exec init_down`/`init_up` (21.7.2.6).
        _e("set_executor", Disposition.STRUCTURAL, SOLVE_ONLY, "21.7.2.6"),
    )

    def __init__(self) -> None:
        super().__init__()
        # No `derives_from`, so `OpModelTarget.__init__` registers nothing and
        # this target's Tier-2 set has to be registered here. Stated rather than
        # inherited because this backend is NOT a restyled C target: it reaches
        # different conclusions about strings and randomness, and a `dict()`
        # copy of the C entry would silently hand it whatever that entry gains
        # next.
        #
        # The DEFAULT set, so that anything asking what this target renders
        # without a command line in hand gets the same answer `target_cfg`
        # gives. `run()` re-registers for the form actually chosen.
        self._register_legality("sync")

    # -- the two things `--py-await` changes outside `targets/py/` ------------

    def _await_style(self, opts: argparse.Namespace) -> str:
        return getattr(opts, "py_await", None) or "sync"

    def resolved_target_cfg_for(self, opts: argparse.Namespace):
        """`HAVE_EVENT_WAIT` is a property of the FORM, not of the target.

        The async form generates `async def` methods over a scheduler the
        caller supplies, and in that world a model CAN suspend until another
        party posts an event -- so the model's `compile if
        (target_cfg_pkg::HAVE_EVENT_WAIT)` branches must see the truth.

        `--target-cfg HAVE_EVENT_WAIT=false` still overrides this, as it
        overrides every other target's, and the result is an async model that
        polls. That is a legitimate thing to ask for and is why the override is
        applied on top rather than refused.
        """
        cfg = dict(super().resolved_target_cfg_for(opts) or {})
        cfg["HAVE_EVENT_WAIT"] = self._await_style(opts) == "async"
        return cfg

    def _register_legality(self, await_style: str) -> None:
        """Publish the Tier-2 set for *await_style*.

        PER RUN, not once at construction, because `get`/`put` are refused in
        one form and rendered in the other. Two generations in one interpreter
        therefore have to each re-register before their model is checked --
        which is what `run()` does, and what the both-orders test in
        `tests/progseq/test_op_model_py.py` fails on if this moves.
        """
        from .call_legality import register_extension
        entries = self.legality_entries
        if await_style == "async":
            # RENDERABLE, with no `unsupported` text: `await self.wake.get()`
            # is exactly what a blocking channel receive means, and
            # `pssc_rt_async.Chan1` implements it.
            entries = tuple(
                dataclasses.replace(e, unsupported="")
                if e.name in ("get", "put") else e
                for e in entries)
        register_extension(self.name, entries, replace=True)

    def run(self, ctx, opts: argparse.Namespace):
        # BEFORE `super().run()`, which elaborates and then CHECKS: the check
        # consults the registry, so a stale registration would refuse a call
        # this run renders (or render one it refuses).
        self._register_legality(self._await_style(opts))
        return super().run(ctx, opts)

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        # `--root`, `--ctor-name` and `--no-core-copy` come from
        # `OpModelTarget`. This backend adds two: the generated module's name,
        # and the API form. Everything the C target spells as a flag -- the
        # memory seam, the lifecycle, the register layout -- is either not a
        # choice in Python or is the platform object's business.
        super().add_args(parser)
        parser.add_argument(
            "--py-module", dest="py_module", metavar="NAME",
            help="op-model-py: generated module name (default: the root "
                 "component's name with a trailing _c stripped)",
        )
        parser.add_argument(
            "--py-await", dest="py_await", choices=("sync", "async"),
            default="sync",
            help="op-model-py: API form. `sync` (default) generates plain "
                 "methods; `async` generates `async def` throughout and "
                 "requires an async import API to match. Not a style: it "
                 "changes what the generated model can do (HAVE_EVENT_WAIT)",
        )

    # -- runtime source ------------------------------------------------------

    core_lang = "py"

    def core_file_names(self, model, opts) -> List[str]:
        """`pssc_rt.py`, plus the async half when that is the form.

        Content-dependent, as the C++ backend's channel header is: the runtime
        is REQUIRED only by a model with channels, and copying it beside a model
        that imports nothing would leave a reader wondering what is missing. It
        is copied anyway for its `MemoryBus`, which is what a bring-up drives --
        so the honest rule is "always, and the import is what varies".

        `pssc_rt.py` in BOTH forms, not only the sync one: `check_import_api`
        and the sparse memory `AsyncMemoryBus` wraps live there, and the async
        runtime imports them.
        """
        if self._await_style(opts) == "async":
            return ["pssc_rt.py", "pssc_rt_async.py"]
        return ["pssc_rt.py"]

    # -- entry point ---------------------------------------------------------

    #: The backend class that assembles the module. Resolved late so importing
    #: this module does not drag the whole lowering in.
    backend_cls = None

    @classmethod
    def backend_class(cls):
        from .py.backend import PyOpModelBackend
        return cls.backend_cls or PyOpModelBackend

    def backend_for(self, opts: argparse.Namespace):
        """The backend instance this run generates through."""
        return self.backend_class()(getattr(opts, "py_module", None) or "",
                                    await_style=self._await_style(opts))

    def sections(self, model, opts: argparse.Namespace) -> Dict[str, str]:
        return self.backend_for(opts).sections(model)

    def emit(self, model, opts: argparse.Namespace) -> List[Path]:
        # The generated module FIRST, then the runtime. Python resolves imports
        # at run time, so unlike the SV package this is not a compilation order
        # -- it is a reading order, and the generated file is what a reader
        # opens.
        be = self.backend_for(opts)
        return be.generate(model) + self.install_core(model, opts)
