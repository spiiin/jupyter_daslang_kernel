import unittest
from editor_services import EditorServices


class EditorTests(unittest.TestCase):
    def test_editor_requests_do_not_start_runtime(self):
        from kernel import DaslangKernel
        kernel = DaslangKernel()
        try:
            self.assertIsNone(kernel.worker_client.worker)
            self.assertIn("print", kernel.do_complete("pri", 3)["matches"])
            self.assertTrue(kernel.do_inspect("print(", 6)["found"])
            self.assertEqual(kernel.do_is_complete("def f() {")["status"], "incomplete")
            self.assertIsNone(kernel.worker_client.worker)
            self.assertEqual(kernel.worker_client.state["variables"], {})
        finally:
            kernel.do_shutdown(False)
    def setUp(self):
        self.state = {"variables": {"stats": "Stats", "state": "State", "items": "array<Stats>", "counter": "int"},
                      "schemas": {"Stats": [["count", "int"], ["values", "array<int>"]], "State": [["stats", "Stats"]]},
                      "definitions": {"calc": "def calc(n : int; var a : array<int>) : int { return n }"},
                      "definition_origins": {"calc": {"label": "In[1]", "line": 2,
                                              "code": "//! Calculate a value.\ndef calc(n : int; var a : array<int>) : int { return n }"}}}
        self.editor = EditorServices(lambda: self.state)

    def complete(self, code):
        return self.editor.complete(code, len(code))

    def test_global_names_fields_and_nested_paths(self):
        self.assertIn("counter", self.complete("cou")["matches"])
        self.assertIn("calc", self.complete("cal")["matches"])
        self.assertEqual(self.complete("stats.co")["matches"], ["count"])
        self.assertEqual(self.complete("state.stats.v")["matches"], ["values"])
        self.assertEqual(self.complete("items[0].co")["matches"], ["count"])
        self.assertEqual(self.complete("unknown.co")["matches"], [])

    def test_cursor_positions_unicode_and_suppressed_contexts(self):
        code = 'print("Привет")\nstats.co'
        reply = self.complete(code)
        self.assertEqual(code[reply["cursor_start"]:reply["cursor_end"]], "co")
        self.assertEqual(self.complete('print("stats.co')["matches"], [])
        self.assertEqual(self.complete('// stats.co')["matches"], [])
        self.assertEqual(self.complete('/* stats.co')["matches"], [])
        self.assertEqual(self.complete('print("value={stats.co')["matches"], ["count"])
        self.assertEqual(self.complete('%re')["matches"], ["%reset"])

    def test_current_cell_typed_variables_and_scopes(self):
        reply = self.complete('var temporary : Stats\ntemporary.co')
        self.assertEqual(reply["matches"], ["count"])
        self.assertNotIn("hidden", self.complete('if (true) {var hidden : int = 1}\nhi')["matches"])
        self.assertIn("visible", self.complete('if (true) {var visible : int = 1\nvi')["matches"])

    def test_inspection_source_signature_docs_and_fields(self):
        reply = self.editor.inspect("calc(", 5)
        self.assertTrue(reply["found"])
        self.assertIn("n : int; var a : array<int>", reply["data"]["text/plain"])
        self.assertIn("Calculate a value", reply["data"]["text/plain"])
        self.assertIn("In[1]", reply["data"]["text/plain"])
        self.assertIn("Stats", self.editor.inspect("stats", 5)["data"]["text/plain"])
        self.assertIn("int", self.editor.inspect("stats.count", 11)["data"]["text/plain"])
        self.assertIn("int", self.editor.inspect("items[0].count", 14)["data"]["text/plain"])
        self.assertFalse(self.editor.inspect("missing", 7)["found"])
        self.assertTrue(self.editor.inspect("print(", 6)["found"])

    def test_complete_code(self):
        for code in ('', '%reset', '%eval counter', 'var x = 1', 'print("\\}")', 'print("{counter}")',
                     'counter // comment +', 'def f() {return 1}',
                     'struct P {x : int}\nvar p : P\np.x = 2', 'var x = 1\nx * 2'):
            with self.subTest(code=code):
                self.assertEqual(self.editor.is_complete(code)["status"], "complete")

    def test_incomplete_code_and_indentation(self):
        for code in ('def f() {', 'print(', 'var x =', 'var x :', 'counter + // pending',
                     'if (true)', 'struct P', 'print("open', '/* open', 'stats.', 'var x : table<string;'):
            with self.subTest(code=code):
                reply = self.editor.is_complete(code)
                self.assertEqual(reply["status"], "incomplete", reply)
                self.assertIn("indent", reply)
        self.assertEqual(self.editor.is_complete('def f() {\n    var x = 1')["indent"], "    ")

    def test_invalid_syntax(self):
        for code in ('}', 'var x = 1)', 'if true {pass}', 'var x = ;'):
            with self.subTest(code=code):
                self.assertEqual(self.editor.is_complete(code)["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
