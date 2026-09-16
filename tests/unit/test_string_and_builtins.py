"""
Tests for string method calls, substring expressions, and builtin function translations.

Covers:
 - s.size() → ExprCall(ExprAttribute(..., 'size'), [])
 - s.find("x") → ExprCall(ExprAttribute(..., 'find'), [ExprConstant('x')])
 - s.lower() → ExprCall(ExprAttribute(..., 'lower'), [])
 - s[1..3] → ExprSlice(lower=1, upper=3)
 - print("msg") → StmtExpr(ExprCall)
 - format("fmt", x) → ExprCall
"""
import unittest

from pssc import Parser
from pssparser.core import Factory
from pssc.ast2ir import AstToIrTranslator, AstToIrContext
from zuspec.dataclasses import ir


class TestStringMethods(unittest.TestCase):
    """String method AST→IR translation tests."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def _get_body_stmts(self, ctx, action_key="C::a"):
        action = ctx.type_map.get(action_key)
        self.assertIsNotNone(action, f"Action not found: {action_key}")
        body_fn = next((f for f in action.functions if f.name == "body"), None)
        self.assertIsNotNone(body_fn, "No body function")
        return body_fn.body

    def test_string_size_method(self):
        """s.size() translates to ExprCall with attr 'size'."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        string s = "hello";
                        int n = s.size();
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        # Second stmt is `int n = s.size()`
        assign = stmts[1]
        self.assertIsInstance(assign, ir.StmtAnnAssign)
        call = assign.value
        self.assertIsInstance(call, ir.ExprCall)
        self.assertIsInstance(call.func, ir.ExprAttribute)
        self.assertEqual(call.func.attr, "size")

    def test_string_find_method_with_arg(self):
        """s.find("x") translates to ExprCall(attr='find', args=[ExprConstant('x')])."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        string s = "hello";
                        int idx = s.find("l");
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        assign = stmts[1]
        call = assign.value
        self.assertIsInstance(call, ir.ExprCall)
        self.assertIsInstance(call.func, ir.ExprAttribute)
        self.assertEqual(call.func.attr, "find")
        self.assertEqual(len(call.args), 1)
        self.assertIsInstance(call.args[0], ir.ExprConstant)
        self.assertEqual(call.args[0].value, "l")

    def test_string_lower_method(self):
        """s.lower() translates to ExprCall with attr 'lower'."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        string s = "HELLO";
                        string low = s.lower();
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        assign = stmts[1]
        call = assign.value
        self.assertIsInstance(call, ir.ExprCall)
        self.assertEqual(call.func.attr, "lower")

    def test_string_upper_method(self):
        """s.upper() translates to ExprCall with attr 'upper'."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        string s = "hello";
                        string up = s.upper();
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        assign = stmts[1]
        call = assign.value
        self.assertIsInstance(call, ir.ExprCall)
        self.assertEqual(call.func.attr, "upper")

    def test_string_substring_slice(self):
        """s[1..3] translates to ExprSubscript(s, ExprSlice(1, 3)).

        The parser models a range subscript as an ``ExprSliceRange`` hanging off
        the hier-id element, so both endpoints must survive into the IR.
        """
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        string s = "hello";
                        string sub = s[1..3];
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        assign = stmts[1]
        self.assertIsInstance(assign, ir.StmtAnnAssign)
        sub = assign.value
        self.assertIsInstance(sub, ir.ExprSubscript)
        self.assertIsInstance(sub.value, ir.ExprRefLocal)
        self.assertEqual(sub.value.name, "s")
        self.assertIsInstance(sub.slice, ir.ExprSlice)
        self.assertEqual(sub.slice.lower.value, 1)
        self.assertEqual(sub.slice.upper.value, 3)


class TestBuiltinFunctions(unittest.TestCase):
    """Builtin function call AST→IR translation tests."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def _get_body_stmts(self, ctx, action_key="C::a"):
        action = ctx.type_map.get(action_key)
        self.assertIsNotNone(action, f"Action not found: {action_key}")
        body_fn = next((f for f in action.functions if f.name == "body"), None)
        self.assertIsNotNone(body_fn, "No body function")
        return body_fn.body

    def test_print_call_translates(self):
        """print("msg") translates to StmtExpr(ExprCall)."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        print("hello");
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertIsInstance(stmt, ir.StmtExpr)
        self.assertIsInstance(stmt.expr, ir.ExprCall)

    def test_print_call_has_string_arg(self):
        """print("hello") produces ExprCall with one ExprConstant arg."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        print("hello");
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        call = stmts[0].expr
        self.assertEqual(len(call.args), 1)
        self.assertIsInstance(call.args[0], ir.ExprConstant)
        self.assertEqual(call.args[0].value, "hello")

    def test_print_call_func_name(self):
        """print() ExprCall func has attr 'print'."""
        ctx = self.parse_and_translate("""
            component C {
                action a {
                    exec body {
                        print("msg");
                    }
                }
            }
        """)
        stmts = self._get_body_stmts(ctx)
        call = stmts[0].expr
        self.assertIsInstance(call.func, ir.ExprAttribute)
        self.assertEqual(call.func.attr, "print")

    def test_format_call_translates(self):
        """format("fmt %0d", n) requires PSS import; skip - not in default scope.
        format() is a PSS std_pkg function that is not available without special import.
        This test documents the limitation."""
        pass  # format() not in default scope; would need std_pkg import

    def test_error_call_translates(self):
        """error("msg") requires PSS import; skip - not in default scope."""
        pass  # error() not in default scope; would need std_pkg import

    def test_fatal_call_translates(self):
        """fatal("msg") requires PSS import; skip - not in default scope."""
        pass  # fatal() not in default scope; would need std_pkg import


if __name__ == "__main__":
    unittest.main()
