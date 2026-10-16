"""PSS string methods (LRM 7.6.3) for the generated Python module.

`str.find` and `str.rfind` are nearly PSS's `find` and `find_last`; the
difference is the empty sub-string, which PSS finds at 0 and at the string's
size respectively, whatever the start. As with `pss_int.py`, each helper a
generated body calls is copied into that module, so a helper uses nothing but
builtins.
"""
from __future__ import annotations

import inspect
from typing import Dict


def _pss_find(s, sub, first=0):
    """`s.find(sub, first)`: where `sub` first starts at or after `first`,
    or -1; an empty `sub` is at 0."""
    if sub == "":
        return 0
    return s.find(sub, first)


def _pss_find_last(s, sub, first=-1):
    """`s.find_last(sub, first)`: where `sub` last starts at or before
    `first` (the end of `s` if -1), or -1; an empty `sub` is at the size."""
    if sub == "":
        return len(s)
    if first < 0:
        return s.rfind(sub)
    return s.rfind(sub, 0, first + len(sub))


#: The functions a generated module may call, by name.
HELPERS: Dict[str, object] = {
    f.__name__: f for f in (_pss_find, _pss_find_last)}


def inline_source(names) -> str:
    """The source of the helpers in *names*, in a fixed order, for a module."""
    names = set(names)
    return "\n\n".join(inspect.getsource(f) for n, f in HELPERS.items()
                       if n in names)
