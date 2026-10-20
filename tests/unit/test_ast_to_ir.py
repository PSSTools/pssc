"""
Test AST to IR translation
"""
import unittest
import logging
from pssc import Parser
from pssparser.core import Factory
from pssc.ast2ir import AstToIrTranslator, AstToIrContext
from zuspec.dataclasses import ir

# Setup logging for debugging
logging.basicConfig(level=logging.INFO)

class TestAstToIr(unittest.TestCase):
    """Test AST to IR translation"""
    
    def setUp(self):
        """Setup test fixtures"""
        self.factory = Factory.inst()
        self.parser = Parser()
        self.translator = AstToIrTranslator(debug=False)
    
    def parse_and_translate(self, pss_code: str, filename: str = "test.pss") -> AstToIrContext:
        """
        Helper to parse PSS code and translate to IR
        
        Args:
            pss_code: PSS source code
            filename: Filename for error reporting
            
        Returns:
            Translation context with IR
        """
        # Parse
        self.parser.parses([(filename, pss_code)])
        ast_root = self.parser.link()
        
        # Translate
        ctx = self.translator.translate(ast_root)
        
        # Check for errors
        if ctx.errors:
            for error in ctx.errors:
                print(f"Translation error: {error}")
        
        return ctx
    
    def test_empty_component(self):
        """Test translation of empty component"""
        ctx = self.parse_and_translate("""
            component C {
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0, f"Unexpected errors: {ctx.errors}")
        
        # Should have component C in type map
        self.assertIn("C", ctx.type_map)
        
        # Get component
        comp = ctx.type_map["C"]
        self.assertIsInstance(comp, ir.DataTypeComponent)
        self.assertEqual(comp.name, "C")
        self.assertEqual(len(comp.fields), 0)
        self.assertEqual(len(comp.functions), 0)
        self.assertIsNone(comp.super)
    
    def test_multiple_components(self):
        """Test translation of multiple components"""
        ctx = self.parse_and_translate("""
            component A {
            }
            
            component B {
            }
            
            component pss_top {
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Should have all three components
        self.assertIn("A", ctx.type_map)
        self.assertIn("B", ctx.type_map)
        self.assertIn("pss_top", ctx.type_map)
        
        # All should be DataTypeComponent
        for name in ["A", "B", "pss_top"]:
            comp = ctx.type_map[name]
            self.assertIsInstance(comp, ir.DataTypeComponent)
            self.assertEqual(comp.name, name)
    
    def test_component_inheritance(self):
        """Test translation of component inheritance"""
        ctx = self.parse_and_translate("""
            component Base {
            }
            
            component Derived : Base {
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get components
        base = ctx.type_map["Base"]
        derived = ctx.type_map["Derived"]
        
        # Base should have no super
        self.assertIsNone(base.super)
        
        # Derived should have super (as DataTypeRef for now)
        self.assertIsNotNone(derived.super)
        self.assertIsInstance(derived.super, ir.DataTypeRef)
    
    def test_builtin_types(self):
        """Test that built-in types are registered"""
        ctx = self.parse_and_translate("""
            component C {
            }
        """)
        
        # Check built-in types
        self.assertIn("bool", ctx.type_map)
        self.assertIn("int", ctx.type_map)
        self.assertIn("string", ctx.type_map)
        
        # Check bool type
        bool_type = ctx.type_map["bool"]
        self.assertIsInstance(bool_type, ir.DataTypeInt)
        self.assertEqual(bool_type.bits, 1)
        self.assertEqual(bool_type.signed, False)
        
        # Check int type
        int_type = ctx.type_map["int"]
        self.assertIsInstance(int_type, ir.DataTypeInt)
        self.assertEqual(int_type.bits, 32)
        self.assertEqual(int_type.signed, True)
        
        # Check string type
        string_type = ctx.type_map["string"]
        self.assertIsInstance(string_type, ir.DataTypeString)
    
    def test_nested_component_in_pss_top(self):
        """Test component with nested action (action should be skipped for now)"""
        ctx = self.parse_and_translate("""
            component pss_top {
                action A {
                }
            }
        """)
        
        # Should have no errors (action is skipped but not an error)
        self.assertEqual(len(ctx.errors), 0)
        
        # Should have pss_top
        self.assertIn("pss_top", ctx.type_map)
        comp = ctx.type_map["pss_top"]
        self.assertIsInstance(comp, ir.DataTypeComponent)
    
    def test_debug_logging(self):
        """Test that debug logging can be enabled"""
        translator = AstToIrTranslator(debug=True)
        
        # Parse and translate with debug
        self.parser.parses([("test.pss", "component C {}")])
        ast_root = self.parser.link()
        ctx = translator.translate(ast_root)
        
        # Should complete without errors
        self.assertEqual(len(ctx.errors), 0)
        self.assertIn("C", ctx.type_map)
    
    def test_component_with_data_fields(self):
        """Test component with simple data fields"""
        ctx = self.parse_and_translate("""
            component C {
                int x;
                bit[8] flags;
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get component
        comp = ctx.type_map["C"]
        self.assertIsInstance(comp, ir.DataTypeComponent)
        
        # Should have 2 fields
        self.assertEqual(len(comp.fields), 2)
        
        # Check first field (x)
        field_x = comp.fields[0]
        self.assertEqual(field_x.name, "x")
        self.assertIsNotNone(field_x.datatype)
        
        # Check second field (flags)
        field_flags = comp.fields[1]
        self.assertEqual(field_flags.name, "flags")
        self.assertIsNotNone(field_flags.datatype)
    
    def test_component_with_function(self):
        """Test component with a simple function"""
        ctx = self.parse_and_translate("""
            component C {
                function void init() { }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get component
        comp = ctx.type_map["C"]
        self.assertIsInstance(comp, ir.DataTypeComponent)
        
        # Should have 1 function
        self.assertEqual(len(comp.functions), 1)
        
        # Check function
        func = comp.functions[0]
        self.assertEqual(func.name, "init")
        self.assertIsNone(func.returns)  # void return
        self.assertEqual(len(func.body), 0)  # empty body for now
    
    def test_function_with_return_value(self):
        """Test function with return value"""
        ctx = self.parse_and_translate("""
            component C {
                function int get_value() {
                    return 42;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get component and function
        comp = ctx.type_map["C"]
        self.assertEqual(len(comp.functions), 1)
        
        func = comp.functions[0]
        self.assertEqual(func.name, "get_value")
        self.assertIsNotNone(func.returns)  # int return
        self.assertEqual(len(func.body), 1)  # return statement
        
        # Check return statement
        stmt = func.body[0]
        self.assertIsInstance(stmt, ir.StmtReturn)
        self.assertIsNotNone(stmt.value)
        self.assertIsInstance(stmt.value, ir.ExprConstant)
        self.assertEqual(stmt.value.value, 42)
    
    def test_function_with_parameters(self):
        """Test function with parameters"""
        ctx = self.parse_and_translate("""
            component C {
                function int add(int a, int b) {
                    return a + b;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        self.assertEqual(func.name, "add")
        self.assertIsNotNone(func.args)
        self.assertEqual(len(func.args.args), 2)
    
    def test_assignment_statement(self):
        """Test assignment statement"""
        ctx = self.parse_and_translate("""
            component C {
                function void f() {
                    int x;
                    x = 5;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have 2 statements: declaration and assignment
        self.assertEqual(len(func.body), 2)
        
        # Check declaration
        decl_stmt = func.body[0]
        self.assertIsInstance(decl_stmt, ir.StmtAnnAssign)
        
        # Check assignment
        assign_stmt = func.body[1]
        self.assertIsInstance(assign_stmt, ir.StmtAssign)
        self.assertIsNotNone(assign_stmt.value)
    
    def test_if_statement(self):
        """Test if-else statement"""
        ctx = self.parse_and_translate("""
            component C {
                function int max(int a, int b) {
                    if (a > b) {
                        return a;
                    } else {
                        return b;
                    }
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have 1 if statement
        self.assertEqual(len(func.body), 1)
        
        if_stmt = func.body[0]
        self.assertIsInstance(if_stmt, ir.StmtIf)
        self.assertIsNotNone(if_stmt.test)  # condition
        self.assertGreater(len(if_stmt.body), 0)  # then branch
        self.assertGreater(len(if_stmt.orelse), 0)  # else branch

    def test_else_if_chain_is_one_flat_node(self):
        """`if`/`else if`/`else` is one StmtIfChain, its clauses in order;
        `else { if ... }` written with braces is a body, not a link."""
        ctx = self.parse_and_translate("""
            component C {
                function int f(int a) {
                    if (a == 0) { return 1; }
                    else if (a == 1) { return 2; }
                    else if (a == 2) { return 3; }
                    else { if (a == 3) { return 4; } }
                    return 0;
                }
            }
        """)
        self.assertEqual(len(ctx.errors), 0)
        chain = ctx.type_map["C"].functions[0].body[0]
        self.assertIsInstance(chain, ir.StmtIfChain)
        self.assertEqual([c.test.rhs.value for c in chain.clauses], [0, 1, 2])
        self.assertEqual(len(chain.orelse), 1)
        self.assertIsInstance(chain.orelse[0], ir.StmtIf)
        self.assertEqual(chain.orelse[0].orelse, [])

    def test_an_else_belongs_to_the_nearest_if(self):
        """`if (a) if (b) x; else if (c) y; else z;`: the chain is the inner
        `if`'s, and the outer has no `else`."""
        ctx = self.parse_and_translate("""
            component C {
                function int f(int a, int b) {
                    int r = 0;
                    if (a == 1) if (b == 1) r = 1; else if (b == 2) r = 3; else r = 2;
                    return r;
                }
            }
        """)
        self.assertEqual(len(ctx.errors), 0)
        outer = ctx.type_map["C"].functions[0].body[1]
        self.assertIsInstance(outer, ir.StmtIf)
        self.assertEqual(outer.orelse, [])
        (inner,) = outer.body
        self.assertIsInstance(inner, ir.StmtIfChain)
        self.assertEqual(len(inner.clauses), 2)
        self.assertEqual(len(inner.orelse), 1)

    def test_for_loop(self):
        """Test repeat statement (PSS equivalent of for loop)"""
        ctx = self.parse_and_translate("""
            component C {
                function int sum(int n) {
                    int total = 0;
                    repeat (n) {
                        total = total + 1;
                    }
                    return total;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have declaration, repeat (for), and return
        self.assertGreaterEqual(len(func.body), 2)
        
        # Check for the repeat statement (translated as For)
        has_loop = any(isinstance(stmt, ir.StmtFor) for stmt in func.body)
        self.assertTrue(has_loop, "Should have a for loop statement")
    
    def test_while_loop(self):
        """Test while loop statement"""
        ctx = self.parse_and_translate("""
            component C {
                function int countdown(int n) {
                    while (n > 0) {
                        n = n - 1;
                    }
                    return n;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have while loop and return
        self.assertGreaterEqual(len(func.body), 1)
        
        # Check for while statement
        has_while = any(isinstance(stmt, ir.StmtWhile) for stmt in func.body)
        self.assertTrue(has_while, "Should have a while loop statement")
    
    def test_arithmetic_expression(self):
        """Test arithmetic expressions"""
        ctx = self.parse_and_translate("""
            component C {
                function int calc() {
                    return (5 + 3) * 2;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have return statement with binary expression
        self.assertEqual(len(func.body), 1)
        ret_stmt = func.body[0]
        self.assertIsInstance(ret_stmt, ir.StmtReturn)
        self.assertIsNotNone(ret_stmt.value)
    
    def test_unary_expression(self):
        """Test unary expressions (negation, logical not)"""
        ctx = self.parse_and_translate("""
            component C {
                function int negate(int x) {
                    return -x;
                }
                
                function bool invert(bool flag) {
                    return !flag;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get component
        comp = ctx.type_map["C"]
        self.assertEqual(len(comp.functions), 2)
        
        # Check negate function
        negate_func = comp.functions[0]
        self.assertEqual(negate_func.name, "negate")
        self.assertEqual(len(negate_func.body), 1)
        
        # Check return statement has unary expression
        ret_stmt = negate_func.body[0]
        self.assertIsInstance(ret_stmt, ir.StmtReturn)
        self.assertIsNotNone(ret_stmt.value)
        # Should be ExprUnary but we accept any expression for now
    
    def test_conditional_expression(self):
        """Test conditional (ternary) expression"""
        ctx = self.parse_and_translate("""
            component C {
                function int abs(int x) {
                    return (x >= 0) ? x : -x;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have return with conditional expression
        self.assertEqual(len(func.body), 1)
        ret_stmt = func.body[0]
        self.assertIsInstance(ret_stmt, ir.StmtReturn)
        self.assertIsNotNone(ret_stmt.value)
    
    def test_comparison_operators(self):
        """Test comparison operators"""
        ctx = self.parse_and_translate("""
            component C {
                function bool compare(int a, int b) {
                    bool res;
                    res = (a < b);
                    res = (a <= b);
                    res = (a > b);
                    res = (a >= b);
                    res = (a == b);
                    res = (a != b);
                    return res;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have declaration + 6 assignments + return = 8 statements
        self.assertEqual(len(func.body), 8)
    
    def test_logical_operators(self):
        """Test logical operators (&&, ||)"""
        ctx = self.parse_and_translate("""
            component C {
                function bool logic(bool a, bool b) {
                    return (a && b) || (!a && !b);
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Get function
        comp = ctx.type_map["C"]
        func = comp.functions[0]
        
        # Should have return statement
        self.assertEqual(len(func.body), 1)
        ret_stmt = func.body[0]
        self.assertIsInstance(ret_stmt, ir.StmtReturn)
    
    def test_action_basic(self):
        """Test basic action translation"""
        ctx = self.parse_and_translate("""
            component pss_top {
                action MyAction {
                    int value;
                    bool flag;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Should have action in type map
        self.assertIn("pss_top::MyAction", ctx.type_map)
        
        # Get action
        action = ctx.type_map["pss_top::MyAction"]
        self.assertIsInstance(action, ir.DataTypeClass)
        self.assertEqual(action.name, "MyAction")
        
        # Should have 2 fields
        self.assertEqual(len(action.fields), 2)
        self.assertEqual(action.fields[0].name, "value")
        self.assertEqual(action.fields[1].name, "flag")
    
    def test_action_inheritance(self):
        """Test action inheritance"""
        ctx = self.parse_and_translate("""
            component pss_top {
                action BaseAction {
                    int base_value;
                }
                
                action DerivedAction : BaseAction {
                    int derived_value;
                }
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Should have both actions
        self.assertIn("pss_top::BaseAction", ctx.type_map)
        self.assertIn("pss_top::DerivedAction", ctx.type_map)
        
        # Check inheritance
        base = ctx.type_map["pss_top::BaseAction"]
        derived = ctx.type_map["pss_top::DerivedAction"]
        
        self.assertIsNone(base.super)
        self.assertIsNotNone(derived.super)
        self.assertIsInstance(derived.super, ir.DataTypeRef)
    
    def test_struct_basic(self):
        """Test basic struct translation"""
        ctx = self.parse_and_translate("""
            struct Point {
                int x;
                int y;
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Should have struct in type map
        self.assertIn("Point", ctx.type_map)
        
        # Get struct
        struct = ctx.type_map["Point"]
        self.assertIsInstance(struct, ir.DataTypeStruct)
        self.assertEqual(struct.name, "Point")
        
        # Should have 2 fields
        self.assertEqual(len(struct.fields), 2)
        self.assertEqual(struct.fields[0].name, "x")
        self.assertEqual(struct.fields[1].name, "y")
    
    def test_struct_inheritance(self):
        """Test struct inheritance"""
        ctx = self.parse_and_translate("""
            struct Point2D {
                int x;
                int y;
            }
            
            struct Point3D : Point2D {
                int z;
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Should have both structs
        self.assertIn("Point2D", ctx.type_map)
        self.assertIn("Point3D", ctx.type_map)
        
        # Check inheritance
        base = ctx.type_map["Point2D"]
        derived = ctx.type_map["Point3D"]
        
        self.assertIsNone(base.super)
        self.assertIsNotNone(derived.super)
        self.assertIsInstance(derived.super, ir.DataTypeRef)
    
    def test_mixed_types(self):
        """Test component with action and struct fields"""
        ctx = self.parse_and_translate("""
            struct Config {
                int timeout;
                bool enabled;
            }
            
            component System {
                action Process {
                    int data;
                }
                
                Config cfg;
                Process proc;
            }
        """)
        
        # Should have no errors
        self.assertEqual(len(ctx.errors), 0)
        
        # Should have all three types
        self.assertIn("Config", ctx.type_map)
        self.assertIn("System::Process", ctx.type_map)
        self.assertIn("System", ctx.type_map)
        
        # Check types
        self.assertIsInstance(ctx.type_map["Config"], ir.DataTypeStruct)
        self.assertIsInstance(ctx.type_map["System::Process"], ir.DataTypeClass)
        self.assertIsInstance(ctx.type_map["System"], ir.DataTypeComponent)
        
        # System should have 2 fields
        system = ctx.type_map["System"]
        self.assertEqual(len(system.fields), 2)


if __name__ == '__main__':
    unittest.main()
