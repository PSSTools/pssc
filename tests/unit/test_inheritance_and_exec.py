"""Tests for action/struct inheritance, abstract actions, and exec blocks."""
import pytest
from pssc import Parser
from pssparser.core import Factory
from pssc.ast2ir import AstToIrTranslator, AstToIrContext
from zuspec.dataclasses import ir

Factory.inst()


def parse_and_translate(pss_text: str):
    parser = Parser()
    parser.parses([('test.pss', pss_text)])
    root = parser.link()
    translator = AstToIrTranslator()
    return translator.translate(root)


# -------------------------------------------------------------------
# Action inheritance
# -------------------------------------------------------------------

class TestActionInheritance:
    def test_action_inherits_super_name(self):
        ctx = parse_and_translate("""
            component C {
                action base_a { int a; }
                action derived_a : base_a { int b; }
            }
        """)
        derived = ctx.type_map.get("C::derived_a")
        assert derived is not None
        assert isinstance(derived.super, ir.DataTypeRef)
        assert derived.super.ref_name == "C::base_a"   # as linked, not as spelled

    def test_base_action_has_no_super(self):
        ctx = parse_and_translate("""
            component C { action base_a { int a; } }
        """)
        base = ctx.type_map.get("C::base_a")
        assert base is not None
        assert base.super is None

    def test_action_inheritance_fields_preserved(self):
        ctx = parse_and_translate("""
            component C {
                action base_a { int a; }
                action derived_a : base_a { int b; int c; }
            }
        """)
        derived = ctx.type_map.get("C::derived_a")
        assert len(derived.fields) == 2
        field_names = {f.name for f in derived.fields}
        assert field_names == {"b", "c"}

    def test_deep_action_inheritance_chain(self):
        ctx = parse_and_translate("""
            component C {
                action a0 { int x; }
                action a1 : a0 { int y; }
                action a2 : a1 { int z; }
            }
        """)
        a1 = ctx.type_map.get("C::a1")
        a2 = ctx.type_map.get("C::a2")
        assert isinstance(a1.super, ir.DataTypeRef)
        assert a1.super.ref_name == "C::a0"
        assert isinstance(a2.super, ir.DataTypeRef)
        assert a2.super.ref_name == "C::a1"


# -------------------------------------------------------------------
# Abstract actions
# -------------------------------------------------------------------

class TestAbstractActions:
    def test_abstract_action_flag_set(self):
        ctx = parse_and_translate("""
            component C { abstract action base_a { int a; } }
        """)
        base = ctx.type_map.get("C::base_a")
        assert base is not None
        assert base.is_abstract is True

    def test_non_abstract_action_flag_false(self):
        ctx = parse_and_translate("""
            component C { action normal_a { int a; } }
        """)
        a = ctx.type_map.get("C::normal_a")
        assert a is not None
        assert a.is_abstract is False

    def test_abstract_action_with_inheritance(self):
        ctx = parse_and_translate("""
            component C {
                abstract action base_a { int a; }
                action derived_a : base_a { int b; }
            }
        """)
        base = ctx.type_map.get("C::base_a")
        derived = ctx.type_map.get("C::derived_a")
        assert base.is_abstract is True
        assert derived.is_abstract is False
        assert derived.super.ref_name == "C::base_a"   # as linked, not as spelled


# -------------------------------------------------------------------
# Struct inheritance
# -------------------------------------------------------------------

class TestStructInheritance:
    def test_struct_inherits_super_name(self):
        ctx = parse_and_translate("""
            struct base_s { int a; }
            struct derived_s : base_s { int b; }
        """)
        derived = ctx.type_map.get("derived_s")
        assert derived is not None
        assert isinstance(derived.super, ir.DataTypeRef)
        assert derived.super.ref_name == "base_s"

    def test_base_struct_has_no_super(self):
        ctx = parse_and_translate("""
            struct base_s { int a; }
        """)
        base = ctx.type_map.get("base_s")
        assert base is not None
        assert base.super is None

    def test_struct_inheritance_fields_own_only(self):
        ctx = parse_and_translate("""
            struct base_s { int a; }
            struct derived_s : base_s { int b; int c; }
        """)
        derived = ctx.type_map.get("derived_s")
        assert len(derived.fields) == 2
        field_names = {f.name for f in derived.fields}
        assert field_names == {"b", "c"}


# -------------------------------------------------------------------
# Struct exec blocks
# -------------------------------------------------------------------

class TestStructExecBlocks:
    def test_struct_pre_solve_translated(self):
        ctx = parse_and_translate("""
            struct s {
                rand int x;
                exec pre_solve { x = 0; }
            }
        """)
        s = ctx.type_map.get("s")
        assert s is not None
        func_names = [f.name for f in s.functions]
        assert 'pre_solve' in func_names

    def test_struct_post_solve_translated(self):
        ctx = parse_and_translate("""
            struct s {
                rand int x;
                exec post_solve { x = 1; }
            }
        """)
        s = ctx.type_map.get("s")
        func_names = [f.name for f in s.functions]
        assert 'post_solve' in func_names

    def test_struct_both_exec_blocks(self):
        ctx = parse_and_translate("""
            struct s {
                rand int x;
                exec pre_solve { x = 0; }
                exec post_solve { x = 1; }
            }
        """)
        s = ctx.type_map.get("s")
        func_names = [f.name for f in s.functions]
        assert 'pre_solve' in func_names
        assert 'post_solve' in func_names

    def test_struct_exec_not_async(self):
        ctx = parse_and_translate("""
            struct s { rand int x; exec pre_solve { x = 0; } }
        """)
        s = ctx.type_map.get("s")
        pre = next(f for f in s.functions if f.name == 'pre_solve')
        assert pre.is_async is False


# -------------------------------------------------------------------
# Component exec blocks
# -------------------------------------------------------------------

class TestComponentExecBlocks:
    def test_component_init_down_translated(self):
        ctx = parse_and_translate("""
            component C { exec init_down { } }
        """)
        c = ctx.type_map.get("C")
        func_names = [f.name for f in c.functions]
        assert 'init_down' in func_names

    def test_component_init_up_translated(self):
        ctx = parse_and_translate("""
            component C { exec init_up { } }
        """)
        c = ctx.type_map.get("C")
        func_names = [f.name for f in c.functions]
        assert 'init_up' in func_names

    def test_component_init_translated_as_init_up(self):
        ctx = parse_and_translate("""
            component C { exec init { } }
        """)
        c = ctx.type_map.get("C")
        func_names = [f.name for f in c.functions]
        assert 'init_up' in func_names

    def test_component_run_start_translated(self):
        ctx = parse_and_translate("""
            component C { exec run_start { } }
        """)
        c = ctx.type_map.get("C")
        func_names = [f.name for f in c.functions]
        assert 'run_start' in func_names

    def test_component_run_end_translated(self):
        ctx = parse_and_translate("""
            component C { exec run_end { } }
        """)
        c = ctx.type_map.get("C")
        func_names = [f.name for f in c.functions]
        assert 'run_end' in func_names

    def test_component_all_exec_blocks(self):
        ctx = parse_and_translate("""
            component C {
                exec init_down { }
                exec init_up { }
                exec run_start { }
                exec run_end { }
            }
        """)
        c = ctx.type_map.get("C")
        func_names = [f.name for f in c.functions]
        assert 'init_down' in func_names
        assert 'init_up' in func_names
        assert 'run_start' in func_names
        assert 'run_end' in func_names

    def test_component_exec_blocks_not_async(self):
        ctx = parse_and_translate("""
            component C { exec init_down { } exec run_start { } }
        """)
        c = ctx.type_map.get("C")
        for f in c.functions:
            assert f.is_async is False
