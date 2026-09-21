"""pssc front end: the single-pass ``Parser`` wrapper over ``pssparser``.

The source is parsed verbatim — no rewriting, no annotation side-channel. The
only front-end logic is a pair of guards that turn a cryptic parse error into a
specific one: the non-LRM ``fill`` statement (:func:`_reject_fill`) and the two
common mis-spellings of an exported action (:func:`_reject_export_action`).

Split out of the package ``__init__`` so the driver/CLI can reach the parser
without importing the IR/runtime/SV layers.
"""
from typing import List
from pssparser import Parser as _PssParser, ParseException

from . import diag as _diag


# ---------------------------------------------------------------------------
# PSS source text-transformation helpers
# ---------------------------------------------------------------------------

def _is_word_char(c: str) -> bool:
    return c.isalnum() or c == '_'


def _scan_comment_or_string(text: str, i: int) -> int:
    """Return end index after a comment or string at i, or -1 if not at one."""
    n = len(text)
    if text[i:i+2] == '//':
        end = text.find('\n', i)
        return n if end == -1 else end + 1
    if text[i:i+2] == '/*':
        end = text.find('*/', i + 2)
        return n if end == -1 else end + 2
    if text[i] == '"':
        j = i + 1
        while j < n and text[j] != '"':
            if text[j] == '\\':
                j += 1
            j += 1
        return min(j + 1, n)
    return -1


def _reject_fill(text: str, filename: str) -> None:
    """Raise a clear diagnostic when the non-LRM ``fill { ... }`` activity
    statement appears in *text*.

    ``fill`` (and its companion ``FILL`` placeholder) is a Perspec-specific
    extension, not part of the PSS LRM. It is no longer rewritten/inferred;
    surface it explicitly instead of letting it become a cryptic parse error or
    silently dropping the enclosed constraints. ``fill`` remains valid as an
    ordinary identifier (e.g. an action named ``fill``); only the
    statement-position block form ``fill { ... }`` is rejected.
    """
    n = len(text)
    i = 0
    while i < n:
        end = _scan_comment_or_string(text, i)
        if end != -1:
            i = end
            continue
        if (text[i:i+4] == "fill"
                and (i == 0 or not _is_word_char(text[i - 1]))
                and (i + 4 < n and not _is_word_char(text[i + 4]))):
            # Followed by '{' (skipping whitespace)?
            j = i + 4
            while j < n and text[j] in " \t\r\n":
                j += 1
            # Preceded (skipping whitespace) by a statement boundary? This
            # distinguishes the `fill { ... }` statement from a declaration that
            # merely names a type/action `fill` (e.g. `action fill { ... }`).
            k = i - 1
            while k >= 0 and text[k] in " \t\r\n":
                k -= 1
            if j < n and text[j] == "{" and (k < 0 or text[k] in "{;}"):
                line = text.count("\n", 0, i) + 1
                msg = ("%s:%d: 'fill' is not supported (non-LRM Perspec "
                       "extension). Rewrite the activity using "
                       "'repeat'/'replicate' or a coverage-driven loop."
                       % (filename, line))
                raise ParseException(msg, [_diag.marker(
                    "'fill' is not supported (non-LRM Perspec extension)",
                    file=filename, line=line, col=_col_of(text, i), extent=4,
                    code="PSSC001",
                    notes=["rewrite the activity using 'repeat'/'replicate' "
                           "or a coverage-driven loop"])])
        i += 1


def _col_of(text: str, i: int) -> int:
    """1-based column of offset *i* in *text*."""
    return i - text.rfind("\n", 0, i)


def _skip_trivia(text: str, i: int) -> int:
    """Advance past whitespace and comments starting at *i*."""
    n = len(text)
    while i < n:
        if text[i] in " \t\r\n":
            i += 1
            continue
        if text[i:i+2] in ("//", "/*"):
            end = _scan_comment_or_string(text, i)
            if end == -1:
                return i
            i = end
            continue
        return i
    return i


def _word_at(text: str, i: int) -> str:
    """The identifier/keyword starting at *i*, or "" if there is none."""
    n = len(text)
    j = i
    while j < n and _is_word_char(text[j]):
        j += 1
    return text[i:j]


def _reject_export_action(text: str, filename: str) -> None:
    """Raise an actionable diagnostic for the two ways an ``export`` of an
    action is commonly mis-written.

    PSS 3.1 §20.10::

        export_action ::= export [target|solve] action_type_identifier
                          function_parameter_list_prototype ;

    There is no ``action`` keyword, and the parameter list is not optional --
    so the two natural guesses are both rejected by the grammar, and the
    grammar can only say what token it did not expect:

        export action pss_top::entry_a();   ->  syntax error at 'action'
        export pss_top::entry_a;            ->  unexpected ';' expecting '('

    Neither says what to write instead. This guard recognises both shapes
    BEFORE the parser sees them and names the fix. It follows
    :func:`_reject_fill`: pssc already pre-scans source when it can turn a
    cryptic parse error into a specific one.

    Only statement-position ``export`` is examined, and ``export target
    function f;`` (an exported *function*, §20.9) is left alone.
    """
    n = len(text)
    i = 0
    while i < n:
        end = _scan_comment_or_string(text, i)
        if end != -1:
            i = end
            continue

        if not (text[i:i+6] == "export"
                and (i == 0 or not _is_word_char(text[i - 1]))
                and (i + 6 >= n or not _is_word_char(text[i + 6]))):
            i += 1
            continue

        # Statement position? (start of file, or after `{`, `}` or `;`)
        k = i - 1
        while k >= 0 and text[k] in " \t\r\n":
            k -= 1
        if k >= 0 and text[k] not in "{;}":
            i += 1
            continue

        j = _skip_trivia(text, i + 6)
        word = _word_at(text, j)

        # An optional platform qualifier precedes the target in both forms.
        if word in ("target", "solve"):
            j = _skip_trivia(text, j + len(word))
            word = _word_at(text, j)

        if word == "function":
            # `export [target|solve] function f;` -- an exported function.
            i += 1
            continue

        if word == "action":
            raise ParseException(
                "%s:%d: 'export action' is not PSS syntax -- an exported "
                "action is declared without the 'action' keyword."
                % (filename, text.count("\n", 0, j) + 1),
                [_diag.marker(
                    "'export action' is not PSS syntax -- an exported action "
                    "is declared without the 'action' keyword",
                    file=filename, line=text.count("\n", 0, j) + 1,
                    col=_col_of(text, j), extent=6, code="PSSC002",
                    notes=[
                        "drop 'action': export <action_type>(<params>);",
                        "PSS 3.1 §20.10: export [target|solve] "
                        "action_type_identifier (<params>);",
                    ])])

        if not word:
            i += 1
            continue

        # `export <qualified-id>` -- walk the type identifier and see whether a
        # parameter list follows. Only `;` in its place is diagnosable here;
        # anything else is left to the parser.
        m = j
        while m < n and (_is_word_char(text[m]) or text[m] == ":"
                         or text[m] in " \t\r\n"):
            m += 1
        if m < n and text[m] == ";":
            raise ParseException(
                "%s:%d: an exported action needs a parameter list, even an "
                "empty one." % (filename, text.count("\n", 0, m) + 1),
                [_diag.marker(
                    "an exported action needs a parameter list, even an empty "
                    "one", file=filename, line=text.count("\n", 0, m) + 1,
                    col=_col_of(text, m), extent=1, code="PSSC003",
                    notes=[
                        "write '%s();'" % text[j:m].strip(),
                        "PSS 3.1 §20.10: the parameters name which of the "
                        "action's fields appear in the exported signature",
                    ])])

        i += 1


class Parser(_PssParser):
    """pssparser.Parser wrapper.

    The PSS source is parsed verbatim — there is no source rewriting and no
    annotation side-channel. ``forall``, ``covergroup``, component ``bind``, and
    the state ``initial`` / resource ``instance_id`` built-ins are handled
    natively by pssparser. The non-LRM ``fill`` statement (:func:`_reject_fill`)
    and a mis-written exported action (:func:`_reject_export_action`) are
    rejected with explicit diagnostics. ``exec file`` parses natively
    (it is grammar-valid) but is not lowered — its builder is a stub, so it is
    accepted and ignored.

    Comments are collected by default. The generated operation model is a
    close transcription of its PSS source, so dropping the prose drops the
    part a reader cannot recover from the code. Pass ``collect_comments=False``
    (``--no-comments``) to get the pre-comment output back byte for byte.
    """

    def __init__(self, *args, collect_comments: bool = True, **kwargs):
        super().__init__(*args, collect_comments=collect_comments, **kwargs)

    def parse(self, files: List[str], prelude=()) -> bool:
        """Read and parse PSS ``files``, with ``prelude`` processed first.

        ``prelude`` is a sequence of ``(name, text)`` in-memory source units --
        typically a target's ``target_cfg_pkg`` (see
        :mod:`pssc.targets.target_cfg`). They are prepended rather than
        appended because ``compile if`` reads constants only from
        previously-processed source units (PSS 3.1 §19.1.2), and arriving late
        is silent: the model simply takes its default branch.
        """
        text_files = [(name, text) for name, text in prelude]
        for path in files:
            with open(path, 'r') as fh:
                src = fh.read()
            text_files.append((path, src))
        return self._guard_and_parse(text_files)

    def parses(self, text_files) -> bool:
        """Parse in-memory PSS texts."""
        return self._guard_and_parse(list(text_files))

    def _guard_and_parse(self, text_files: List[tuple]) -> bool:
        for fname, src in text_files:
            _reject_fill(src, fname)
            _reject_export_action(src, fname)
        return super().parses(text_files)

