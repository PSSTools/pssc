"""Rendering PSS comments into a generated target language.

Shared by the SystemVerilog and C progseq emitters, which produce a close
transcription of their PSS source and so want the prose in the same places.

The three differ only in house style: the SV output uses `//` for statements
and `/** */` for documentation; the C output is uniformly `/* */`, which is
what every other comment it emits already looks like; a `#` language (Python,
Make, shell) has one form for both. All are selected with ``style``.

See docs/design/pss-comment-propagation-plan.md for where the text comes from, and why
orphaned comments are not among it.
"""
from typing import List, Optional

#: `//` line comments -- SystemVerilog statements.
LINE = "line"

#: `/* */` block comments -- everything in the C output.
BLOCK = "block"

#: `# ` line comments -- Python and any other `#` language.
#:
#: There is no close-comment marker to escape and no block form to fall back
#: to, so this style needs neither of the two escape hatches the others carry.
#: A `#` comment carries any content verbatim, which is why the LINE branch
#: below handles it unchanged.
HASH = "hash"

#: The comment introducer for each line style.
_MARKER = {LINE: "//", HASH: "#"}


def _has_close_marker(text: str) -> bool:
    return "*/" in text


def comment_lines(text: Optional[str], pad: str, style: str = LINE) -> List[str]:
    """Render *text* as a comment at indent *pad*.

    A blank source line stays blank rather than becoming a bare `//`, so the
    paragraph breaks in a doc comment survive as paragraph breaks.
    """
    if not text:
        return []

    lines = text.split("\n")

    if style in _MARKER or _has_close_marker(text):
        # A line comment carries any content verbatim, which is also the escape
        # hatch when the text contains a close-comment marker. Escaping it
        # inside a block comment would either corrupt the prose or read as an
        # escape rather than as prose.
        mark = _MARKER.get(style, "//")
        return ["%s%s%s" % (pad, mark, (" " + l) if l else "") for l in lines]

    if len(lines) == 1:
        return ["%s/* %s */" % (pad, lines[0])]

    out = ["%s/*" % pad]
    for l in lines:
        out.append("%s *%s" % (pad, (" " + l) if l else ""))
    out.append("%s */" % pad)
    return out


def doc_block(text: Optional[str], pad: str, style: str = LINE) -> List[str]:
    """Render *text* as a documentation block above a declaration.

    `/** */` in SystemVerilog -- the form doxygen-filter-sv reads -- and plain
    `/* */` in C, matching the surrounding output. Falls back to `//` lines
    when the text contains a close-comment marker.

    `#` has no documentation form distinct from its ordinary one, so HASH
    renders the same either way. That is the honest mapping rather than a
    missing feature: a generator wanting a docstring is emitting a string
    literal, not a comment, and should say so at the call site.
    """
    if not text:
        return []
    if style == HASH:
        return comment_lines(text, pad, HASH)
    if _has_close_marker(text):
        return comment_lines(text, pad, LINE)
    if style == BLOCK:
        return comment_lines(text, pad, BLOCK)

    lines = text.split("\n")
    if len(lines) == 1:
        return ["%s/** %s */" % (pad, lines[0])]

    out = ["%s/**" % pad]
    for l in lines:
        out.append("%s *%s" % (pad, (" " + l) if l else ""))
    out.append("%s */" % pad)
    return out


def blank_line(lines: List[str]) -> List[str]:
    """Separate what comes next from what precedes it.

    Declarations run together otherwise, and in a list of prototypes the eye
    has nothing else to break on. The separation belongs to the *declaration*,
    not to its documentation: an undocumented function needs it as much as a
    documented one, and a doc block butted against the previous prototype reads
    as a continuation of it.

    No-ops at the start of a section and where a blank line is already present,
    so it never doubles up. Returns *lines*, mutated, so it can be chained at a
    call site that is already accumulating.

    Tightly-coupled generated groups are deliberately *not* separated
    internally -- a sub-component's `ch()`/`ch_size()` pair, the
    `_init`/`_create`/`_destroy` lifecycle, the per-register accessor
    one-liners. Those are one member's plumbing rather than API entries with
    their own contracts, and spacing each apart would bury the operations.
    """
    if lines and lines[-1].strip():
        lines.append("")
    return lines


def append_trailing(lines: List[str], text: Optional[str],
                    style: str = LINE) -> List[str]:
    """Attach *text* to the end of the last line of *lines*.

    A trailing comment documents the statement it sits beside, so it has to
    stay on that line rather than become a line of its own. A statement that
    lowered to nothing takes its comment with it -- there is no longer anything
    for it to be beside.
    """
    if not text or not lines:
        return lines

    # A multi-line comment cannot ride on the end of a line; put it above the
    # statement instead, which is the only faithful place left.
    if "\n" in text:
        pad = lines[0][:len(lines[0]) - len(lines[0].lstrip())]
        return comment_lines(text, pad, style) + lines

    if style == BLOCK and not _has_close_marker(text):
        marker = "/* %s */" % text
    else:
        marker = "%s %s" % (_MARKER.get(style, "//"), text)
    out = list(lines)
    out[-1] = "%s   %s" % (out[-1], marker)
    return out
