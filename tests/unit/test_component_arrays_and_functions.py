"""
Tests for component arrays and function features.

Covers:
 - component arrays my_c insts[4] → DataTypeArray(element_type=DataTypeComponent, size=4)
 - pure function → is_invariant=True (NOTE: PSS frontend doesn't set is_pure, skipped)
 - function default params → Arguments.defaults contains translated defaults
 - varargs function → Arguments.vararg
"""
import unittest

import pytest

from pssc import Parser
from pssparser.core import Factory
from pssc.ast2ir import AstToIrTranslator, AstToIrContext
from zuspec.dataclasses import ir


class TestComponentArrays(unittest.TestCase):
    """Component array field AST→IR translation tests."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def test_component_array_field_translates(self):
        """my_c insts[4] → DataTypeArray field in parent component."""
        ctx = self.parse_and_translate("""
            component SubC {}
            component C {
                SubC insts[4];
            }
        """)
        comp = ctx.type_map.get("C")
        self.assertIsNotNone(comp)
        insts_field = next((f for f in comp.fields if f.name == "insts"), None)
        self.assertIsNotNone(insts_field, "No 'insts' field found")

    def test_component_array_type_is_array(self):
        """Component array field has DataTypeArray as datatype."""
        ctx = self.parse_and_translate("""
            component SubC {}
            component C {
                SubC insts[4];
            }
        """)
        comp = ctx.type_map.get("C")
        insts_field = next((f for f in comp.fields if f.name == "insts"), None)
        self.assertIsInstance(insts_field.datatype, ir.DataTypeArray)

    def test_component_array_element_type(self):
        """Component array element_type is DataTypeComponent."""
        ctx = self.parse_and_translate("""
            component SubC {}
            component C {
                SubC insts[4];
            }
        """)
        comp = ctx.type_map.get("C")
        insts_field = next((f for f in comp.fields if f.name == "insts"), None)
        array_type = insts_field.datatype
        self.assertIsInstance(array_type.element_type, ir.DataTypeComponent)

    def test_component_array_size(self):
        """Component array size=4."""
        ctx = self.parse_and_translate("""
            component SubC {}
            component C {
                SubC insts[4];
            }
        """)
        comp = ctx.type_map.get("C")
        insts_field = next((f for f in comp.fields if f.name == "insts"), None)
        self.assertEqual(insts_field.datatype.size, 4)

    def test_component_array_element_type_name(self):
        """Component array element type has correct name."""
        ctx = self.parse_and_translate("""
            component SubC {}
            component C {
                SubC insts[4];
            }
        """)
        comp = ctx.type_map.get("C")
        insts_field = next((f for f in comp.fields if f.name == "insts"), None)
        self.assertEqual(insts_field.datatype.element_type.name, "SubC")


class TestFunctionFeatures(unittest.TestCase):
    """Function feature AST→IR translation tests."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def _get_func(self, ctx, comp_key, func_name):
        comp = ctx.type_map.get(comp_key)
        self.assertIsNotNone(comp, f"Component not found: {comp_key}")
        func = next((f for f in comp.functions if f.name == func_name), None)
        self.assertIsNotNone(func, f"Function not found: {func_name}")
        return func

    def test_function_with_params_translates(self):
        """Function parameters are translated to ir.Arg with annotation."""
        ctx = self.parse_and_translate("""
            component C {
                function int add(int x, int y) {
                    return x;
                }
            }
        """)
        func = self._get_func(ctx, "C", "add")
        self.assertIsNotNone(func.args)
        self.assertEqual(len(func.args.args), 2)
        self.assertEqual(func.args.args[0].arg, "x")
        self.assertEqual(func.args.args[1].arg, "y")

    def test_function_param_has_type_annotation(self):
        """Function int parameter has DataTypeInt annotation."""
        ctx = self.parse_and_translate("""
            component C {
                function int add(int x) {
                    return x;
                }
            }
        """)
        func = self._get_func(ctx, "C", "add")
        param = func.args.args[0]
        self.assertIsInstance(param.annotation, ir.DataTypeInt)

    def test_function_default_param_translates(self):
        """Function with default param: Arguments.defaults has one entry."""
        ctx = self.parse_and_translate("""import std_pkg::*;
            component C {
                function void greet(string name = "World") {
                    print(name);
                }
            }
        """)
        func = self._get_func(ctx, "C", "greet")
        self.assertEqual(len(func.args.defaults), 1)
        self.assertIsInstance(func.args.defaults[0], ir.ExprConstant)
        self.assertEqual(func.args.defaults[0].value, "World")

    def test_function_default_int_param(self):
        """Function with int default: defaults list has ExprConstant(42)."""
        ctx = self.parse_and_translate("""import std_pkg::*;
            component C {
                function void compute(int n = 42) {
                    print("ok");
                }
            }
        """)
        func = self._get_func(ctx, "C", "compute")
        self.assertEqual(len(func.args.defaults), 1)
        self.assertIsInstance(func.args.defaults[0], ir.ExprConstant)
        self.assertEqual(func.args.defaults[0].value, 42)

    def test_function_no_defaults_empty_list(self):
        """Function without defaults has empty defaults list."""
        ctx = self.parse_and_translate("""
            component C {
                function int add(int x, int y) {
                    return x;
                }
            }
        """)
        func = self._get_func(ctx, "C", "add")
        self.assertEqual(func.args.defaults, [])

    def test_function_return_type_translated(self):
        """Function return type int → DataTypeInt."""
        ctx = self.parse_and_translate("""
            component C {
                function int get_value() {
                    return 42;
                }
            }
        """)
        func = self._get_func(ctx, "C", "get_value")
        self.assertIsInstance(func.returns, ir.DataTypeInt)

    def test_void_function_no_return_type(self):
        """void function has returns=None."""
        ctx = self.parse_and_translate("""import std_pkg::*;
            component C {
                function void do_nothing() {
                    print("ok");
                }
            }
        """)
        func = self._get_func(ctx, "C", "do_nothing")
        self.assertIsNone(func.returns)


class TestSuperCallFeature(unittest.TestCase):
    """Super call behavior tests.
    
    NOTE: The PSS frontend produces identical AST nodes for 'super.x' and 'x',
    so we cannot distinguish them at the IR level. Both translate to self.x.
    """

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def _get_body_stmts(self, ctx, action_key):
        action = ctx.type_map.get(action_key)
        self.assertIsNotNone(action, f"Action not found: {action_key}")
        body_fn = next((f for f in action.functions if f.name == "body"), None)
        self.assertIsNotNone(body_fn, "No body function")
        return body_fn.body

    def test_super_field_access_translates_to_self_attr(self):
        """super.x translates to self.x (PSS frontend doesn't distinguish super vs self)."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    rand int x;
                }
                action b : a {
                    exec body {
                        int y = super.x;
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx, "C::b")
        assign = stmts[0]
        self.assertIsInstance(assign, ir.StmtAnnAssign)
        attr = assign.value
        # PSS frontend gives identical AST for super.x and plain x
        self.assertIsInstance(attr, ir.ExprAttribute)
        self.assertEqual(attr.attr, "x")

    def test_plain_field_access_same_as_super(self):
        """Plain x access and super.x produce same IR (frontend limitation)."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    rand int x;
                    exec body { int y = x; }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx, "C::a")
        assign = stmts[0]
        attr = assign.value
        self.assertIsInstance(attr, ir.ExprAttribute)
        self.assertEqual(attr.attr, "x")


class TestFunctionVarargs(unittest.TestCase):
    """Tests for varargs function parameter translation."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def _get_func(self, ctx, comp_key, func_name):
        comp = ctx.type_map.get(comp_key)
        self.assertIsNotNone(comp, f"Component not found: {comp_key}")
        func = next((f for f in comp.functions if f.name == func_name), None)
        self.assertIsNotNone(func, f"Function not found: {func_name}")
        return func

    def test_varargs_goes_to_args_vararg(self):
        """Varargs param goes into args.vararg, not args.args."""
        ctx = self.parse_and_translate("""
            component C {
                function void f(int x, string... rest) {}
            }
        """)
        func = self._get_func(ctx, "C", "f")
        self.assertIsNotNone(func.args.vararg)
        self.assertEqual(func.args.vararg.arg, "rest")

    def test_varargs_type_annotation(self):
        """Varargs param has correct type annotation."""
        ctx = self.parse_and_translate("""
            component C {
                function void f(string... args) {}
            }
        """)
        func = self._get_func(ctx, "C", "f")
        self.assertIsInstance(func.args.vararg.annotation, ir.DataTypeString)

    def test_regular_params_before_varargs_still_in_args(self):
        """Regular params before varargs remain in args.args."""
        ctx = self.parse_and_translate("""
            component C {
                function void f(int a, int b, string... rest) {}
            }
        """)
        func = self._get_func(ctx, "C", "f")
        self.assertEqual(len(func.args.args), 2)
        self.assertEqual(func.args.args[0].arg, "a")
        self.assertEqual(func.args.args[1].arg, "b")
        self.assertIsNotNone(func.args.vararg)
        self.assertEqual(func.args.vararg.arg, "rest")

    def test_only_varargs_param(self):
        """Function with only a varargs param has empty args.args and set vararg."""
        ctx = self.parse_and_translate("""
            component C {
                function void log(string... msgs) {}
            }
        """)
        func = self._get_func(ctx, "C", "log")
        self.assertEqual(len(func.args.args), 0)
        self.assertIsNotNone(func.args.vararg)


class TestExtendDeclaration(unittest.TestCase):
    """Tests for extend action/struct/component declarations."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def test_extend_action_adds_field(self):
        """extend action adds new fields to the target action IR type."""
        ctx = self.parse_and_translate("""
            component C {
                action a { rand int x; }
            }
            extend action C::a { rand int y; }
        """)
        a = ctx.type_map.get("C::a")
        self.assertIsNotNone(a)
        field_names = [f.name for f in a.fields]
        self.assertIn("x", field_names)
        self.assertIn("y", field_names)

    def test_extend_action_adds_exec_body(self):
        """extend action with exec body adds body function to the action."""
        ctx = self.parse_and_translate("""import std_pkg::*;
            component C {
                action a { rand int x; }
            }
            extend action C::a {
                exec body { print("extended"); }
            }
        """)
        a = ctx.type_map.get("C::a")
        self.assertIsNotNone(a)
        body_func = next((f for f in a.functions if f.name == "body"), None)
        self.assertIsNotNone(body_func)
        self.assertGreater(len(body_func.body), 0)

    def test_extend_struct_adds_field(self):
        """extend struct adds new fields to the target struct IR type."""
        ctx = self.parse_and_translate("""
            struct S { int a; }
            extend struct S { int b; }
        """)
        s = ctx.type_map.get("S")
        self.assertIsNotNone(s)
        field_names = [f.name for f in s.fields]
        self.assertIn("a", field_names)
        self.assertIn("b", field_names)

    def test_extend_action_adds_constraint(self):
        """extend action with constraint block adds a _constraint function."""
        ctx = self.parse_and_translate("""
            component C {
                action a { rand int x; }
            }
            extend action C::a {
                constraint { x > 0; }
            }
        """)
        a = ctx.type_map.get("C::a")
        self.assertIsNotNone(a)
        constraint_fn = next((f for f in a.functions if f.metadata.get('_is_constraint')), None)
        self.assertIsNotNone(constraint_fn)

    def test_extend_action_both_field_and_exec(self):
        """extend action can add both a field and an exec block together."""
        ctx = self.parse_and_translate("""import std_pkg::*;
            component C {
                action a { rand int x; }
            }
            extend action C::a {
                rand int z;
                exec post_solve { print("post"); }
            }
        """)
        a = ctx.type_map.get("C::a")
        self.assertIsNotNone(a)
        field_names = [f.name for f in a.fields]
        self.assertIn("z", field_names)
        func_names = [f.name for f in a.functions]
        self.assertIn("post_solve", func_names)

if __name__ == '__main__':
    unittest.main()


class TestExtendEnum(unittest.TestCase):
    """Tests for extend enum declarations."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def test_extend_enum_adds_item(self):
        """extend enum appends new item to existing DataTypeEnum."""
        ctx = self.parse_and_translate("""
            enum color_e { RED=0, GREEN=1, BLUE=2 }
            extend enum color_e { YELLOW }
        """)
        e = ctx.type_map.get("color_e")
        self.assertIsNotNone(e)
        self.assertIn("YELLOW", e.items)

    def test_extend_enum_auto_increments_value(self):
        """extend enum item auto-increments from the last value."""
        ctx = self.parse_and_translate("""
            enum color_e { RED=0, GREEN=1, BLUE=2 }
            extend enum color_e { YELLOW }
        """)
        e = ctx.type_map.get("color_e")
        self.assertEqual(e.items["YELLOW"], 3)

    def test_extend_enum_preserves_original_items(self):
        """extend enum does not remove original items."""
        ctx = self.parse_and_translate("""
            enum color_e { RED=0, GREEN=1, BLUE=2 }
            extend enum color_e { YELLOW }
        """)
        e = ctx.type_map.get("color_e")
        for name, val in [("RED", 0), ("GREEN", 1), ("BLUE", 2)]:
            self.assertEqual(e.items[name], val)

    def test_extend_enum_multiple_items(self):
        """extend enum can add multiple new items at once."""
        ctx = self.parse_and_translate("""
            enum state_e { IDLE=0 }
            extend enum state_e { RUNNING, PAUSED, DONE }
        """)
        e = ctx.type_map.get("state_e")
        self.assertIn("RUNNING", e.items)
        self.assertIn("PAUSED", e.items)
        self.assertIn("DONE", e.items)


if __name__ == "__main__":
    unittest.main()
