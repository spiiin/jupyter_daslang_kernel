"""Inspect real compiler ASTs without executing or committing notebook code."""
import os
from pathlib import Path
import tempfile
import unittest
from editor_services import EditorServices
from inspection_magic import parse_inspection
from session import Runtime, NotebookSession, CompileError
from worker_client import WorkerClient


class InspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = Runtime(os.environ["DAS_SDK"])

    @classmethod
    def tearDownClass(cls):
        cls.runtime.close()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.folder.name)
        self.session = NotebookSession(self.runtime)
        self.macro = Path("notebook_macros.das")
        self.original = (Path(__file__).parent / "examples/notebook_macros.das").read_text()
        self.macro.write_text(self.original, encoding="utf-8")

    def tearDown(self):
        self.session.close()
        os.chdir(self.cwd)
        self.folder.cleanup()

    def test_expansion_ast_and_compilation_do_not_execute_or_commit(self):
        self.session.execute("require ./notebook_macros.das\nvar value=40\nvar calls=0\ndef bump():int {calls+=1;return calls}")
        before = self.session.snapshot()
        expanded = self.session.inspect_compilation("expression", "plus_one(value)")
        self.assertIn("value + 1", expanded)
        self.assertNotIn("plus_one(", expanded)
        self.session.inspect_compilation("expression", "bump()")
        tree = self.session.inspect_compilation("ast", 'value=999\nvar local=plus_one(value)\nprint("RUNTIME_MARKER")')
        self.assertIn("ExprOp2", tree)
        self.assertIn("ExprVar", tree)
        self.assertIn("int", tree)
        self.assertEqual(tree.count("ExprConstInt : int const  1"), 1)
        self.assertIn("ExprOp2", self.session.inspect_compilation("ast", "plus_one(value)"))
        self.assertIn("ExprOp2", self.session.inspect_compilation("ast", "plus_one(value);"))
        self.assertEqual(self.session.snapshot(), before)
        self.assertEqual(self.session.execute("value"), "40")
        self.assertEqual(self.session.execute("calls"), "0")
        self.assertNotIn("local", self.session.variables)

    def test_new_require_and_definition_are_temporary_and_edits_refresh(self):
        before = self.session.snapshot()
        code = "require ./notebook_macros.das\ndef inspected(x:int):int{return plus_one(x)}"
        output = self.session.inspect_compilation("code", code)
        self.assertIn("x + 1", output)
        self.assertEqual(self.session.snapshot(), before)
        self.macro.write_text(self.original.replace(" + 1)", " + 2)"), encoding="utf-8")
        self.assertIn("x + 2", self.session.inspect_compilation("code", code))
        self.assertEqual(self.session.snapshot(), before)
        self.assertNotIn("inspected", self.session.definitions)

    def test_existing_overloads_and_structure_macro_generated_members(self):
        self.session.execute("require ./notebook_macros.das\ndef sample(x:int):int{return plus_one(x)}\ndef sample(x:float):float{return x+2.0}")
        before = self.session.snapshot()
        output = self.session.inspect_compilation("expression", "sample")
        self.assertIn("x + 1", output)
        self.assertIn("x + 2f", output)
        self.assertEqual(self.session.snapshot(), before)
        source = Path(os.environ["DAS_SDK"]) / "tutorials/macros/structure_macro_mod.das"
        Path("structure_macro_mod.das").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        code = "require ./structure_macro_mod.das\n[serializable(version=2)]\nstruct Color {r:float;g:float;b:float}"
        tree = self.session.inspect_compilation("ast", code)
        self.assertIn("Structure Color", tree)
        self.assertIn("Field _version", tree)
        self.assertIn("describe_Color", tree)
        self.assertEqual(self.session.snapshot(), before)

    def test_errors_map_to_cell_and_leave_session_usable(self):
        self.session.execute("var saved=42")
        before = self.session.snapshot()
        with self.assertRaises(CompileError) as error:
            self.session.inspect_compilation("ast", "missing_function()", cell_label="In[99]")
        self.assertIn("In[99], line 2", str(error.exception))
        self.assertEqual(self.session.snapshot(), before)
        self.assertEqual(self.session.execute("saved"), "42")

    def test_worker_magics_and_readonly_editor_services(self):
        editor = EditorServices(lambda: {})
        self.assertEqual(editor.complete("%exp", 4)["matches"], ["%expand"])
        self.assertEqual(editor.complete("%%a", 3)["matches"], ["%%ast"])
        self.assertTrue(editor.inspect("%expand bump()", 4)["found"])
        self.assertEqual(editor.is_complete("%%ast\nvar value=1"), {"status": "complete"})
        self.assertEqual(editor.is_complete("%expand foo(")["status"], "incomplete")
        self.assertEqual(editor.is_complete("%%ast")["status"], "incomplete")
        with self.assertRaises(ValueError):
            parse_inspection("%%ast --unknown\nvar value=1")
        worker = WorkerClient()
        try:
            self.assertEqual(worker.execute("var value=40")["status"], "ok")
            before = worker.state.copy()
            reply = worker.execute("%%ast\nvalue=999\nprint(\"RUNTIME_MARKER\")")
            self.assertEqual(reply["status"], "ok", reply)
            self.assertIn("ExprCopy", reply["result"])
            self.assertNotIn("RUNTIME_MARKER", reply["output"].get("stdout", ""))
            self.assertEqual(worker.state, before)
            self.assertEqual(worker.execute("value")["result"], "40")
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
