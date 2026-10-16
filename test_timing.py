"""Real runtime timing, profiling and checkpoint isolation checks."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from editor_services import EditorServices
from session import Runtime, NotebookSession, ExecutionError, CompileError
from timing_magic import parse_timing, execute_timing
from worker_client import WorkerClient, WorkerInterrupted


class TimingTests(unittest.TestCase):
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

    def run_magic(self, code):
        return execute_timing(self.session, parse_timing(code), "In[77]")

    def test_time_runs_once_commits_and_separates_phases(self):
        self.session.execute("var count=0\ndef bump():int {count+=1; return count}")
        output, data = self.run_magic("%time bump()")
        self.assertTrue(output.startswith("1\n"))
        phases = data["timings"]
        for key in ("compile_ns", "simulate_ns", "restore_ns", "initialize_ns", "execute_ns", "total_ns"):
            self.assertGreater(phases[key], 0)
        self.assertGreaterEqual(phases["total_ns"], sum(value for key, value in phases.items() if key != "total_ns"))
        self.assertEqual(self.session.execute("count"), "1")
        self.assertTrue(self.run_magic("%time -1")[0].startswith("-1\n"))

    def test_timeit_resets_each_iteration_without_committing_definitions_or_imports(self):
        self.session.execute("var count=10\nvar values:array<int>\ndef bump():int {count+=1;return count}")
        original_context = self.session.current
        before = self.session.snapshot()
        # Every execution must start with 10, even across repeats and loops.
        code = "%%timeit -r 2 -n 3\nrequire daslib/random\nif (count!=10) {panic(\"state leaked\")}\ncount+=1\nvalues |> push(1)\nvar temporary=99\ndef ephemeral():int {return 99}"
        output, data = self.run_magic(code)
        self.assertEqual(len(data["samples"]), 6)
        self.assertIn("2 repeats x 3", output)
        self.assertIs(self.session.current, original_context)
        self.assertEqual(self.session.snapshot(), before)

    def test_timeit_errors_preserve_original_session_and_body_lines(self):
        self.session.execute("var count=10")
        before = self.session.snapshot()
        with self.assertRaises(ExecutionError):
            self.run_magic('%%timeit -r 1\ncount=999\npanic("trial error")')
        self.assertEqual(self.session.snapshot(), before)
        with self.assertRaises(CompileError) as error:
            self.run_magic("%%time\nmissing_function()")
        self.assertIn("In[77], line 2", str(error.exception))

    def test_profile_call_counts_recursion_allocations_trace_and_cleanup(self):
        self.session.execute('''var count=0
def leaf():int { var a:array<int>; a |> resize(1024); count+=1; return length(a) }
def recur(n:int):int { if (n==0) {return leaf()}; return recur(n-1) }
def outer():int {return recur(3)+leaf()}''')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "function trace.json"
            output, data = self.run_magic(f'%%profile --top 10 --trace "{path}"\nouter()')
            profile = data["profile"]
            leaf = next(row for row in profile["functions"] if row["name"] == "leaf")
            outer = next(row for row in profile["functions"] if row["name"] == "outer")
            recur = next(row for row in profile["functions"] if row["name"].split()[0] == "recur")
            self.assertEqual(leaf["calls"], 2)
            self.assertEqual(recur["calls"], 4)
            self.assertGreaterEqual(leaf["allocated_bytes"], 8192)
            self.assertGreater(leaf["allocations"], 0)
            self.assertGreater(outer["inclusive_ns"], outer["self_ns"])
            self.assertEqual(profile["incomplete_frames"], 0)
            trace = json.loads(path.read_text())
            self.assertTrue(trace["traceEvents"])
            self.assertTrue(all(event["ph"] == "X" and event["dur"] >= 0 for event in trace["traceEvents"]))
            self.assertIn("Instrumentation adds overhead", output)
        self.assertEqual(self.session.execute("count"), "2")
        # No hooks or collection remain active on ordinary execution.
        self.session.execute("outer()")
        self.assertIsNone(self.session.last_profile)

    def test_profile_runtime_error_detaches_agent_and_retains_normal_error_semantics(self):
        self.session.execute('var count=0\ndef bad() {count+=1;panic("expected")}')
        with self.assertRaises(ExecutionError):
            self.run_magic("%%profile\nbad()")
        self.assertFalse(self.session.profile_requested)
        self.assertEqual(self.session.execute("count"), "1")
        output, data = self.run_magic("%%profile\ncount+=1")
        self.assertTrue(data["profile"]["functions"])
        self.assertEqual(self.session.execute("count"), "2")

    def test_parser_editor_and_worker_protocol(self):
        for code in ("%timeit -r 0 f()", "%timeit -r x f()", "%%profile --bad x\nf()", "%%profile --top 2", "%time f()\nf()"):
            with self.assertRaises(ValueError):
                parse_timing(code)
        self.assertEqual(parse_timing('%timeit -r 2 -n 3 f()')[1]["number"], 3)
        editor = EditorServices(lambda: {})
        self.assertEqual(editor.complete("%%prof", 6)["matches"], ["%%profile"])
        self.assertTrue(editor.inspect("%time f()", 4)["found"])
        self.assertEqual(editor.is_complete("%%timeit\nvar x=1")["status"], "complete")
        self.assertEqual(editor.is_complete("%time f(")["status"], "incomplete")
        worker = WorkerClient()
        try:
            worker.execute("var count=1")
            before = worker.state.copy()
            reply = worker.execute("%timeit -r 2 count+=1")
            self.assertEqual(reply["status"], "ok", reply)
            self.assertIn("measurement", reply)
            self.assertEqual(worker.state, before)
            reply = worker.execute("%%profile\ncount+=1")
            self.assertEqual(reply["status"], "ok", reply)
            self.assertTrue(reply["measurement"]["profile"]["functions"])
            self.assertEqual(worker.execute("count")["result"], "2")
        finally:
            worker.close()

    def test_interrupt_benchmark_recovers_original_checkpoint(self):
        worker = WorkerClient()
        started = threading.Event()
        errors, replies, chunks = [], [], []
        def output(name, text):
            chunks.append(text)
            if "BENCHMARK_STARTED" in "".join(chunks):
                started.set()
        def execute():
            try:
                replies.append(worker.execute('%%timeit -r 2\ncount=999\nprint("BENCHMARK_STARTED\\n")\nwhile (true) {pass}',
                               on_output=output))
            except BaseException as error:
                errors.append(error)
        try:
            worker.execute("var count=42")
            thread = threading.Thread(target=execute, daemon=True)
            thread.start()
            self.assertTrue(started.wait(15), f"benchmark did not start: {chunks} {errors} {replies}")
            worker.interrupt()
            thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], WorkerInterrupted)
            self.assertEqual(worker.execute("count")["result"], "42")
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
