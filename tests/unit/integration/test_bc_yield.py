"""`yield;` on bc (LRM 20.7.14): the running exec block steps aside for the
other ready threads, then resumes (P1.0).

It was refused as "unsupported statement StmtYield", although both engines
already had the YIELD op. The observation is interleaving: two parallel
bodies that yield after each step alternate, and without the yield each runs
to completion first.
"""
from .test_activity_bc_runs import trace

_PAR = """\
import std_pkg::*;
component pss_top {
    action P1 { exec body { int i = 0; while (i < 2) {
        message(NONE, "1.%%d", i); %s i += 1; } } }
    action P2 { exec body { int i = 0; while (i < 2) {
        message(NONE, "2.%%d", i); %s i += 1; } } }
    action T { activity { parallel { do P1; do P2; } } }
}
"""


def test_yield_interleaves_parallel_bodies():
    assert trace(_PAR % ("yield;", "yield;")) == ["1.0", "2.0", "1.1", "2.1"]


def test_without_yield_each_body_runs_to_completion():
    """The control: the interleaving above is the yield's doing."""
    assert trace(_PAR % ("", "")) == ["1.0", "1.1", "2.0", "2.1"]
