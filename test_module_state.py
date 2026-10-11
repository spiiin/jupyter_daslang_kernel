"""Module state/lifecycle tests use actual .das modules and isolated workers."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from session import Runtime, NotebookSession, CompileError, ExecutionError
from worker_client import WorkerClient, WorkerInterrupted, WorkerCrashed


class ModuleStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = Runtime(os.environ["DAS_SDK"])

    @classmethod
    def tearDownClass(cls):
        cls.runtime.close()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original = os.getcwd()
        os.chdir(self.folder.name)
        self.session = NotebookSession(self.runtime)

    def tearDown(self):
        try:
            self.session.close()
        finally:
            os.chdir(self.original)
            self.folder.cleanup()

    def module(self, text, name="state"):
        Path(name + ".das").write_text("options gen2\nmodule " + name + "\n" + text, encoding="utf-8")

    def test_private_state_initializers_once_and_root_name_collision(self):
        self.module('''var private count=5
var private starts=0
[init]
def startup(){count+=10;starts+=1}
def public next():int {count+=1;return count}
def public init_count():int {return starts}''')
        self.session.execute("require ./state.das as m\nvar count=100")
        self.assertEqual(self.session.execute("m::next()"), "16")
        self.assertEqual(self.session.execute("m::next()"), "17")
        self.assertEqual(self.session.execute("count"), "100")
        self.assertEqual(self.session.execute("m::init_count()"), "1")
        self.session.execute("require ./state.das as m")
        self.assertEqual(self.session.execute("m::next()"), "18")

    def test_nested_struct_arrays_tables_strings_and_checkpoint(self):
        self.module('''struct Box {name:string; values:array<array<int>>; map:table<int;string>}
var private box:Box
var private labels:table<string;array<int>>
[init]
def startup(){box.name="Привет"; var row <- [3,4]; box.values |> push_clone(row); box.map[7]="seven"; labels["first"] := row}
def public value():string {return "{box.name}:{box.values[0][1]}:{box.map[7]}:{labels[\"first\"][0]}"}''')
        self.session.execute("require ./state.das as m")
        checkpoint = self.session.snapshot()
        self.session.close()
        self.session.restore(checkpoint)
        self.assertEqual(self.session.execute("m::value()"), "Привет:4:seven:3")

    def test_packed_and_large_tables_and_deleted_slots(self):
        self.module('''var private words:table<string;int>
var private numbers:table<int;array<int>>
[init]
def startup(){for(i in range(30)){words[string(i)]=i; var row <- [i,i+1]; numbers[i] := row}; words |> erase("4"); numbers |> erase(4)}
def public value():int {return words["29"] + numbers[29][1] + length(words) + length(numbers)}''')
        self.session.execute("require ./state.das as m")
        self.assertEqual(self.session.execute("m::value()"), "117")
        self.assertEqual(self.session.execute("m::value()"), "117")
        checkpoint = self.session.snapshot()
        self.session.close()
        self.session.restore(checkpoint)
        self.assertEqual(self.session.execute("m::value()"), "117")

    def test_shared_module_code_changes_keep_values_without_init_replay(self):
        text = '''options gen2
module state shared
var private count=5
var private starts=0
[init]
def startup(){starts+=1}
def public next():int {count+=1;return count}
def public init_count():int {return starts}'''
        Path("state.das").write_text(text)
        self.session.execute("require ./state.das as m")
        self.assertEqual(self.session.execute("m::next()"), "6")
        Path("state.das").write_text(text.replace("count+=1", "count+=10").replace("count=5", "count=999"))
        self.assertEqual(self.session.execute("m::next()"), "16")
        self.assertEqual(self.session.execute("m::init_count()"), "1")

    def test_new_module_initializer_reads_restored_dependency(self):
        self.module("var public count=5\ndef public bump(){count+=1}", "base")
        self.session.execute("require ./base.das as b\nb::bump()")
        self.module("require ./base.das\nvar private initial=base::count\ndef public value():int {return initial}", "dependent")
        self.session.execute("require ./dependent.das as d")
        self.assertEqual(self.session.execute("d::value()"), "6")
        self.assertEqual(self.session.execute("b::count"), "6")

    def test_fixed_arrays_and_formatter_source_library(self):
        self.module('''var private numbers:int[3]
var private names:string[2]
[init]
def startup(){numbers[0]=7;names[1]="saved"}
def public value():string {return "{numbers[0]}:{names[1]}"}''')
        self.session.execute("require ./state.das as m")
        self.assertEqual(self.session.execute("m::value()"), "7:saved")
        self.assertEqual(self.session.execute("m::value()"), "7:saved")
        self.session.execute('require daslib/das_source_formatter as fmt\nlet text="var x=1"')
        self.assertIn("var x = 1", self.session.execute("fmt::format_source_string(text)"))

    def test_new_global_initializes_once_without_rerunning_module_hook(self):
        original = "var private count=5\n[init]\ndef startup(){count+=10}\ndef public value():int {return count}"
        self.module(original)
        self.session.execute("require ./state.das as m")
        self.assertEqual(self.session.execute("m::value()"), "15")
        self.module(original + "\nvar private added=count+2\ndef public added_value():int {return added}")
        self.assertEqual(self.session.execute("m::added_value()"), "17")
        self.assertEqual(self.session.execute("m::value()"), "15")

    def test_struct_layout_change_detected_before_blob_load(self):
        original = "struct Box {value:int}\nvar private box=Box(value=5)\ndef public value():int {return box.value}"
        self.module(original)
        self.session.execute("require ./state.das as m")
        self.assertEqual(self.session.execute("m::value()"), "5")
        self.module(original.replace("struct Box {value:int}", "struct Box {other:int; value:int}"))
        with self.assertRaisesRegex(ValueError, "changed type layout"):
            self.session.execute("m::value()")
        self.module(original)
        self.assertEqual(self.session.execute("m::value()"), "5")

    def test_unsupported_pointer_rejected_before_init(self):
        self.module('''require daslib/fio
var private ptr:int?
[init]
def startup(){fwrite("should-not-exist.txt", "INIT")}
def public value():bool {return ptr==null}''')
        with self.assertRaisesRegex(CompileError, "unsupported module global type"):
            self.session.execute("require ./state.das as m\nm::value()")
        self.assertFalse(Path("should-not-exist.txt").exists())

    def test_shared_memory_global_rejected_before_init(self):
        self.module('''require daslib/fio
let shared count=value_init()
def private value_init():int {print("MUST NOT RUN"); return 0}
[init]
def startup(){fwrite("should-not-exist.txt", "INIT")}
def public value():int {return count}''')
        with self.assertRaisesRegex(CompileError, "shared-memory"):
            self.session.execute("require ./state.das as m\nm::value()")
        self.assertFalse(Path("should-not-exist.txt").exists())

    def test_stuck_finalizer_has_bounded_worker_shutdown(self):
        self.module('[finalize]\ndef cleanup(){while(true){pass}}')
        client = WorkerClient()
        try:
            self.assertEqual(client.execute("require ./state.das")["status"], "ok")
            process = client.worker.process
            start = time.monotonic()
            client.close()
            self.assertLess(time.monotonic() - start, 5)
            self.assertIsNotNone(process.poll())
        finally:
            client.close()

    def test_init_failure_preserves_previous_session_and_reports_module_line(self):
        self.session.execute("var x=7")
        self.module('[init]\ndef startup(){panic("bad init")}')
        with self.assertRaises(ExecutionError) as error:
            self.session.execute("require ./state.das")
        self.assertIn("bad init", str(error.exception))
        self.assertIn("state.das", str(error.exception))
        self.assertEqual(self.session.execute("x"), "7")
        self.assertEqual(self.session.requires, {})

    def test_corrupt_checkpoint_rejected_before_init(self):
        self.module("var private count=5\n[init]\ndef startup(){count+=1}\ndef public value():int {return count}")
        self.session.execute("require ./state.das as m\nm::value()")
        state = self.session.snapshot()
        record = next(value for value in state["module_values"].values() if value["name"] == "count")
        record["data"] = "AA=="
        other = NotebookSession(self.runtime)
        try:
            with self.assertRaisesRegex(ExecutionError, "truncated"):
                other.restore(state)
        finally:
            other.close()

    def test_reset_finalize_and_idle_close_finalize_once(self):
        self.module('''require daslib/fio
var private count=1
[init]
def startup(){count+=10; fwrite("init.txt", string(count))}
[finalize]
def cleanup(){fwrite("final.txt", string(count))}
def public bump(){count+=1}''')
        client = WorkerClient()
        try:
            self.assertEqual(client.execute("require ./state.das as m\nm::bump()")["status"], "ok")
            self.assertEqual(client.execute("m::bump()")["status"], "ok")
            self.assertFalse(Path("final.txt").exists())
            self.assertEqual(client.execute("%reset")["status"], "ok")
            self.assertEqual(Path("final.txt").read_text(), "13")
            self.assertEqual(client.state["module_values"], {})
            Path("final.txt").unlink()
            self.assertEqual(client.execute("require ./state.das as m\nm::bump()")["status"], "ok")
            client.close()
            self.assertEqual(Path("final.txt").read_text(), "12")
        finally:
            client.close()

    def test_interrupt_restore_skips_init_and_keeps_module_data(self):
        self.module('''require daslib/fio
var private count=5
[init]
def startup(){var previous=fread("starts.txt"); fwrite("starts.txt", previous+"I")}
def public next():int {count+=1;return count}''')
        Path("starts.txt").write_text("")
        client = WorkerClient()
        try:
            reply = client.execute("require ./state.das as m\nm::next()")
            self.assertEqual(reply["status"], "ok", reply)
            self.assertEqual(reply["result"], "6")
            ready = threading.Event()
            errors = []
            def run():
                try:
                    client.execute('m::next()\nprint("READY")\nwhile(true){pass}', on_output=lambda name, text: ready.set())
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(ready.wait(10))
            client.interrupt(); thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(errors[0], WorkerInterrupted)
            self.assertEqual(client.execute("m::next()")["result"], "7")
            self.assertEqual(Path("starts.txt").read_text(), "I")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
