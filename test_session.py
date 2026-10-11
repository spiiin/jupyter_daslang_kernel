import os
import unittest
from pathlib import Path
from session import Runtime, NotebookSession, CompileError, ExecutionError


class SessionTests(unittest.TestCase):
    def test_struct_state_and_inferred_constructor(self):
        self.session.execute("struct Point {x : int; y : int}\nvar p = Point(x=3, y=4)")
        self.session.execute("p.x += 10")
        self.assertEqual(self.session.evaluate("p.x + p.y"), "17")
        self.assertEqual(self.session.variables["p"], "Point")

    def test_nested_containers_and_structs_survive_snapshot(self):
        self.session.execute('''struct Stats {count : int; values : array<int>}
struct State {stats : Stats; title : string}
var state : State
state.title = "saved"
state.stats.count = 5
state.stats.values |> push(8)
var rows : array<array<int>>
var row : array<int> <- [1,2]
rows |> push_clone(row)
var lookup : table<string;array<int>>
lookup["first"] := row
''')
        checkpoint = self.session.snapshot()
        self.session.close()
        other = NotebookSession(self.runtime)
        try:
            other.restore(checkpoint)
            self.assertEqual(other.evaluate("state.title"), "saved")
            self.assertEqual(other.evaluate("state.stats.values[0]"), "8")
            self.assertEqual(other.evaluate("rows[0][1]"), "2")
            self.assertEqual(other.evaluate('lookup["first"][0]'), "1")
        finally:
            other.close()

    def test_used_schema_changes_are_rejected_and_reset_allows_them(self):
        self.session.execute("struct Item {value : int}\nstruct Box {items : array<Item>}\nvar box : Box")
        original = self.session.snapshot()
        for code in ("struct Item {value : string}", "struct Box {extra : int; items : array<Item>}"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.session.execute(code)
        self.assertEqual(self.session.schemas, original["schemas"])
        self.session.close()
        self.session.execute("struct Item {value : string}\nvar item : Item\nitem.value=\"new\"")
        self.assertEqual(self.session.evaluate("item.value"), "new")

    def test_same_schema_reexecution_and_unused_schema_change(self):
        self.session.execute("struct P {x : int}\nvar p : P\np.x=7")
        self.session.execute("struct P { x:int }")
        self.assertEqual(self.session.evaluate("p.x"), "7")
        self.session.execute("struct Unused {x : int}")
        self.session.execute("struct Unused {name : string}")
        self.assertEqual(self.session.evaluate("p.x"), "7")

    def test_unsafe_schema_forms_and_defaults_are_rejected(self):
        for code in ("struct S {x : int = 2}", "struct S {p : int?}",
                     "struct S {items : array<S>}"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.session.execute(code)
        self.assertEqual(self.session.schemas, {})

    def test_checkpoint_type_hash_mismatch_is_rejected(self):
        self.session.execute("struct P {x : int}\nvar p : P\np.x=7")
        checkpoint = self.session.snapshot()
        checkpoint["type_hashes"]["p"] += 1
        other = NotebookSession(self.runtime)
        try:
            with self.assertRaises(ValueError):
                other.restore(checkpoint)
        finally:
            other.close()
    def test_last_expression_and_suppression(self):
        self.assertEqual(self.session.execute("var x = 10\nx * 2"), "20")
        self.assertEqual(self.session.execute("x // trailing comment"), "10")
        self.assertIsNone(self.session.execute("x; // suppress"))
        self.assertIsNone(self.session.execute("x += 1"))
        self.assertIsNone(self.session.execute("var y = 3"))
        self.assertIsNone(self.session.execute("x\ndef f() : int {return x}"))
        self.assertEqual(self.session.execute('""'), "")

    def test_last_expression_runs_once_and_renders_containers(self):
        self.session.execute("var count = 0\ndef next() : int {count += 1; return count}")
        self.assertEqual(self.session.execute("next()"), "1")
        self.assertEqual(self.session.evaluate("count"), "1")
        self.session.execute("var values : array<int> <- [2, 3]")
        self.assertEqual(self.session.execute("values"), "[ 2, 3]")
        self.assertEqual(self.session.execute("(\nnext()\n)"), "2")

    def test_runtime_location_and_stack_survive_restore(self):
        self.session.execute('def fail() {\npanic("boom")\n}\ndef outer() {fail()}', cell_label="In[1]")
        other = NotebookSession(self.runtime)
        try:
            other.restore(self.session.snapshot())
            with self.assertRaises(ExecutionError) as error:
                other.execute("outer()", cell_label="In[2]")
            message = str(error.exception)
            self.assertIn("In[1], line 2", message)
            self.assertIn("daslang stack:", message)
            self.assertIn("fail", message)
            self.assertNotIn("notebook.das:", message)
            self.assertNotIn("sptr=", message)
        finally:
            other.close()

    def test_multiline_result_error_maps_to_original_row(self):
        with self.assertRaises(CompileError) as error:
            self.session.execute("var x = 1\n(\n missing_function()\n)", cell_label="In[9]")
        self.assertIn("In[9], line 3", str(error.exception))
        self.assertNotIn("notebook.das:", str(error.exception))

    def test_primes_syntax_diagnostic_and_working_gen2_function(self):
        fixtures = Path(__file__).parent / "tests/fixtures"
        with self.assertRaises(CompileError) as error:
            self.session.execute((fixtures / "primes_original.txt").read_text(), cell_label="In[1]")
        self.assertIn("In[1], line 6", str(error.exception))
        self.assertIn("expecting '('", str(error.exception))
        self.session.execute((fixtures / "primes_gen2.txt").read_text())
        self.session.execute("var primes : array<int>")
        for n in (0, 1, 2, 10):
            self.session.execute(f"calc_primes({n}, primes)")
            self.assertEqual(self.session.evaluate("length(primes)"), str(n))
        self.assertEqual(self.session.execute("primes"), "[ 2, 3, 5, 7, 11, 13, 17, 19, 23, 29]")
    def test_compiler_errors_point_to_original_cell_lines(self):
        for code, row in [('var x = 1\nmissing_function()', 2),
                          ('var x = (\n missing_function()\n)', 2),
                          ('var x = 1\ndef bad() : int {\nreturn missing_function()\n}', 3)]:
            with self.subTest(code=code), self.assertRaises(CompileError) as error:
                self.session.execute(code, cell_label="In[42]")
            self.assertIn(f"In[42], line {row}", str(error.exception))
            self.assertNotIn("notebook.das:", str(error.exception))

    def test_definition_origin_survives_checkpoint_restore(self):
        self.session.execute("def provider() : int { return 1 }\ndef consumer() : int {\nreturn provider()\n}", cell_label="In[10]")
        other = NotebookSession(self.runtime)
        try:
            other.restore(self.session.snapshot())
            with self.assertRaises(CompileError) as error:
                other.execute('def provider() : string {return "changed"}', cell_label="In[20]")
            self.assertIn("In[10], line 3", str(error.exception))
        finally:
            other.close()

    @classmethod
    def setUpClass(cls):
        cls.runtime = Runtime(os.environ["DAS_SDK"])

    @classmethod
    def tearDownClass(cls):
        cls.runtime.close()

    def setUp(self):
        self.session = NotebookSession(self.runtime)

    def tearDown(self):
        self.session.close()

    def test_numbers_and_repeat_execution(self):
        self.session.execute("var x : int = 10")
        self.session.execute("x += 5")
        self.assertEqual(self.session.evaluate("x"), "15")
        self.session.execute("x += 5")
        self.assertEqual(self.session.evaluate("x"), "20")
        self.session.execute("var x : int = 10")
        self.assertEqual(self.session.evaluate("x"), "10")

    def test_strings_and_arrays(self):
        self.session.execute('var name : string = "hello"')
        self.session.execute("var xs : array<int>")
        self.session.execute('xs |> push(7)\nname = name + " world"')
        self.assertEqual(self.session.evaluate("name"), "hello world")
        self.assertEqual(self.session.evaluate("xs[0]"), "7")
        self.session.execute("xs |> push(9)")
        self.assertEqual(self.session.evaluate("length(xs)"), "2")

    def test_function_redefinition(self):
        self.session.execute("var x : int = 3")
        self.session.execute("def f() : int { return x * 2 }")
        self.assertEqual(self.session.evaluate("f()"), "6")
        self.session.execute("def f() : int { return x * 3 }")
        self.assertEqual(self.session.evaluate("f()"), "9")

    def test_compile_error_preserves_state(self):
        self.session.execute("var x : int = 42")
        with self.assertRaises(CompileError):
            self.session.execute("missing_function()")
        self.assertEqual(self.session.evaluate("x"), "42")
        with self.assertRaises(CompileError):
            self.session.execute("var bad : int = missing_function()")
        self.assertNotIn("bad", self.session.variables)

    def test_initializer_is_not_replayed(self):
        self.session.execute("var count : int = 0")
        self.session.execute("def next() : int { count += 1; return count }")
        self.session.execute("var x : int = next()")
        self.session.execute("x += 10")
        self.assertEqual(self.session.evaluate("count"), "1")
        self.assertEqual(self.session.evaluate("x"), "11")

    def test_runtime_exception_preserves_partial_changes(self):
        self.session.execute("var x : int = 1")
        with self.assertRaises(ExecutionError):
            self.session.execute('x = 9\npanic("test exception")')
        self.assertEqual(self.session.evaluate("x"), "9")

    def test_type_changes_are_rejected(self):
        self.session.execute("var x : int = 4")
        with self.assertRaises(ValueError):
            self.session.execute("var x : float = 4.0")
        self.assertEqual(self.session.evaluate("x"), "4")

    def test_move_initializer_and_table(self):
        self.session.execute("var xs : array<int> <- [1, 2, 3]")
        self.assertEqual(self.session.evaluate("xs[2]"), "3")
        self.session.execute("var lookup : table<string;int>")
        self.session.execute('lookup["a"] = 8')
        self.assertEqual(self.session.evaluate('lookup["a"]'), "8")

    def test_sessions_are_independent_and_reset(self):
        other = NotebookSession(self.runtime)
        try:
            self.session.execute("var x : int = 1")
            other.execute("var x : int = 2")
            self.assertEqual(self.session.evaluate("x"), "1")
            self.assertEqual(other.evaluate("x"), "2")
            self.session.close()
            with self.assertRaises(CompileError):
                self.session.evaluate("x")
        finally:
            other.close()

    def test_mixed_cells_infer_types_and_preserve_values(self):
        self.session.execute('var x = 10\nvar y = x * 2\nprint("{y}\\n")')
        self.assertEqual(self.session.variables, {"x": "int", "y": "int"})
        self.session.execute("x += 1\ny += x")
        self.assertEqual(self.session.evaluate("x"), "11")
        self.assertEqual(self.session.evaluate("y"), "31")

    def test_functions_and_statements_in_same_cell(self):
        self.session.execute("var x = 5\ndef f() : int { return x * 2 }\nvar y = f()\nx += 1")
        self.assertEqual(self.session.evaluate("y"), "10")
        self.assertEqual(self.session.evaluate("f()"), "12")
        self.session.execute("def f() : int { return x * 3 }\ny = f()")
        self.assertEqual(self.session.evaluate("y"), "18")

    def test_nested_variables_remain_local(self):
        self.session.execute("var total = 0\nfor (i in range(4)) {\nvar local = i * 2\ntotal += local\n}")
        self.assertEqual(self.session.evaluate("total"), "12")
        self.assertEqual(set(self.session.variables), {"total"})

    def test_inference_does_not_execute_initializers(self):
        self.session.execute("var count = 0\ndef next() : int { count += 1; return count }\nvar x = next()\nvar y = next()")
        self.assertEqual(self.session.evaluate("count"), "2")
        self.session.execute("x += 10\ny += 10")
        self.assertEqual(self.session.evaluate("count"), "2")

    def test_multiline_initializers_comments_and_semicolons(self):
        self.session.execute('''// var ignored = 99
var x = (
    2 + 3
)
var name = "Привет; // var fake = 1"; var y = x * 3
/* var ignored2 = 0; def fake() {} */
var xs : array<int> <- [x, y]
xs |> push(20)
''')
        self.assertEqual(set(self.session.variables), {"x", "name", "y", "xs"})
        self.assertEqual(self.session.evaluate("y"), "15")
        self.assertEqual(self.session.evaluate("name"), "Привет; // var fake = 1")
        self.assertEqual(self.session.evaluate("xs[2]"), "20")

    def test_late_error_does_not_commit_earlier_statements(self):
        self.session.execute("var x = 7")
        with self.assertRaises(CompileError):
            self.session.execute("x += 1\nvar new_value = 42\nmissing_function()")
        self.assertEqual(self.session.evaluate("x"), "7")
        self.assertNotIn("new_value", self.session.variables)
        with self.assertRaises(ValueError):
            self.session.execute("var new_value = 42\nif (")
        self.assertEqual(self.session.evaluate("x"), "7")

    def test_inferred_redeclaration_and_type_change(self):
        self.session.execute("var z = 1; var z : int = 2; var z = 3")
        self.assertEqual(self.session.evaluate("z"), "3")
        self.session.execute("var x = 10")
        self.session.execute("x += 5\nvar y = x")
        self.session.execute("var x = 20\ny += x")
        self.assertEqual(self.session.evaluate("x"), "20")
        self.assertEqual(self.session.evaluate("y"), "35")
        with self.assertRaises((CompileError, ValueError)):
            self.session.execute('var x = "different type"')
        self.assertEqual(self.session.evaluate("x"), "20")

    def test_definition_text_inside_strings_and_block_scope(self):
        self.session.execute('''if (false) { print("def fake()") }
def real() : int { return 8 }
var result = real()
''')
        self.assertEqual(set(self.session.definitions), {"real"})
        self.assertEqual(self.session.evaluate("result"), "8")
        with self.assertRaises(ValueError):
            self.session.execute("if (true) { def nested() : int { return 1 } }")
        self.assertEqual(set(self.session.definitions), {"real"})

    def test_unsupported_binding_forms_leave_state_intact(self):
        self.session.execute("var x = 10")
        for code in ("var y &= x", "let y &= x", "var a,b : int"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.session.execute(code)
        self.assertEqual(self.session.variables, {"x": "int"})


if __name__ == "__main__":
    unittest.main()
