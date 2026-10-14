"""Read a generated op-model-sv package as one text.

op-model-sv writes `<pkg>.sv` and one `.svh` per class, which the package
`` `include ``s. A test that asks "does the package contain X" means the
package as the compiler sees it: this expands the includes in place, from the
package's own directory, as `-I<dir>` does.
"""
from __future__ import annotations

import re
from pathlib import Path

_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"\s*$')


def sv_package_text(path) -> str:
    """``path``'s text, every `` `include "f" `` replaced by ``f``'s text
    (recursively) from the same directory."""
    path = Path(path)
    out = []
    for line in path.read_text().splitlines():
        m = _INCLUDE.match(line)
        if m:
            out.append(sv_package_text(path.parent / m.group(1)))
        else:
            out.append(line)
    return "\n".join(out)
