"""The declared pssparser floor covers every pssparser API pssc reads.

An install that resolves from PyPI takes the floor at its word: a floor below
the release that introduced an API pssc uses gives a pssc that fails on its
first compile. `API_INTRODUCED` names each such API with the release it
first shipped in; add a row when pssc starts reading a new one.
"""
import re
import tomllib
from pathlib import Path

import pytest

import pssparser.ast as pss_ast

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

#: pssparser API -> the first release carrying it (pssparser CHANGELOG.md).
API_INTRODUCED = {
    "ast.FunctionAccess": (3, 6),      # private/protected functions (op_api)
    "ast.FunctionPrototype.getAccess": (3, 6),
}


def _floor(dist: str):
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    for d in deps:
        m = re.fullmatch(rf"{dist}\s*>=\s*(\d+)\.(\d+)(?:\.\d+)?", d.strip())
        if m:
            return int(m.group(1)), int(m.group(2))
    raise AssertionError(f"no '{dist}>=' floor in {PYPROJECT}")


@pytest.mark.parametrize("api,introduced", sorted(API_INTRODUCED.items()))
def test_the_pssparser_floor_carries_the_api(api, introduced):
    assert _floor("pssparser") >= introduced, (
        f"pssc reads pssparser {api}, first in {'.'.join(map(str, introduced))}; "
        f"raise the floor in pyproject.toml")


@pytest.mark.parametrize("api", sorted(API_INTRODUCED))
def test_the_api_exists_in_the_installed_pssparser(api):
    obj = pss_ast
    for part in api.split(".")[1:]:
        obj = getattr(obj, part)
