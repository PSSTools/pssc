"""End-to-end: PSS source → load_pss → registry.create(imports) → await ep.Action().

The API-only export-api workflow, exercised from the PSS frontend.
"""
import pytest

from pssc import load_pss

pytestmark = pytest.mark.anyio


async def test_atomic_action_runs_via_export_api():
    registry = load_pss("""
        component pss_top {
            action Entry {
                rand bit[8] addr;
                constraint addr % 4 == 0;
            }
        }
    """)
    # Auto-detected single root is exposed.
    ep = registry.create(seed=7)
    assert "Entry" in ep.actions
    result = await ep.Entry()
    assert result.addr % 4 == 0


async def test_import_target_and_solve_wired_end_to_end(capsys):
    registry = load_pss("""
        package dut_api {
            import target function void doit(int i);
            // Unqualified: a `solve` import may not be called from a body
            // (LRM 20.2.1.3, pssparser PSS144), and the runtime does not run
            // one from post_solve.
            import function int  getval(int i);
        }
        component pss_top {
            import dut_api::*;
            action Entry {
                exec body {
                    doit(getval(7));
                }
            }
        }
    """)

    seen = []

    class Imports:
        def getval(self, i):
            return i + 5

        def doit(self, i):
            seen.append(i)
            print(f"[imp] doit({i})")

    ep = registry.create(Imports())
    await ep.Entry()
    assert seen == [12]
    assert "[imp] doit(12)" in capsys.readouterr().out


async def test_registry_is_reusable_for_independent_runs():
    registry = load_pss("""
        component pss_top {
            action Entry { rand bit[8] addr; constraint addr % 4 == 0; }
        }
    """)
    ep1 = registry.create(seed=1)
    ep2 = registry.create(seed=1)
    r1 = await ep1.Entry()
    r2 = await ep2.Entry()
    assert r1.addr == r2.addr            # same seed → same solved value
    assert ep1._comp is not ep2._comp    # independent component trees


async def test_explicit_export_actions_select_subset():
    registry = load_pss(
        """
        component pss_top {
            action A { rand bit[4] x; }
            action B { rand bit[4] y; }
        }
        """,
        export_actions=["pss_top::A"],
    )
    ep = registry.create(seed=2)
    assert ep.actions == ["A"]
