"""The :class:`Target` ABC — the contract every pssc code-generation backend
implements.
"""
from __future__ import annotations

import abc
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import target_cfg as _target_cfg


class Target(abc.ABC):
    """A pssc code-generation target.

    Subclasses set :attr:`name`, optionally publish capabilities via
    :attr:`target_cfg`, optionally contribute CLI options via :meth:`add_args`,
    and emit artifacts via :meth:`run`.
    """

    #: The version of THIS contract -- the method set below, their signatures,
    #: and the shape of the objects they are handed. A discovered plugin
    #: carrying a different major is refused by name rather than allowed to
    #: fail later against a `run()` signature that moved, because a target that
    #: half-works produces generated code nobody can trust.
    #:
    #: Bump this when an existing member changes meaning or signature -- NOT
    #: when one is added, which older plugins simply do not use.
    PSSC_TARGET_API: int = 1

    #: The ``--target`` selector name (e.g. ``"op-model-sv"``, ``"python"``).
    name: str = ""

    #: Extra selector names this target answers to. Read by plugin discovery;
    #: built-ins pass theirs to :func:`~pssc.targets.register` directly.
    aliases: Tuple[str, ...] = ()

    #: Names this target deliberately takes over from an already-registered
    #: target. Empty is the right value: a collision is an error unless the
    #: author states here that the shadowing is intended.
    replaces: Tuple[str, ...] = ()

    #: One-line description shown by ``pssc targets``.
    description: str = ""

    #: Capabilities published to the model as ``target_cfg_pkg``, injected
    #: ahead of the user's sources -- or ``None`` to inject nothing, which
    #: leaves the model to take its own defaults.
    #:
    #: A target that sets this must supply EVERY constant in the contract
    #: version (see :mod:`pssc.targets.target_cfg`); a partial mapping is an
    #: error rather than a package with holes in it. ``None`` is the right
    #: value for a target whose capabilities have not been established --
    #: publishing a guess is worse than publishing nothing, because a model
    #: that sees the version marker trusts every flag beside it.
    target_cfg: Optional[Dict[str, bool]] = None

    def resolved_target_cfg(self) -> Optional[Dict[str, bool]]:
        """The capabilities actually published -- :attr:`target_cfg` here.

        The indirection exists so that a target family can compute them (the
        op-model family merges a derived target's flags onto its ancestor's).
        Everything that reads the capabilities -- `prelude`, `pssc targets` --
        goes through this, so a computed set cannot be right in the generated
        prelude and wrong in the listing.
        """
        return self.target_cfg

    def resolved_target_cfg_for(
            self, opts: argparse.Namespace) -> Optional[Dict[str, bool]]:
        """The capabilities published for THIS RUN. :meth:`resolved_target_cfg`
        by default, which is the answer for every target whose capabilities do
        not depend on an option.

        The hook exists because one of them does. `op-model-py` generates
        either a synchronous or an `async`/`await` API (`--py-await`), and only
        the second can suspend until another party posts an event -- so
        `HAVE_EVENT_WAIT` is a property of the chosen form, not of the target.
        A model reads that constant in a `compile if` and takes a different
        branch, so answering it wrong does not produce different code around
        the same behaviour: it compiles a DIFFERENT MODEL.

        :meth:`resolved_target_cfg` stays the target's answer with no options in
        hand, and `pssc targets` still reads it. That answer must be the DEFAULT
        one -- what a bare command line gets -- so that the listing is truthful
        rather than merely non-empty.
        """
        return self.resolved_target_cfg()

    def add_args(self, parser: argparse.ArgumentParser) -> None:  # noqa: B027
        """Contribute target-specific options to the ``compile`` parser.

        Optional; the default contributes nothing. Option names must be unique
        across targets (they are all registered on the one ``compile`` parser).
        """

    # -- `-X NAME=VALUE` options --------------------------------------------

    @staticmethod
    def parse_target_opts(opts: argparse.Namespace) -> Dict[str, str]:
        """Parse ``-X NAME=VALUE`` pairs off ``opts`` into a dict.

        A bare ``NAME`` means ``NAME=true``, which is what makes ``-X verbose``
        behave the way anyone typing it expects. Later wins, so a wrapper script
        can prepend defaults and let the user's own ``-X`` override them.
        """
        out: Dict[str, str] = {}
        for item in (getattr(opts, "target_opts", None) or []):
            name, sep, value = str(item).partition("=")
            name = name.strip()
            if not name:
                raise ValueError(
                    f"malformed -X option {item!r}: expected NAME=VALUE")
            out[name] = value.strip() if sep else "true"
        return out

    def opt(self, opts: argparse.Namespace, name: str, default=None,
            choices: Optional[Sequence[str]] = None):
        """Read the ``-X <name>=...`` option, or ``default`` if it was not given.

        ``choices`` is validated here rather than in each target, because the
        alternative is what argparse already taught us to avoid: every backend
        writing its own "unrecognized value" message, half of them not writing
        one, and a typo becoming a silent fallback to the default.
        """
        value = self.parse_target_opts(opts).get(name, default)
        if choices is not None and value is not None and value not in choices:
            raise ValueError(
                f"target '{self.name}': -X {name}={value!r} is not one of "
                f"{', '.join(map(str, choices))}")
        return value

    def opt_bool(self, opts: argparse.Namespace, name: str,
                 default: bool = False) -> bool:
        """:meth:`opt` for a flag. Accepts true/false, 1/0, yes/no, on/off."""
        raw = self.parse_target_opts(opts).get(name)
        if raw is None:
            return default
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(
            f"target '{self.name}': -X {name}={raw!r} is not a boolean "
            f"(true/false, 1/0, yes/no, on/off)")

    def prelude(self, opts: argparse.Namespace) -> List[Tuple[str, str]]:
        """PSS source units to process BEFORE the user's sources.

        Returns ``(name, text)`` pairs. ``name`` is reported in diagnostics and
        is deliberately not a real path.

        The default renders :attr:`target_cfg` as ``target_cfg_pkg``, with any
        ``--target-cfg NAME=VALUE`` overrides applied on top. Order matters and
        is the caller's responsibility to preserve: ``compile if`` reads
        constants only from previously-processed source units (PSS 3.1 §19.1.2),
        so a prelude that arrives late is not a diagnostic -- it is a silent
        fallback to whatever default the model declares.

        Override this to inject anything else a target needs in scope first.
        """
        overrides = _target_cfg.parse_overrides(
            getattr(opts, "target_cfg", None))
        declared = self.resolved_target_cfg_for(opts)
        if declared is None and not overrides:
            return []
        cfg: Dict[str, bool] = dict(declared or {})
        cfg.update(overrides)
        return [(_target_cfg.source_name(self.name),
                 _target_cfg.render(self.name, cfg))]

    @abc.abstractmethod
    def run(self, ctx, opts: argparse.Namespace) -> List[Path]:
        """Generate output for ``ctx`` under ``opts``; return written file paths.

        ``ctx`` is the PSS :class:`pssc.ast2ir.AstToIrContext`, which the driver
        enriches with ``ctx.ir_context`` — the canonical
        :class:`zuspec.ir.core.Context`. Built-in targets read the PSS context
        directly (the SV path needs ``parent_comp_names``; the Python path needs
        the whole context); external Phase-3 backends consume ``ctx.ir_context``.
        """
