"""Rendering of front-end diagnostics.

The front end already produces STRUCTURED diagnostics: every marker
``pssparser`` collects carries a severity, a message, a file, a line, a column,
the extent of the offending token and a diagnostic code (see
``pssparser.parser.Parser._collectMarkers``). What reaches a user, though, is
whatever someone chose to print -- and a location rendered at the END of a
sentence is a location no editor, IDE problem matcher or log filter can act on.

This module is the one place that turns a marker into text::

    exp_kw.pss:1:8: error: syntax error at 'action' [PSS028]
        export action pss_top::entry_a();
               ^~~~~~

``file:line:col: severity: message`` is the form every tool in the chain
already knows how to parse, and the caret answers "where" without the reader
counting columns.

A marker may carry ``notes``: lines rendered under the snippet. A parse error
can only say what the parser expected; a note is how a guard that KNOWS the
mistake (:func:`pssc.frontend._reject_export_action`) says what to write
instead.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: Columns a tab occupies when a snippet is rendered. The caret is positioned
#: against the expanded text, so this only has to be self-consistent.
TAB_WIDTH = 4

#: Indent for the source snippet and its caret, relative to the header line.
_SNIPPET_INDENT = "    "


def _line_of(text: str, line: int) -> Optional[str]:
    """Return 1-based *line* of *text*, or None if it is out of range."""
    if line is None or line < 1:
        return None
    lines = text.splitlines()
    if line > len(lines):
        return None
    return lines[line - 1]


def _source_line(marker: Mapping[str, Any],
                 sources: Optional[Mapping[str, str]]) -> Optional[str]:
    """The source line a marker points at, from *sources* or from disk.

    In-memory units (a target's ``target_cfg_pkg`` prelude, or text handed to
    ``Parser.parses``) have a name but no file, so a caller that has the text
    passes it in ``sources``. Anything else is read back from disk; a file that
    has since changed or vanished simply yields no snippet, because a snippet
    that does not match the source is worse than none.
    """
    path = marker.get("file")
    if not path:
        return None
    if sources and path in sources:
        return _line_of(sources[path], marker.get("line"))
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r") as fp:
            return _line_of(fp.read(), marker.get("line"))
    except OSError:
        return None


def _caret(line_text: str, col: int, extent: int) -> str:
    """A caret/tilde run under the token at 1-based *col*, spanning *extent*.

    Positioned against the TAB-EXPANDED text, so the caret lands under the
    token in a terminal rather than under wherever the raw column index fell.
    """
    col = max(1, int(col or 1))
    prefix_width = len(line_text[:col - 1].expandtabs(TAB_WIDTH))
    width = max(1, int(extent or 1))
    # Do not run past the end of the line: an extent that overshoots (an
    # unterminated token, a marker at EOF) would otherwise draw tildes into
    # empty space.
    remaining = max(1, len(line_text.expandtabs(TAB_WIDTH)) - prefix_width)
    width = min(width, remaining)
    return " " * prefix_width + "^" + "~" * (width - 1)


def format_marker(marker: Mapping[str, Any],
                  sources: Optional[Mapping[str, str]] = None) -> str:
    """Render one marker as a (possibly multi-line) diagnostic block.

    The first line is always ``file:line:col: severity: message``. The source
    line and caret follow when the source can be recovered, then any ``notes``.
    """
    path = marker.get("file") or "<unknown>"
    line = marker.get("line")
    col = marker.get("col")
    severity = marker.get("severity") or "error"
    message = marker.get("message") or ""
    code = marker.get("code")

    where = path
    if line:
        where += ":%d" % line
        if col:
            where += ":%d" % col
    head = "%s: %s: %s" % (where, severity, message)
    if code:
        head += " [%s]" % code

    out = [head]

    line_text = _source_line(marker, sources)
    if line_text is not None:
        out.append(_SNIPPET_INDENT + line_text.expandtabs(TAB_WIDTH))
        out.append(_SNIPPET_INDENT + _caret(line_text, col,
                                            marker.get("extent")))

    for note in marker.get("notes") or []:
        out.append(_SNIPPET_INDENT + "note: " + str(note))

    return "\n".join(out)


def format_markers(markers: Sequence[Any],
                   sources: Optional[Mapping[str, str]] = None,
                   severities: Optional[Sequence[str]] = ("error",),
                   ) -> List[str]:
    """Render *markers*, one block per marker.

    *severities* selects what is rendered (errors only, by default). A marker
    that is not a mapping -- anything a future front end hands back -- is
    rendered with ``str()`` rather than dropped: an unrecognised diagnostic is
    still a diagnostic, and silence is the failure mode this module exists to
    remove.
    """
    out: List[str] = []
    for m in markers or []:
        if not isinstance(m, Mapping):
            out.append(str(m))
            continue
        if severities and (m.get("severity") or "error") not in severities:
            continue
        out.append(format_marker(m, sources))
    return out


def marker(message: str, *, file: str, line: int, col: int = 1,
           extent: int = 1, severity: str = "error",
           code: Optional[str] = None,
           notes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Build a marker dict in the shape ``pssparser`` produces.

    Used by the front-end guards, so a diagnostic pssc raises itself renders
    exactly like one the parser raised.
    """
    m: Dict[str, Any] = {
        "severity": severity,
        "message": message,
        "file": file,
        "line": line,
        "col": col,
        "extent": extent,
        "related": [],
    }
    if code:
        m["code"] = code
    if notes:
        m["notes"] = list(notes)
    return m


def indent_block(text: str, prefix: str = "  ") -> str:
    """Prefix EVERY line of *text*, so a caret stays under its token.

    Indenting only the first line of a multi-line diagnostic is what turns a
    caret into noise; this is why the CLI does not simply print ``"  " + err``.
    """
    return "\n".join(prefix + line for line in text.split("\n"))


# -- diagnostics from the IR -------------------------------------------------
#
# A refusal that names ``pss_top::run`` sends the reader looking through the
# function; one that names ``m.pss:12:5`` sends them to the line, and lets a
# consumer of locations -- the corpus checker's ``diag_location`` rule, an
# editor -- point at it (plan P5, docs/design/construct-test-audit/00-plan.md).
# ast2ir gives every statement, expression, function, parameter, field,
# component and struct the ``loc`` its AST node had.

class Located(str):
    """A diagnostic's text prefixed ``file:line:col: ``, carrying the parts.

    A ``str``, so every caller that joins, prints or greps diagnostics is
    unchanged; `pssc.driver.CompileError.markers` reads the parts.
    """

    file: Optional[str]
    line: int
    col: int
    message: str

    def __new__(cls, loc: Any, message: str) -> "Located":
        file = getattr(loc, "file", None)
        line = int(getattr(loc, "line", 0) or 0)
        col = int(getattr(loc, "pos", 0) or 0) or 1
        self = super().__new__(
            cls, f"{file or '<unknown>'}:{line}:{col}: {message}")
        self.file, self.line, self.col, self.message = file, line, col, message
        return self

    def marker(self) -> Dict[str, Any]:
        """This diagnostic as a pssparser-shaped marker (`marker`)."""
        return marker(self.message, file=self.file, line=self.line,
                      col=self.col)


def loc_of(*nodes: Any) -> Optional[Any]:
    """The first located ``loc`` among *nodes* (``None`` entries skipped)."""
    for n in nodes:
        loc = getattr(n, "loc", None)
        if loc is not None and getattr(loc, "line", 0):
            return loc
    return None


def at(nodes: Any, message: str) -> str:
    """*message* located at the first of *nodes* -- one node, or a sequence
    from most to least specific -- that has a location; *message* itself if
    none does. A node a pass synthesized has no ``loc``: pass the node it
    came from, or its statement, after it. An invented location is worse
    than none, so there is no default."""
    if not isinstance(nodes, (list, tuple)):
        nodes = (nodes,)
    loc = loc_of(*nodes)
    return Located(loc, message) if loc is not None else message


class Refusal(ValueError):
    """A construct of the model refused from inside a pass or an emitter,
    with the node it is at.

    A pass deep in lowering that meets something it will not render (a
    `sum()` whose array contains a call, an `in` over a value it cannot
    enumerate) has no list of diagnostics to append to. A bare
    ``ValueError`` from there reached the user as an internal error, with
    no location. This one is the MODEL's error: `OpModelTarget.run` reports
    it as a `CompileError`, located at the first of ``nodes`` that has a
    location. It is still a ``ValueError``, so a caller that catches one
    keeps working.
    """

    def __init__(self, message: str, *nodes: Any) -> None:
        super().__init__(message)
        self.nodes = nodes

    def located(self) -> str:
        return at(self.nodes, str(self))

    def at_least(self, *nodes: Any) -> "Refusal":
        """Add *nodes* as the least specific places this is at: an emitter
        adds the statement it was rendering, so a site deep inside one
        needs no node of its own to be located."""
        self.nodes = tuple(self.nodes) + nodes
        return self
