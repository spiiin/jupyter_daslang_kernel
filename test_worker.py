import os
import threading
import time
import unittest
import ctypes as C
from pathlib import Path
import subprocess
import sys
from worker_client import WorkerClient, WorkerInterrupted, WorkerCrashed


class WorkerTests(unittest.TestCase):
    def test_structs_and_nested_arrays_survive_interrupt(self):
        self.execute("struct Stats {value : int; values : array<int>}\nvar stats : Stats\nstats.value=7\nstats.values |> push(8)\nvar x=7")
        thread, errors = self.run_forever()
        self.client.interrupt()
        thread.join(timeout=5)
        self.assertIsInstance(errors[0], WorkerInterrupted)
        self.assertEqual(self.execute("stats.value")["result"], "7")
        self.assertEqual(self.execute("stats.values[0]")["result"], "8")
        reply = self.client.execute("struct Stats {value : string; values : array<int>}")
        self.assertEqual(reply["status"], "error")
        self.assertEqual(self.execute("stats.value")["result"], "7")
    def test_automatic_result_and_void_call(self):
        self.execute("var count = 0\ndef next() : int {count += 1; return count}")
        self.assertEqual(self.execute("next()")["result"], "1")
        reply = self.execute('print("only once")')
        self.assertIsNone(reply["result"])
        self.assertEqual(reply["output"]["stdout"], "only once")
        self.assertEqual(self.execute("count")["result"], "1")

    def test_stream_output_arrives_before_interrupt(self):
        self.execute("var x = 7")
        received = []
        ready = threading.Event()
        errors = []
        def publish(name, text):
            received.append((name, text))
            if "Привет" in "".join(chunk for stream, chunk in received if stream == "stdout"):
                ready.set()
        def run():
            try:
                self.client.execute('print("Привет без перевода строки")\nx = 99\nwhile (true) {pass}', on_output=publish)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(timeout=10), "Output did not arrive while native code was running")
        self.assertTrue(thread.is_alive())
        self.client.interrupt()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], WorkerInterrupted)
        self.assertEqual("".join(chunk for stream, chunk in received if stream == "stdout"),
                         "Привет без перевода строки")
        self.assertEqual(self.execute("%eval x")["result"], "7")

    def test_output_is_not_duplicated_and_chunks_keep_utf8(self):
        reply = self.execute('for (i in range(4000)) {print("я")}')
        self.assertEqual(reply["output"]["stdout"], "я" * 4000)
        chunks = []
        reply = self.client.execute('print("one")\nprint("two")', on_output=lambda name,text: chunks.append(text))
        self.assertEqual("".join(chunks), "onetwo")
        self.assertEqual(reply["output"]["stdout"], "")

    @unittest.skipUnless(os.name == "nt", "Windows worker parent watchdog")
    def test_parent_exit_stops_the_runtime(self):
        script = 'from worker_client import WorkerClient; c=WorkerClient(); c.execute("var x = 1"); print(c.worker.process.pid, flush=True); c.execute("while (true) {pass}")'
        parent = subprocess.Popen([sys.executable, "-u", "-c", script],
            cwd=Path(__file__).parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        api = C.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [C.c_uint32, C.c_bool, C.c_uint32]
        api.OpenProcess.restype = C.c_void_p
        api.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
        api.WaitForSingleObject.restype = C.c_uint32
        api.CloseHandle.argtypes = [C.c_void_p]
        api.TerminateProcess.argtypes = [C.c_void_p, C.c_uint32]
        handle = None
        try:
            pid = int(parent.stdout.readline())
            handle = api.OpenProcess(0x00100001, False, pid)
            self.assertTrue(handle)
            parent.kill()
            parent.wait(timeout=5)
            self.assertEqual(api.WaitForSingleObject(handle, 5000), 0, "Orphaned runtime did not exit")
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            if handle:
                if api.WaitForSingleObject(handle, 0) != 0:
                    api.TerminateProcess(handle, 1)
                api.CloseHandle(handle)
            parent.stdout.close()
            parent.stderr.close()

    def setUp(self):
        self.client = WorkerClient()

    def tearDown(self):
        self.client.close()

    def execute(self, code):
        reply = self.client.execute(code)
        self.assertEqual(reply["status"], "ok", reply.get("error"))
        return reply

    def run_forever(self):
        errors = []
        def run():
            try:
                self.client.execute("x = 999\nwhile (true) { x += 1 }")
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(self.client.started.wait(timeout=10))
        time.sleep(0.3)
        self.assertTrue(thread.is_alive(), "Infinite cell unexpectedly completed")
        return thread, errors

    def test_interrupt_restores_values_arrays_and_functions(self):
        self.execute("var x = 7\nvar xs : array<int> <- [1, 2]\ndef f() : int { return x * 3 }")
        old_pid = self.client.worker.process.pid
        thread, errors = self.run_forever()
        start = time.monotonic()
        self.assertTrue(self.client.interrupt())
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 5)
        self.assertIsInstance(errors[0], WorkerInterrupted)
        self.assertEqual(self.execute("%eval x")["result"], "7")
        self.assertNotEqual(self.client.worker.process.pid, old_pid)
        self.assertEqual(self.execute("%eval xs[1]")["result"], "2")
        self.assertEqual(self.execute("%eval f()")["result"], "21")

    def test_crash_recovery_does_not_replay_initializers(self):
        self.execute("var count = 0\ndef next() : int {count += 1; return count}\nvar x = next()")
        thread, errors = self.run_forever()
        self.client.worker.process.kill()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], WorkerCrashed)
        self.assertEqual(self.execute("%eval x")["result"], "1")
        self.assertEqual(self.execute("%eval count")["result"], "1")

    def test_runtime_error_commits_partial_state_and_reset_clears_checkpoint(self):
        self.execute("var x = 1")
        reply = self.client.execute('x = 9\npanic("partial")')
        self.assertEqual(reply["status"], "error")
        self.client.worker.process.kill()
        self.client.worker.process.wait(timeout=5)
        self.assertEqual(self.execute("%eval x")["result"], "9")
        self.execute("%reset")
        self.client.worker.process.kill()
        self.client.worker.process.wait(timeout=5)
        reply = self.client.execute("%eval x")
        self.assertEqual(reply["status"], "error")
        self.assertEqual(self.client.state["variables"], {})

    def test_idle_interrupt_keeps_session(self):
        self.execute("var x = 4")
        pid = self.client.worker.process.pid
        self.assertFalse(self.client.interrupt())
        self.assertEqual(self.execute("%eval x")["result"], "4")
        self.assertEqual(self.client.worker.process.pid, pid)

    def test_lifecycle_hooks_are_rejected_before_state_changes(self):
        self.execute("var x = 4")
        reply = self.client.execute("[init]\ndef change_on_init() {x += 10}")
        self.assertEqual(reply["status"], "error")
        self.assertEqual(reply["error"]["ename"], "CellSyntaxError")
        self.assertEqual(self.execute("%eval x")["result"], "4")

    def test_reset_recovers_from_a_failed_checkpoint_restore(self):
        self.execute("var x = 4")
        self.client.worker.process.kill()
        self.client.worker.process.wait(timeout=5)
        self.client.state["values"]["x"] = "invalid archive"
        with self.assertRaises(WorkerCrashed):
            self.client.execute("%eval x")
        self.execute("%reset")
        self.assertEqual(self.client.state["variables"], {})


if __name__ == "__main__":
    unittest.main()
