"""The extension guide's examples must be real code from this repository.

Exit criterion 6 of the extension work: "`docs/custom-generator-styles.md`
covers all four levels, and every example in it is executed by a test". This is
what enforces the second half. An extension guide whose examples are not run
rots within two releases, and a rotted extension guide is worse than none -- it
costs the reader a day before they conclude the docs are wrong, and by then they
have stopped believing the parts that were still true.

The mechanism is deliberately dumb: a fenced block whose first line is a `#`
comment naming a repository file must appear VERBATIM in that file. No
extraction DSL, no doctest runner, no execution of the doc itself -- the code
runs because the file it was quoted from is on a test path, which is a much
stronger guarantee than a snippet that merely imports cleanly.

Blocks with no such header are prose illustrations. They are still checked, but
only for the things that can be checked without a source of truth: that a
`--flag` they name exists, and that a file they link to does.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"

#: The guide, plus the reference it hands off to. Both quote code.
_GUIDE = _DOCS / "custom-generator-styles.md"
_PAGES = (_GUIDE, _DOCS / "extension-stability.md")

_FENCE = re.compile(r"^```(\w*)\n(.*?)^```$", re.MULTILINE | re.DOTALL)

#: A first line like `# src/pssc/targets/py_progseq_tgt.py`, and nothing else.
_SOURCE_HEADER = re.compile(r"^#\s+([\w./-]+\.(?:py|toml|yaml|json|pss))\s*$")


class Block:
    """One fenced code block: where it came from, and what it says."""

    def __init__(self, page: Path, lang: str, text: str, index: int):
        self.page, self.lang, self.index = page, lang, index
        lines = text.splitlines()
        m = _SOURCE_HEADER.match(lines[0]) if lines else None
        self.source = _ROOT / m.group(1) if m else None
        self.body = "\n".join(lines[1:] if m else lines).rstrip("\n")

    def __repr__(self) -> str:
        where = self.source.relative_to(_ROOT) if self.source else "(prose)"
        return f"{self.page.name}#{self.index} [{self.lang}] {where}"


def _blocks(page: Path):
    if not page.is_file():
        return []
    text = page.read_text()
    return [Block(page, m.group(1), m.group(2), i)
            for i, m in enumerate(_FENCE.finditer(text))]


_ALL = [b for page in _PAGES for b in _blocks(page)]
_QUOTED = [b for b in _ALL if b.source is not None]


def test_the_guide_exists_and_quotes_real_code():
    """The guard on every other test here: an empty list passes vacuously, and
    a doc that stops quoting its sources is exactly what this file is for."""
    assert _GUIDE.is_file(), f"{_GUIDE} is missing"
    quoted = [b for b in _QUOTED if b.page == _GUIDE]
    assert len(quoted) >= 8, (
        f"the guide quotes {len(quoted)} source blocks; it covers four levels "
        f"plus options, capabilities, legality and runtime source, so this is "
        f"too few to be complete")


@pytest.mark.parametrize("block", _QUOTED, ids=repr)
def test_a_quoted_block_is_verbatim_from_its_source(block: Block):
    """The whole mechanism.

    A block may quote a CONTIGUOUS slice of its file -- there is no elision
    syntax on purpose, because a `...` in the middle is where a quotation stops
    being checkable and starts being a paraphrase that drifts.
    """
    assert block.source.is_file(), (
        f"{block} names a file that does not exist. Either the path is wrong "
        f"or the example moved and the guide did not")

    text = block.source.read_text()
    assert block.body in text, (
        f"{block} is not verbatim in {block.source.relative_to(_ROOT)}.\n"
        f"Quoted:\n{block.body[:400]}\n\n"
        f"Fix the DOC, not the source: the source is what the test suite runs, "
        f"and the guide is what the reader believes")


def test_every_quoted_source_is_on_a_test_path():
    """Verbatim is only half the claim. A block quoted from a file nothing
    exercises is still a snippet nobody runs -- it just happens to be a snippet
    that also sits in the tree.
    """
    exercised = ("src/pssc/", "tests/")
    for block in _QUOTED:
        rel = str(block.source.relative_to(_ROOT))
        assert rel.startswith(exercised), (
            f"{block} quotes {rel}, which is not under a path pssc's own suite "
            f"runs. Quote from the implementation or from a test")


# -- the unquoted blocks -----------------------------------------------------

def _declared_flags() -> set:
    """Options the doc's own examples declare, which the CLI will not know:
    they belong to plugin targets that are not installed."""
    out = set()
    for block in _ALL:
        out |= set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', block.body))
        out |= set(re.findall(r'"(--[\w-]+)"', block.body))
    return out


def _help_flags(subcommand: str) -> set:
    """The options `pssc <subcommand> --help` offers, as a set."""
    import io
    from contextlib import redirect_stdout

    from pssc import cli

    buf = io.StringIO()
    with pytest.raises(SystemExit), redirect_stdout(buf):
        cli.main([subcommand, "--help"])
    return set(re.findall(r"--[\w-]+", buf.getvalue()))


def test_every_flag_the_docs_show_is_a_real_option():
    """A bash example is the one kind that cannot be quoted from source, so it
    is the one that rots first -- and a renamed flag in a copy-pasteable
    command line fails for the reader, not for us.

    Checked against the SUBCOMMAND the example actually invokes: `--overrides`
    is real and `pssc compile` has never heard of it.
    """
    declared = _declared_flags()
    for block in _ALL:
        if block.lang != "bash":
            continue
        # Join shell continuations first, or every flag after a trailing `\`
        # is silently unchecked -- which is where the long examples put them.
        for line in block.body.replace("\\\n", " ").splitlines():
            words = line.split()
            if len(words) < 2 or words[0] != "pssc" or words[1].startswith("-"):
                continue
            known = _help_flags(words[1]) | declared
            for flag in re.findall(r"(?<![\w-])--[\w-]+", line):
                assert flag in known, (
                    f"{block} shows `{flag}`, which `pssc {words[1]} --help` "
                    f"does not offer. Either the flag was renamed or the "
                    f"example is wrong")


def test_every_doc_link_resolves():
    """Relative links between the docs pages. Cheap, and the failure they catch
    -- a page renamed, a hand-off left pointing at nothing -- is the one a
    reader hits first."""
    link = re.compile(r"\[[^\]]+\]\((?!https?:)([^)#]+)")
    for page in _PAGES:
        if not page.is_file():
            continue
        for target in link.findall(page.read_text()):
            assert (page.parent / target).exists(), (
                f"{page.name} links to '{target}', which does not exist")
