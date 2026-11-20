"""Src-spec resolution: what `pssc test <spec>` loads.

Order is asserted explicitly in several places. It is part of the contract --
the list feeds `driver.translate`, and pssc has a whole test file about file
order mattering (test_file_order_independence.py).
"""
from __future__ import annotations

import pytest

from pssc.testing.sources import (
    PSSUNIT_PKG, SourceSpecError, pssunit_sources, resolve_sources,
    all_sources)


@pytest.fixture
def suite(tmp_path):
    """A small tree: two files at the top, one nested, one non-PSS."""
    (tmp_path / "b_c.pss").write_text("// b\n")
    (tmp_path / "a_c.pss").write_text("// a\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c_c.pss").write_text("// c\n")
    (tmp_path / "notes.md").write_text("not pss\n")
    return tmp_path


def _names(paths):
    return [p.name for p in paths]


def test_a_file_spec_is_itself(suite):
    assert _names(resolve_sources([str(suite / "a_c.pss")])) == ["a_c.pss"]


def test_a_directory_is_searched_recursively_and_sorted(suite):
    found = resolve_sources([str(suite)])
    assert _names(found) == ["a_c.pss", "b_c.pss", "c_c.pss"]


def test_non_pss_files_are_not_collected(suite):
    assert "notes.md" not in _names(resolve_sources([str(suite)]))


def test_a_glob_is_expanded(suite):
    assert _names(resolve_sources([str(suite / "*.pss")])) == ["a_c.pss", "b_c.pss"]


def test_no_specs_means_the_current_directory(suite, monkeypatch):
    monkeypatch.chdir(suite)
    assert _names(resolve_sources([])) == ["a_c.pss", "b_c.pss", "c_c.pss"]


def test_overlapping_specs_do_not_duplicate_a_file(suite):
    found = resolve_sources([str(suite), str(suite / "a_c.pss")])
    assert _names(found) == ["a_c.pss", "b_c.pss", "c_c.pss"]


# --- filelists --------------------------------------------------------------

def test_filelist_entries_are_relative_to_the_filelist(suite, tmp_path,
                                                       monkeypatch):
    lst = suite / "sub" / "files.f"
    lst.write_text("c_c.pss\n")
    monkeypatch.chdir(tmp_path.parent)     # deliberately not the filelist's dir
    assert _names(resolve_sources([f"@{lst}"])) == ["c_c.pss"]


def test_filelist_strips_comments_and_blank_lines(suite):
    lst = suite / "files.f"
    lst.write_text("# a comment\n\na_c.pss  # trailing\n   \nb_c.pss\n")
    assert _names(resolve_sources([f"@{lst}"])) == ["a_c.pss", "b_c.pss"]


def test_filelist_entries_may_be_directories(suite):
    lst = suite / "files.f"
    lst.write_text("sub\n")
    assert _names(resolve_sources([f"@{lst}"])) == ["c_c.pss"]


def test_a_self_including_filelist_is_an_error_not_a_hang(suite):
    lst = suite / "files.f"
    lst.write_text(f"@{lst}\n")
    with pytest.raises(SourceSpecError) as e:
        resolve_sources([f"@{lst}"])
    assert "itself" in str(e.value)


def test_a_filelist_error_names_the_line(suite):
    lst = suite / "files.f"
    lst.write_text("a_c.pss\nnosuch.pss\n")
    with pytest.raises(SourceSpecError) as e:
        resolve_sources([f"@{lst}"])
    assert "files.f:2" in str(e.value)


# --- failure ----------------------------------------------------------------

def test_a_spec_matching_nothing_is_an_error(suite):
    """Not an empty contribution.

    A mistyped spec that quietly adds no files ends as a green run over a suite
    that was never loaded.
    """
    with pytest.raises(SourceSpecError) as e:
        resolve_sources([str(suite / "nosuch*.pss")])
    assert "nosuch" in str(e.value)


def test_an_empty_directory_is_an_error(tmp_path):
    with pytest.raises(SourceSpecError):
        resolve_sources([str(tmp_path)])


# --- the built-in package ---------------------------------------------------

def test_pssunit_pkg_is_shipped_and_locatable():
    """Through importlib.resources, not __file__.

    The package-data glob that ships this file (`std_libs/*.pss`) does not cross
    a directory boundary, so a `pssunit_pkg/` directory would work perfectly in
    a development checkout and ship as nothing in a wheel. Resolving it the way
    an installed copy would is what makes this test able to catch that.
    """
    found = pssunit_sources()
    assert [p.name for p in found] == [PSSUNIT_PKG]
    assert found[0].is_file()


def test_the_framework_package_is_declared_ahead_of_user_sources(suite):
    """Order, not membership: `pssunit_pkg::test_a` must be in scope first."""
    found = all_sources([str(suite)])
    assert found[0].name == PSSUNIT_PKG
    assert _names(found[1:]) == ["a_c.pss", "b_c.pss", "c_c.pss"]


def test_pssunit_pkg_declares_what_discovery_looks_for():
    text = pssunit_sources()[0].read_text()
    assert "package pssunit_pkg" in text
    assert "abstract action test_a" in text
    assert "annotation test" in text
