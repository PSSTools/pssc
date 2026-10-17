"""
Tests for scalar data type AST→IR translation and IR→Runtime building.

Covers: enum (plain / explicit values), typedef, and their use in fields.
"""
import unittest
from enum import IntEnum

from pssc import Parser
from pssparser.core import Factory
from pssc.ast2ir import AstToIrTranslator, AstToIrContext
from pssc.runtime import IrToRuntimeBuilder
from zuspec.dataclasses import ir


class TestEnumAstToIr(unittest.TestCase):
    """Enum declaration → DataTypeEnum IR."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    # --- enum registration ---------------------------------------------------

    def test_enum_registered_in_type_map(self):
        ctx = self.parse_and_translate("enum status_e { IDLE, BUSY }")
        self.assertIn("status_e", ctx.type_map)
        self.assertIsInstance(ctx.type_map["status_e"], ir.DataTypeEnum)

    def test_enum_name(self):
        ctx = self.parse_and_translate("enum color_e { RED, GREEN, BLUE }")
        dt = ctx.type_map["color_e"]
        self.assertEqual(dt.name, "color_e")

    # --- auto-assigned values ------------------------------------------------

    def test_enum_auto_values_start_at_zero(self):
        ctx = self.parse_and_translate("enum color_e { RED, GREEN, BLUE }")
        items = ctx.type_map["color_e"].items
        self.assertEqual(items["RED"], 0)
        self.assertEqual(items["GREEN"], 1)
        self.assertEqual(items["BLUE"], 2)

    def test_enum_single_item_auto_value(self):
        ctx = self.parse_and_translate("enum single_e { ONLY }")
        items = ctx.type_map["single_e"].items
        self.assertEqual(items["ONLY"], 0)

    # --- explicit values -----------------------------------------------------

    def test_enum_explicit_values(self):
        ctx = self.parse_and_translate("enum e { A=1, B=5 }")
        items = ctx.type_map["e"].items
        self.assertEqual(items["A"], 1)
        self.assertEqual(items["B"], 5)

    def test_enum_mixed_explicit_auto_values(self):
        """After an explicit value, the next auto value increments from it."""
        ctx = self.parse_and_translate("enum e { RED, GREEN=5, BLUE }")
        items = ctx.type_map["e"].items
        self.assertEqual(items["RED"], 0)
        self.assertEqual(items["GREEN"], 5)
        self.assertEqual(items["BLUE"], 6)

    def test_enum_values_are_constant_expressions(self):
        """An item's value is a constant expression (LRM 7.5.1): a negative
        literal is `-` applied to one, and was read as no value at all."""
        ctx = self.parse_and_translate("""
            static const int K = 10;
            enum e { A, NEG = -3, B, C = K + 2, D, E = ~0, F = (1 << 4) }
        """)
        self.assertEqual(ctx.type_map["e"].items, {
            "A": 0, "NEG": -3, "B": -2, "C": 12, "D": 13, "E": -1, "F": 16})

    def test_extend_enum_values_are_constant_expressions(self):
        ctx = self.parse_and_translate("""
            enum e { A }
            extend enum e { NEG = -3, B }
        """)
        self.assertEqual(ctx.type_map["e"].items, {"A": 0, "NEG": -3, "B": -2})

    def test_enum_item_reference_folds_a_negative_value(self):
        """A reference folds to the item's value, not its neighbour's."""
        ctx = self.parse_and_translate("""
            enum e { NEG = -3, FAST = 6 }
            component pss_top {
                function int f() { return e::NEG; }
            }
        """)
        f = next(fn for fn in ctx.type_map["pss_top"].functions
                 if fn.name == "f")
        ret = f.body[-1].value
        self.assertEqual(ret.value, -3)

    def test_enum_item_added_by_an_extension_folds(self):
        """An added item is in no EnumDecl; it was `self.B`, a field that
        does not exist."""
        ctx = self.parse_and_translate("""
            component pss_top {
                function int f() { return e::B; }
            }
            enum e { A }
            extend enum e { NEG = -3, B }
        """)
        f = next(fn for fn in ctx.type_map["pss_top"].functions
                 if fn.name == "f")
        ret = f.body[-1].value
        self.assertIsInstance(ret, ir.ExprConstant)
        self.assertEqual((ret.value, ret.ref), (-2, "e::B"))

    def test_enum_value_that_does_not_fold_is_an_error(self):
        ctx = self.parse_and_translate("enum e { A = B, B }")
        self.assertIn(
            "enum 'e': the value of item 'A' is not a constant expression",
            ctx.errors)

    # --- item ordering -------------------------------------------------------

    def test_enum_item_order_preserved(self):
        ctx = self.parse_and_translate("enum e { C, A, B }")
        keys = list(ctx.type_map["e"].items.keys())
        self.assertEqual(keys, ["C", "A", "B"])

    # --- multiple enums in same file -----------------------------------------

    def test_multiple_enums(self):
        ctx = self.parse_and_translate("""
            enum a_e { X, Y }
            enum b_e { P=10, Q=20 }
        """)
        self.assertIn("a_e", ctx.type_map)
        self.assertIn("b_e", ctx.type_map)
        self.assertEqual(ctx.type_map["b_e"].items["Q"], 20)

    # --- enum field in struct -------------------------------------------------

    def test_enum_field_in_struct(self):
        ctx = self.parse_and_translate("""
            enum state_e { OFF, ON }
            struct dev_s { state_e mode; }
        """)
        self.assertIn("dev_s", ctx.type_map)
        dev_s = ctx.type_map["dev_s"]
        self.assertEqual(len(dev_s.fields), 1)
        field = dev_s.fields[0]
        self.assertEqual(field.name, "mode")
        self.assertIsInstance(field.datatype, ir.DataTypeEnum)
        self.assertEqual(field.datatype.name, "state_e")

    # --- no errors -----------------------------------------------------------

    def test_enum_no_errors(self):
        ctx = self.parse_and_translate("enum flags_e { F0, F1, F2 }")
        self.assertEqual(ctx.errors, [])


class TestTypedefAstToIr(unittest.TestCase):
    """typedef declaration → type alias in IR context."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def parse_and_translate(self, pss_code: str) -> AstToIrContext:
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        return self.translator.translate(ast_root)

    def test_typedef_bit_registered(self):
        ctx = self.parse_and_translate("typedef bit my_bit_t;")
        self.assertIn("my_bit_t", ctx.type_map)

    def test_typedef_int_is_DataTypeInt(self):
        ctx = self.parse_and_translate("typedef int my_int_t;")
        dt = ctx.type_map["my_int_t"]
        self.assertIsInstance(dt, ir.DataTypeInt)

    def test_typedef_int_signed(self):
        ctx = self.parse_and_translate("typedef int my_signed_t;")
        dt = ctx.type_map["my_signed_t"]
        self.assertTrue(dt.signed)

    def test_typedef_bit_unsigned(self):
        ctx = self.parse_and_translate("typedef bit my_unsigned_t;")
        dt = ctx.type_map["my_unsigned_t"]
        self.assertIsInstance(dt, ir.DataTypeInt)
        self.assertFalse(dt.signed)

    def test_typedef_no_errors(self):
        ctx = self.parse_and_translate("typedef int alias_t;")
        self.assertEqual(ctx.errors, [])

    def test_typedef_used_as_field_type(self):
        """A typedef alias can be used as a field type in a struct."""
        ctx = self.parse_and_translate("""
            typedef int my_int_t;
            struct s { my_int_t count; }
        """)
        self.assertIn("s", ctx.type_map)
        s = ctx.type_map["s"]
        self.assertEqual(len(s.fields), 1)
        field = s.fields[0]
        self.assertEqual(field.name, "count")
        self.assertIsInstance(field.datatype, ir.DataTypeInt)


class TestEnumIrToRuntime(unittest.TestCase):
    """Enum IR → Python IntEnum classes via IrToRuntimeBuilder."""

    def setUp(self):
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)

    def build_registry(self, pss_code: str):
        self.parser.parses([("test.pss", pss_code)])
        ast_root = self.parser.link()
        ctx = self.translator.translate(ast_root)
        return IrToRuntimeBuilder(ctx).build(), ctx

    def test_enum_class_in_registry(self):
        registry, _ = self.build_registry("enum color_e { RED, GREEN, BLUE }")
        self.assertIn("color_e", registry)

    def test_enum_class_is_intenum(self):
        registry, _ = self.build_registry("enum color_e { RED, GREEN, BLUE }")
        color_e = registry["color_e"]
        self.assertTrue(issubclass(color_e, IntEnum))

    def test_enum_member_values(self):
        registry, _ = self.build_registry("enum color_e { RED, GREEN=5, BLUE }")
        color_e = registry["color_e"]
        self.assertEqual(color_e.RED, 0)
        self.assertEqual(color_e.GREEN, 5)
        self.assertEqual(color_e.BLUE, 6)

    def test_enum_py_type_set_on_ir(self):
        """DataTypeEnum.py_type should be populated after build()."""
        registry, ctx = self.build_registry("enum state_e { OFF, ON }")
        dt = ctx.type_map["state_e"]
        self.assertIsNotNone(dt.py_type)
        self.assertTrue(issubclass(dt.py_type, IntEnum))

    def test_enum_field_in_component_uses_intenum(self):
        registry, ctx = self.build_registry("""
            enum mode_e { SLOW, FAST }
            component C { mode_e speed; }
        """)
        self.assertIn("C", registry)
        C = registry["C"]
        import inspect
        hints = {}
        for cls in reversed(C.__mro__):
            if hasattr(cls, '__annotations__'):
                hints.update(cls.__annotations__)
        self.assertIn("speed", hints)
        self.assertTrue(issubclass(hints["speed"], IntEnum))


if __name__ == "__main__":
    unittest.main()
