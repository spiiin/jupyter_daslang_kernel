"""Language additions exercise actual compilation, archives and worker recovery."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from session import Runtime, NotebookSession, CompileError
from worker_client import WorkerClient, WorkerInterrupted
from editor_services import EditorServices


class LanguageFeaturesTests(unittest.TestCase):
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

    def test_let_inference_reexecution_and_initializer_once(self):
        self.session.execute("var calls=0\ndef next():int {calls+=1;return calls}\nlet x=next()")
        self.assertEqual(self.session.execute("x + calls"), "2")
        self.assertEqual(self.session.execute("calls"), "1")
        self.session.execute("let x = next()")
        self.assertEqual(self.session.execute("x + calls"), "4")
        with self.assertRaises(CompileError):
            self.session.execute("x = 99")
        self.assertEqual(self.session.execute("x"), "2")
        for code in ("var x = 3", "let calls=3"):
            with self.assertRaises(ValueError):
                self.session.execute(code)

    def test_let_is_const_in_its_declaration_cell_and_in_functions(self):
        for code in ("let x=3\nx=4", "let x:int=3\ndef mutate(){x=4}\nmutate()",
                     "let a <- [1,2]\na[0]=3"):
            with self.subTest(code=code), self.assertRaises(CompileError):
                self.session.execute(code)
            self.assertEqual(self.session.variables, {})

    def test_let_structs_containers_defaults_and_restore(self):
        self.session.execute('''struct P {x:int; values:array<int>}
let number:int
let p = P(x=3)
let values <- [4,5]
let table_value:table<string;int>
let title="saved"''')
        state = self.session.snapshot()
        self.session.close()
        self.session.restore(state)
        self.assertEqual(self.session.execute("number"), "0")
        self.assertEqual(self.session.execute("p.x+values[1]"), "8")
        self.assertEqual(self.session.execute("title"), "saved")
        self.session.execute("let values <- [7,8]")
        self.assertEqual(self.session.execute("values[0]"), "7")
        with self.assertRaises(CompileError):
            self.session.execute("p.x=99")

    def test_overloads_same_cell_and_individual_redefinition(self):
        self.session.execute('''def f(x:int):int {return x+1}
def f(x:string):string {return x+"!"}
def f(x:int;y:int):int {return x+y}''')
        self.assertEqual(self.session.execute('f("ok")'), "ok!")
        self.assertEqual(self.session.execute("f(3)"), "4")
        self.assertEqual(self.session.execute("f(3,4)"), "7")
        self.session.execute("def f(renamed : int const) : int {return renamed*2}")
        self.assertEqual(self.session.execute("f(3)"), "6")
        self.assertEqual(self.session.execute('f("ok")'), "ok!")
        self.assertEqual(len(self.session.definitions), 3)

    def test_overload_defaults_grouped_parameters_and_generics(self):
        self.session.execute('''def add(a,b:int):int {return a+b}
def add(a:string;b:string="!"):string {return a+b}
def identity(x:auto(TT)):TT {return x}''')
        self.assertEqual(self.session.execute('add("ok")'), "ok!")
        self.assertEqual(self.session.execute("add(2,3)"), "5")
        self.session.execute("def add(first:int;second:int):int {return first*second}")
        self.session.execute("def identity(value:auto(UU)):UU {return value}")
        self.assertEqual(self.session.execute("add(2,3)"), "6")
        self.assertEqual(self.session.execute("identity(7)"), "7")
        self.assertEqual(len(self.session.definitions), 3)

    def test_overload_origins_errors_and_checkpoint(self):
        self.session.execute('def f(x:int):int {panic("int failure");return x}', cell_label="In[1]")
        self.session.execute('def f(x:string):string {return x}', cell_label="In[2]")
        state = self.session.snapshot()
        self.session.close()
        self.session.restore(state)
        self.assertEqual(self.session.execute('f("ok")'), "ok")
        from session import ExecutionError
        with self.assertRaises(ExecutionError) as caught:
            self.session.execute("f(2)", cell_label="In[3]")
        self.assertIn("In[1]", str(caught.exception))

    def test_editor_exposes_all_overloads_and_const_bindings_without_running(self):
        self.session.execute("require math as m\nlet value:int const=7\n//! Integer overload\ndef f(x:int):int {return x+1}", cell_label="In[1]")
        self.session.execute('//! String overload\ndef f(x:string):string {return x}', cell_label="In[2]")
        state = self.session.snapshot()
        editor = EditorServices(lambda: state)
        names = editor.complete("f", 1)["matches"]
        self.assertEqual(names.count("f"), 1)
        self.assertFalse(any(name.startswith("f(") for name in names))
        self.assertIn("m", editor.complete("m", 1)["matches"])
        help_text = editor.inspect("f(", 2)["data"]["text/plain"]
        for text in ("x:int", "x:string", "Integer overload", "String overload", "In[1]", "In[2]"):
            self.assertIn(text, help_text)
        source = "def f(renamed:int):int {return renamed*2}\nf("
        shadow = editor.inspect(source, len(source))["data"]["text/plain"]
        self.assertNotIn("Integer overload", shadow)
        self.assertIn("String overload", shadow)
        self.assertIn("let value", editor.inspect("value", 5)["data"]["text/plain"])
        self.assertEqual(editor.is_complete("require")["status"], "incomplete")
        self.assertEqual(editor.is_complete("require math as")["status"], "incomplete")
        self.assertEqual(editor.is_complete("require math\nlet x=1")["status"], "complete")
        self.assertEqual(self.session.execute("value"), "7")

    def test_legacy_definition_keys_are_normalized_on_restore(self):
        self.session.execute("def f(x:int):int {return x+1}")
        state = self.session.snapshot()
        state["definitions"]["f"] = state["definitions"].pop("f(int)")
        state["definition_origins"]["f"] = state["definition_origins"].pop("f(int)")
        self.session.close()
        self.session.restore(state)
        self.session.execute("def f(renamed:int):int {return renamed+2}")
        self.assertEqual(self.session.execute("f(2)"), "4")
        self.assertEqual(len(self.session.definitions), 1)

    def test_require_alias_dedup_error_mapping_and_only_import_checkpoint(self):
        self.session.execute("require math as m", cell_label="In[4]")
        state = self.session.snapshot()
        self.session.close()
        self.session.restore(state)
        self.assertEqual(self.session.execute("m::sqrt(9.0)"), "3")
        self.session.execute("require math as m\nlet root=m::sqrt(16.0)")
        self.assertEqual(len(self.session.requires), 1)
        self.assertEqual(self.session.execute("root"), "4")
        with self.assertRaises(CompileError) as caught:
            self.session.execute("// missing\nrequire ./missing_module.das", cell_label="In[9]")
        self.assertIn("In[9], line 2", str(caught.exception))
        self.assertEqual(len(self.session.requires), 1)
        self.assertEqual(self.session.execute("root"), "4")

    def test_require_inside_strings_and_nested_code_not_hoisted(self):
        self.session.execute('let text = "require ./missing.das"')
        self.assertEqual(self.session.requires, {})
        with self.assertRaises(ValueError):
            self.session.execute("if (true) {require math}")
        self.assertEqual(self.session.requires, {})

    def test_source_library_require_const_container_and_restore(self):
        self.session.execute('require daslib/strings_boost as sb\nlet words <- sb::split("a,b", ",")')
        state = self.session.snapshot()
        self.session.close()
        self.session.restore(state)
        self.assertEqual(self.session.execute("words[1]"), "b")
        self.assertEqual(self.session.execute('sb::join(words, "-")'), "a-b")

    def test_local_require_and_runtime_state_guard(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                Path("utilities.das").write_text('options gen2\nmodule utilities\nlet public answer_value=42\ndef public answer():int {return answer_value}')
                self.session.execute("require ./utilities.das as util\nlet x=util::answer()")
                self.assertEqual(self.session.execute("x+util::answer()"), "84")
                Path("stateful.das").write_text('options gen2\nmodule stateful\nvar counter=0\ndef public next():int {counter+=1;return counter}')
                self.assertEqual(self.session.execute("require ./stateful.das\nstateful::next()"), "1")
                self.assertEqual(self.session.execute("stateful::next()"), "2")
                Path("hooks.das").write_text('options gen2\nmodule hooks\n[init]\ndef startup(){pass}')
                self.session.execute("require ./hooks.das")
                self.assertEqual(self.session.execute("x"), "42")
            finally:
                os.chdir(original)

    def test_interrupt_preserves_let_overloads_and_requires(self):
        client = WorkerClient()
        try:
            setup = "require math as m\nlet x=7\ndef f(x:int):int {return x+1}\ndef f(x:string):string {return x}"
            self.assertEqual(client.execute(setup)["status"], "ok")
            errors = []
            ready = threading.Event()
            def run():
                try:
                    client.execute('let x=99\nprint("READY")\nwhile(true) {pass}',
                                   on_output=lambda name, text: ready.set())
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(ready.wait(10))
            client.interrupt()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(errors[0], WorkerInterrupted)
            self.assertEqual(client.execute("x")["result"], "7")
            self.assertEqual(client.execute("f(2)")["result"], "3")
            self.assertEqual(client.execute('f("ok")')["result"], "ok")
            self.assertEqual(client.execute("m::sqrt(9.0)")["result"], "3")
            self.assertEqual(client.execute("x=3")["status"], "error")
            self.assertEqual(client.execute("%reset")["status"], "ok")
            self.assertEqual(client.state["constants"], [])
            self.assertEqual(client.state["requires"], {})
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
