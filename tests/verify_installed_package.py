"""Run from outside the checkout with the clean environment's Python."""
import os
from pathlib import Path
import sys
import unittest
from jupyter_client import KernelManager
from daslang_jupyter.cli import configuration, kernelspec, check_runtime
from daslang_jupyter import __version__
from daslang_jupyter.worker_client import WorkerCrashed


SDK = os.environ.get("DAS_SDK", "C:/src/daScript")
KERNEL = os.environ.get("DAS_TEST_KERNEL_NAME", "daslang")


class InstalledPackageTests(unittest.TestCase):
    def test_new_language_features_through_jupyter(self):
        manager = KernelManager(kernel_name=KERNEL)
        manager.start_kernel(cwd=str(Path(__file__).parent))
        client = manager.client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=30)
            def shell(request):
                while True:
                    msg = client.get_shell_msg(timeout=30)
                    if msg["parent_header"].get("msg_id") == request:
                        return msg["content"]
            def execute(code, expected="ok"):
                request = client.execute(code)
                reply = shell(request)
                self.assertEqual(reply["status"], expected, reply)
                values = []
                while True:
                    msg = client.get_iopub_msg(timeout=30)
                    if msg["parent_header"].get("msg_id") != request:
                        continue
                    if msg["msg_type"] == "execute_result":
                        values.append(msg["content"]["data"]["text/plain"])
                    if msg["msg_type"] == "status" and msg["content"]["execution_state"] == "idle":
                        break
                return values
            execute('''require math as m
var calls=0
def next():int {calls+=1;return calls}
let saved=next()
def choose(x:int):int {return x+5}
def choose(x:string):string {return x+"!"}''')
            self.assertEqual(execute("choose(saved)"), ["6"])
            self.assertEqual(execute('choose("ok")'), ["ok!"])
            self.assertEqual(execute("calls"), ["1"])
            matches = shell(client.complete("choo", 4))["matches"]
            self.assertEqual(matches, ["choose"])
            help_text = shell(client.inspect("choose(", 7))["data"]["text/plain"]
            self.assertIn("x:int", help_text)
            self.assertIn("x:string", help_text)
            self.assertIn("let saved", shell(client.inspect("saved", 5))["data"]["text/plain"])
            execute("saved=99", "error")
            self.assertEqual(execute("saved"), ["1"])
            execute("def choose(renamed:int):int {return renamed*10}")
            self.assertEqual(execute("choose(saved)"), ["10"])
            self.assertEqual(execute('choose("ok")'), ["ok!"])
            self.assertEqual(execute("m::sqrt(9.0)"), ["3"])
        finally:
            client.stop_channels()
            manager.shutdown_kernel(now=True)
            del client, manager

    def test_package_and_spec(self):
        import daslang_jupyter
        self.assertEqual(__version__, "0.12.0a1")
        self.assertIn("site-packages", str(Path(daslang_jupyter.__file__)))
        env = configuration(SDK)
        self.assertIn("site-packages", env["DAS_PARSER"])
        spec = kernelspec(env)
        self.assertEqual(spec["argv"][:3], [sys.executable, "-m", "daslang_jupyter.kernel"])
        self.assertEqual(spec["interrupt_mode"], "message")

    def test_configuration_failures(self):
        with self.assertRaisesRegex(ValueError, "Specify --sdk"):
            configuration(None)
        with self.assertRaisesRegex(ValueError, "Missing libDaScriptDyn"):
            configuration(Path(__file__).parent)
        with self.assertRaisesRegex(ValueError, "Missing component"):
            configuration(SDK, parser=Path(__file__).with_suffix(".missing"))

    def test_failed_check_restores_environment(self):
        before = dict(os.environ)
        env = configuration(SDK)
        env["DAS_RUNTIME_BRIDGE"] = str(Path(__file__).with_suffix(".missing"))
        with self.assertRaises(WorkerCrashed):
            check_runtime(env)
        self.assertEqual(dict(os.environ), before)

    def test_installed_kernel_state_editor_interrupt(self):
        manager = KernelManager(kernel_name=KERNEL)
        manager.start_kernel(cwd=str(Path(__file__).parent))
        client = manager.client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=30)
            def shell(request):
                while True:
                    msg = client.get_shell_msg(timeout=30)
                    if msg["parent_header"].get("msg_id") == request:
                        return msg["content"]
            self.assertIn("print", shell(client.complete("pri", 3))["matches"])
            self.assertEqual(shell(client.execute("var x = 7"))["status"], "ok")
            request = client.execute('x = 999\nprint("RUNNING\\n")\nwhile (true) {x += 1}')
            text = ""
            while True:
                msg = client.get_iopub_msg(timeout=10)
                if msg["parent_header"].get("msg_id") == request and msg["msg_type"] == "error":
                    self.fail(str(msg["content"]))
                if msg["parent_header"].get("msg_id") == request and msg["msg_type"] == "stream":
                    text += msg["content"]["text"]
                    if "RUNNING" in text:
                        break
            manager.interrupt_kernel()
            self.assertEqual(shell(request)["ename"], "KeyboardInterrupt")
            request = client.execute("x")
            self.assertEqual(shell(request)["status"], "ok")
            while True:
                msg = client.get_iopub_msg(timeout=10)
                if msg["parent_header"].get("msg_id") == request and msg["msg_type"] == "execute_result":
                    self.assertEqual(msg["content"]["data"]["text/plain"], "7")
                    break
        finally:
            client.stop_channels()
            manager.shutdown_kernel(now=True)
            del client, manager


if __name__ == "__main__":
    unittest.main(verbosity=2)
