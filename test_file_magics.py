"""File magics and actual native macro compilation through Jupyter."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from jupyter_client import KernelManager
from file_magics import parse_writefile, writefile
from editor_services import EditorServices


class FileMagicTests(unittest.TestCase):
    def test_exact_body_windows_paths_append_and_atomic_failure(self):
        request = parse_writefile('%%writefile "C:\\source files\\module.das"\r\n// Привет\r\n')
        self.assertEqual(str(request[0]), 'C:\\source files\\module.das')
        self.assertEqual(request[1], '// Привет\r\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested/module.das"
            writefile((path, "original\n", False))
            with patch("file_magics.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    writefile((path, "replacement", False))
            self.assertEqual(path.read_bytes(), b"original\n")
            self.assertEqual(list(path.parent.iterdir()), [path])
            writefile((path, "// Привет\n", True))
            self.assertEqual(path.read_text(encoding="utf-8"), "original\n// Привет\n")

    def test_editor_treats_file_body_as_text_without_execution(self):
        editor = EditorServices(lambda: {})
        self.assertEqual(editor.complete("%%wr", 4)["matches"], ["%%writefile"])
        self.assertTrue(editor.inspect("%%writefile module.das", 8)["found"])
        self.assertEqual(editor.is_complete("%%writefile x.das\nclass NotFinished {"), {"status": "complete"})
        self.assertEqual(editor.is_complete("%%writefile"), {"status": "invalid"})

    def test_native_macro_creation_reload_error_and_checkpoint(self):
        source = (Path(__file__).parent / "examples/notebook_macros.das").read_text()
        with tempfile.TemporaryDirectory() as folder:
            manager = KernelManager()
            manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name("kernel.py")), "-f", "{connection_file}"]
            manager.kernel_spec.interrupt_mode = "message"
            manager.start_kernel(cwd=folder, env={**os.environ, "DAS_SDK": os.environ["DAS_SDK"]})
            client = manager.client()
            client.start_channels()
            try:
                client.wait_for_ready(timeout=30)

                def execute(code, status="ok"):
                    request = client.execute(code)
                    while True:
                        reply = client.get_shell_msg(timeout=60)
                        if reply["parent_header"].get("msg_id") == request:
                            break
                    self.assertEqual(reply["content"]["status"], status, reply["content"])
                    values = []
                    while True:
                        message = client.get_iopub_msg(timeout=60)
                        if message["parent_header"].get("msg_id") != request:
                            continue
                        if message["msg_type"] == "execute_result":
                            values.append(message["content"]["data"]["text/plain"])
                        if message["msg_type"] == "status" and message["content"]["execution_state"] == "idle":
                            break
                    return values

                execute('%%writefile "macro files/notebook_macros.das"\n' + source)
                self.assertEqual((Path(folder) / "macro files/notebook_macros.das").read_text(encoding="utf-8"), source)
                execute("var saved=40")
                # A textual file body is never executed as a cell.
                execute("%%writefile not_executed.das\nsaved=999\npanic(\"do not run\")")
                self.assertEqual(execute("saved"), ["40"])
                # Use a simple require path; the file magic's quoted paths are independent of daslang require syntax.
                execute("%%writefile notebook_macros.das\n" + source)
                self.assertEqual(execute("require ./notebook_macros.das\nplus_one(saved)"), ["41"])
                updated = source.replace(" + 1)", " + 2)")
                execute("%%writefile notebook_macros.das\n" + updated)
                self.assertEqual(execute("plus_one(saved)"), ["42"])
                execute("%%writefile notebook_macros.das\noptions gen2\nthis is invalid daslang")
                execute("plus_one(saved)", "error")
                execute("%%writefile notebook_macros.das\n" + updated)
                self.assertEqual(execute("saved"), ["40"])
                self.assertEqual(execute("plus_one(saved)"), ["42"])
                # Restoring the checkpoint after a worker interruption must recompile the module, not replay file cells.
                request = client.execute('print("RUNNING\\n")\nwhile (true) {saved+=1}')
                output = ""
                while True:
                    message = client.get_iopub_msg(timeout=30)
                    if message["parent_header"].get("msg_id") != request:
                        continue
                    if message["msg_type"] == "error":
                        self.fail(str(message["content"]))
                    if message["msg_type"] == "stream":
                        output += message["content"]["text"]
                        if "RUNNING" in output:
                            break
                manager.interrupt_kernel()
                while True:
                    reply = client.get_shell_msg(timeout=30)
                    if reply["parent_header"].get("msg_id") == request:
                        self.assertEqual(reply["content"]["ename"], "KeyboardInterrupt")
                        break
                self.assertEqual(execute("plus_one(saved)"), ["42"])
            finally:
                client.stop_channels()
                manager.shutdown_kernel(now=True)


if __name__ == "__main__":
    unittest.main()
