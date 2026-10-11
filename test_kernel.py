"""End-to-end protocol smoke test; no kernelspec installation required."""
import os
from pathlib import Path
import sys
import unittest
import time
from jupyter_client import KernelManager


class KernelTests(unittest.TestCase):
    def test_editor_protocol_is_read_only(self):
        manager = KernelManager()
        manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name("kernel.py")),
                                    "-f", "{connection_file}"]
        manager.start_kernel(env={**os.environ, "DAS_SDK": os.environ["DAS_SDK"]})
        client = manager.client(); client.start_channels()
        try:
            client.wait_for_ready(timeout=30)
            def shell(request):
                while True:
                    reply = client.get_shell_msg(timeout=10)
                    if reply["parent_header"].get("msg_id") == request:
                        return reply
            reply = shell(client.complete("pri", cursor_pos=3))
            self.assertEqual(reply["msg_type"], "complete_reply")
            self.assertIn("print", reply["content"]["matches"])
            reply = shell(client.is_complete("def f() {"))
            self.assertEqual(reply["content"]["status"], "incomplete")
            self.assertEqual(reply["content"]["indent"], "    ")
            setup = '''struct Stats {count : int; values : array<int>}
var stats : Stats
stats.count = 5
var counter = 0
//! Increment the counter once.
def bump() : int {counter += 1; return counter}
'''
            reply = shell(client.execute(setup))
            self.assertEqual(reply["content"]["status"], "ok")
            reply = shell(client.complete("stats.co", cursor_pos=8))
            self.assertEqual(reply["content"]["matches"], ["count"])
            self.assertEqual(reply["content"]["cursor_start"], 6)
            reply = shell(client.inspect("bump(", cursor_pos=5))
            self.assertTrue(reply["content"]["found"])
            self.assertIn("def bump() : int", reply["content"]["data"]["text/plain"])
            self.assertIn("Increment the counter", reply["content"]["data"]["text/plain"])
            for code in ("var x = 1", "if (true) {pass}", "bump()"):
                self.assertEqual(shell(client.is_complete(code))["content"]["status"], "complete")
            self.assertEqual(shell(client.is_complete("if true {pass}"))["content"]["status"], "invalid")
            request = client.execute("counter")
            self.assertEqual(shell(request)["content"]["status"], "ok")
            results = []
            while True:
                message = client.get_iopub_msg(timeout=10)
                if message["parent_header"].get("msg_id") != request:
                    continue
                if message["msg_type"] == "execute_result":
                    results.append(message["content"]["data"]["text/plain"])
                if message["msg_type"] == "status" and message["content"]["execution_state"] == "idle":
                    break
            self.assertEqual(results, ["0"], "Editor requests executed the function")
            shell(client.execute("%reset"))
            self.assertNotIn("stats", shell(client.complete("sta", cursor_pos=3))["content"]["matches"])
        finally:
            client.stop_channels(); manager.shutdown_kernel(now=True)

    def test_struct_state_schema_guard_and_reset(self):
        manager = KernelManager()
        manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name("kernel.py")),
                                    "-f", "{connection_file}"]
        manager.start_kernel(env={**os.environ, "DAS_SDK": os.environ["DAS_SDK"]})
        client = manager.client(); client.start_channels()
        try:
            client.wait_for_ready(timeout=30)
            def execute(code):
                request = client.execute(code)
                while True:
                    reply = client.get_shell_msg(timeout=10)
                    if reply["parent_header"].get("msg_id") == request:
                        break
                results = []
                while True:
                    message = client.get_iopub_msg(timeout=10)
                    if message["parent_header"].get("msg_id") != request:
                        continue
                    if message["msg_type"] == "execute_result":
                        results.append(message["content"]["data"]["text/plain"])
                    if message["msg_type"] == "status" and message["content"]["execution_state"] == "idle":
                        return reply["content"], results
            reply, _ = execute("struct Stats {count : int; values : array<int>}\nvar stats : Stats\nstats.count=5\nstats.values |> push(7)")
            self.assertEqual(reply["status"], "ok")
            reply, result = execute("stats.count += 1\nstats.values[0] + stats.count")
            self.assertEqual(result, ["13"])
            reply, _ = execute("struct Stats {count : string; values : array<int>}")
            self.assertEqual(reply["status"], "error")
            self.assertIn("%reset", reply["evalue"])
            _, result = execute("stats.count")
            self.assertEqual(result, ["6"])
            execute("%reset")
            reply, result = execute('struct Stats {count : string}\nvar stats : Stats\nstats.count="new"\nstats.count')
            self.assertEqual(reply["status"], "ok")
            self.assertEqual(result, ["new"])
        finally:
            client.stop_channels(); manager.shutdown_kernel(now=True)

    def test_auto_result_runtime_trace_and_definition_syntax_error(self):
        manager = KernelManager()
        manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name("kernel.py")),
                                    "-f", "{connection_file}"]
        manager.start_kernel(env={**os.environ, "DAS_SDK": os.environ["DAS_SDK"]})
        client = manager.client(); client.start_channels()
        try:
            client.wait_for_ready(timeout=30)
            def execute(code):
                request = client.execute(code)
                while True:
                    reply = client.get_shell_msg(timeout=10)
                    if reply["parent_header"].get("msg_id") == request:
                        break
                messages = []
                while True:
                    message = client.get_iopub_msg(timeout=10)
                    if message["parent_header"].get("msg_id") != request:
                        continue
                    messages.append(message)
                    if message["msg_type"] == "status" and message["content"]["execution_state"] == "idle":
                        return reply["content"], messages
            reply, messages = execute("var x = 10\nx * 2")
            self.assertEqual(reply["status"], "ok")
            self.assertIn("20", [m["content"].get("data", {}).get("text/plain") for m in messages])
            _, messages = execute("x;")
            self.assertFalse(any(m["msg_type"] == "execute_result" for m in messages))
            reply, _ = execute('def fail() {\npanic("boom")\n}')
            definition_count = reply["execution_count"]
            reply, _ = execute("fail()")
            self.assertEqual(reply["ename"], "ExecutionError")
            self.assertIn(f"In[{definition_count}], line 2", reply["evalue"])
            self.assertIn("daslang stack:", reply["evalue"])
            original = (Path(__file__).parent / "tests/fixtures/primes_original.txt").read_text()
            reply, _ = execute(original)
            self.assertEqual(reply["ename"], "CompileError")
            self.assertIn(f"In[{reply['execution_count']}], line 6", reply["evalue"])
        finally:
            client.stop_channels(); manager.shutdown_kernel(now=True)

    def test_streaming_survives_interrupt_and_errors_show_cell_lines(self):
        manager = KernelManager()
        manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name("kernel.py")),
                                    "-f", "{connection_file}"]
        manager.kernel_spec.interrupt_mode = "message"
        manager.start_kernel(env={**os.environ, "DAS_SDK": os.environ["DAS_SDK"]})
        client = manager.client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=30)
            def finish(request):
                while True:
                    reply = client.get_shell_msg(timeout=10)
                    if reply["parent_header"].get("msg_id") == request:
                        break
                messages = []
                while True:
                    message = client.get_iopub_msg(timeout=10)
                    if message["parent_header"].get("msg_id") != request:
                        continue
                    messages.append(message)
                    if message["msg_type"] == "status" and message["content"]["execution_state"] == "idle":
                        return reply["content"], messages
            finish(client.execute("var x = 7"))
            request = client.execute('print("Привет до Interrupt\\n")\nx = 99\nwhile (true) {pass}')
            text = ""
            while "Привет до Interrupt\n" not in text:
                message = client.get_iopub_msg(timeout=10)
                if message["parent_header"].get("msg_id") == request and message["msg_type"] == "stream":
                    text += message["content"]["text"]
            self.assertFalse(client.shell_channel.msg_ready(), "Cell finished before the output appeared")
            manager.interrupt_kernel()
            reply, tail = finish(request)
            self.assertEqual(reply["ename"], "KeyboardInterrupt")
            text += "".join(m["content"].get("text", "") for m in tail if m["msg_type"] == "stream")
            self.assertEqual(text, "Привет до Interrupt\n")
            reply, _ = finish(client.execute("var y = 2\nmissing_function()"))
            self.assertEqual(reply["ename"], "CompileError")
            self.assertIn(f"In[{reply['execution_count']}], line 2", reply["evalue"])
            self.assertNotIn("notebook.das:", reply["evalue"])
        finally:
            client.stop_channels()
            manager.shutdown_kernel(now=True)

    def test_jupyter_interrupt_recovers_without_restarting_kernel(self):
        manager = KernelManager()
        manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name("kernel.py")),
                                    "-f", "{connection_file}"]
        manager.kernel_spec.interrupt_mode = "message"
        manager.start_kernel(env={**os.environ, "DAS_SDK": os.environ["DAS_SDK"]})
        client = manager.client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=30)

            def collect(request):
                while True:
                    reply = client.get_shell_msg(timeout=10)
                    if reply["parent_header"].get("msg_id") == request:
                        break
                messages = []
                while True:
                    message = client.get_iopub_msg(timeout=10)
                    if message["parent_header"].get("msg_id") != request:
                        continue
                    messages.append(message)
                    if message["msg_type"] == "status" and message["content"]["execution_state"] == "idle":
                        return reply["content"], messages

            reply, _ = collect(client.execute("var x = 7"))
            self.assertEqual(reply["status"], "ok")
            for expected in ("7", "10"):
                request = client.execute("x = 999\nwhile (true) {x += 1}")
                while True:
                    message = client.get_iopub_msg(timeout=10)
                    if (message["parent_header"].get("msg_id") == request
                            and message["msg_type"] == "status"
                            and message["content"]["execution_state"] == "busy"):
                        break
                time.sleep(0.4)
                start = time.monotonic()
                manager.interrupt_kernel()
                reply, _ = collect(request)
                self.assertEqual(reply["ename"], "KeyboardInterrupt", reply)
                self.assertLess(time.monotonic() - start, 5)
                self.assertTrue(manager.is_alive())
                reply, messages = collect(client.execute("%eval x"))
                self.assertEqual(reply["status"], "ok", reply)
                self.assertIn(expected, [m["content"].get("data", {}).get("text/plain") for m in messages])
                collect(client.execute("x += 3"))
        finally:
            client.stop_channels()
            manager.shutdown_kernel(now=True)

    def test_state_output_errors_and_redefinition(self):
        manager = KernelManager()
        manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name("kernel.py")),
                                    "-f", "{connection_file}"]
        manager.start_kernel(env={**os.environ, "DAS_SDK": os.environ["DAS_SDK"]})
        client = manager.client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=30)

            def execute(code, expected="ok"):
                request = client.execute(code)
                while True:
                    reply = client.get_shell_msg(timeout=30)
                    if reply["parent_header"].get("msg_id") == request:
                        break
                self.assertEqual(reply["content"]["status"], expected, reply["content"])
                messages = []
                while True:
                    message = client.get_iopub_msg(timeout=30)
                    if message["parent_header"].get("msg_id") != request:
                        continue
                    messages.append(message)
                    if message["msg_type"] == "status" and message["content"]["execution_state"] == "idle":
                        return messages

            execute('var x = 10\nvar y = x * 2\nprint("mixed result: {y}\\n")')
            execute("x += 5")
            messages = execute('%eval x')
            self.assertIn("15", [m["content"].get("data", {}).get("text/plain") for m in messages])
            messages = execute("%eval y")
            self.assertIn("20", [m["content"].get("data", {}).get("text/plain") for m in messages])
            messages = execute('print("native output: {x}\\n")')
            self.assertEqual("".join(m["content"].get("text", "") for m in messages if m["msg_type"] == "stream"),
                             "native output: 15\n")
            execute("missing_function()", "error")
            execute('x += 1\npanic("recoverable")', "error")
            messages = execute("%eval x")
            self.assertIn("16", [m["content"].get("data", {}).get("text/plain") for m in messages])
            execute("def f() : int { return x * 2 }")
            execute("def f() : int { return x * 3 }")
            messages = execute("%eval f()")
            self.assertIn("48", [m["content"].get("data", {}).get("text/plain") for m in messages])
        finally:
            client.stop_channels()
            manager.shutdown_kernel(now=True)


if __name__ == "__main__":
    unittest.main()
