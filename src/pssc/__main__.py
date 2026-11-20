"""``python -m pssc`` -- the same entry point as the ``pssc`` console script.

Worth having on its own terms (a checkout with no installed console script is
still runnable), and required by the plugin integration tests, which run the
CLI in a subprocess with a plugin prefix on ``PYTHONPATH``.
"""
from ._entry import main

if __name__ == "__main__":
    raise SystemExit(main())
