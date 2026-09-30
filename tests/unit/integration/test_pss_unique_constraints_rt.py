"""PSS FE → RT tests: unique constraints (§16.1.9)."""
import pytest
from pssc import load_pss
from zuspec.dataclasses import randomize


def test_unique_two_fields():
    """unique { a, b }: two fields must have distinct values."""
    ns = load_pss("""
        struct Pair {
            rand bit[8] a;
            rand bit[8] b;
            constraint { unique { a, b }; }
        }
    """)
    for seed in range(20):
        p = ns.Pair()
        randomize(p, seed=seed)
        assert p.a != p.b, f"seed={seed}: a={p.a} == b={p.b} (must be unique)"


def test_unique_three_fields():
    """unique { a, b, c }: all three fields must have distinct values."""
    ns = load_pss("""
        struct Triple {
            rand bit[8] a;
            rand bit[8] b;
            rand bit[8] c;
            constraint { unique { a, b, c }; }
        }
    """)
    for seed in range(20):
        t = ns.Triple()
        randomize(t, seed=seed)
        assert t.a != t.b, f"seed={seed}: a={t.a} == b={t.b}"
        assert t.b != t.c, f"seed={seed}: b={t.b} == c={t.c}"
        assert t.a != t.c, f"seed={seed}: a={t.a} == c={t.c}"


def test_unique_with_domain_constraint():
    """unique combined with domain constraints."""
    ns = load_pss("""
        struct Sel {
            rand bit[4] x;
            rand bit[4] y;
            constraint x < 8;
            constraint y < 8;
            constraint { unique { x, y }; }
        }
    """)
    for seed in range(20):
        s = ns.Sel()
        randomize(s, seed=seed)
        assert s.x < 8, f"seed={seed}: x={s.x} >= 8"
        assert s.y < 8, f"seed={seed}: y={s.y} >= 8"
        assert s.x != s.y, f"seed={seed}: x={s.x} == y={s.y}"


# `unique` used to keep only the LAST element of each operand's path, so
# `unique {a.x, b.x}` became `unique {x, x}` -- a different, unsatisfiable
# constraint. Until StmtUnique carries expressions, a path operand is refused.
def test_unique_path_operand_is_a_located_error():
    from pssc import PssTranslationError
    with pytest.raises(PssTranslationError, match=r"line 6: unique operand 'a\.x'"):
        load_pss("""
            struct S { rand bit[4] x; }
            struct P {
                rand S a;
                rand S b;
                constraint { unique { a.x, b.x }; }
            }
        """)


def test_unique_single_operand_is_a_located_error():
    """`unique {arr}` (distinct elements) used to be dropped without a word."""
    from pssc import PssTranslationError
    with pytest.raises(PssTranslationError, match=r"line 4: unique with one operand"):
        load_pss("""
            struct P {
                rand bit[4] arr[4];
                constraint { unique { arr }; }
            }
        """)
