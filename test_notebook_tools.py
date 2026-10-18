"""End-to-end SDK tools, MIME output and snapshot isolation."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
import sys

from jupyter_client import KernelManager

from editor_services import EditorServices
from rich_display import bundle, table
from session import Runtime, NotebookSession, ExecutionError
from tools_magic import parse_tools, run_tools
from worker_client import WorkerClient


class NotebookToolsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.folder.name)
        self.runtime = Runtime(os.environ["DAS_SDK"])
        self.session = NotebookSession(self.runtime)
        self.debug = None

    def tearDown(self):
        if self.debug:
            self.debug.close()
        self.session.close()
        self.runtime.close()
        os.chdir(self.cwd)
        self.folder.cleanup()

    def tool(self, code):
        result, metadata, self.debug = run_tools(self.session, parse_tools(code), self.debug)
        return result, metadata

    def test_real_display_struct_table_html_markdown_image_and_audio(self):
        Path('pixel.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
        with wave.open('tone.wav', 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframes(b'\0\0' * 80)
        self.session.execute('''struct Row {name:string; score:int}
var row:Row
row.name="<script>"
row.score=42
var rows:array<Row>
rows |> push(row)
display(rows)
display_html("<b>Hello</b>")
display_markdown("**Hello**")
display_image("pixel.png")
display_audio("tone.wav")''')
        output = self.session.last_displays
        self.assertEqual(len(output), 5)
        self.assertIn('&lt;script&gt;', output[0]['data']['text/html'])
        self.assertNotIn('<script>', output[0]['data']['text/html'])
        self.assertEqual(output[1]['data']['text/html'], '<b>Hello</b>')
        self.assertIn('text/markdown', output[2]['data'])
        self.assertIn('image/png', output[3]['data'])
        self.assertIn('data:audio/wav;base64,', output[4]['data']['text/html'])
        self.assertIn('50 / 100 rows', table(list(range(100))))

    def test_display_errors_limits_and_no_replay(self):
        with self.assertRaises(ExecutionError):
            self.session.execute('display(42)\npanic("expected")')
        self.assertEqual(len(self.session.last_displays), 1)
        self.session.execute('1')
        self.assertEqual(self.session.last_displays, [])
        with self.assertRaises(ExecutionError):
            self.session.execute('for (i in range(101)) {display(i)}')
        self.assertLessEqual(len(self.session.last_displays), 100)
        Path('bad.png').write_text('not an image')
        with self.assertRaises(ValueError):
            bundle('image', 'bad.png')

    def test_export_restores_nested_values_readonly_bindings_and_no_history_replay(self):
        self.session.execute('''struct Pair {value:int}
var p:Pair
p.value=42
var items:array<int>
items |> push(7)
let constant=9
def answer():int {return p.value+items[0]+constant}''')
        before = self.session.snapshot()
        output, _ = self.tool('%%export "snapshot file.das"\nverify(answer()==58)\nprint("export-ok\\n")')
        self.assertIn('snapshot file.das', output)
        from sdk_tools import SDKTools
        self.assertIn('export-ok', SDKTools(self.runtime.sdk).run([str(Path('snapshot file.das').resolve())]))
        self.assertEqual(self.session.snapshot(), before)
        self.tool('%export --definitions-only definitions.das')
        self.assertNotIn('base64_decode("', Path('definitions.das').read_text())

    def test_isolated_assertions_failures_and_file_test_runner(self):
        self.session.execute('var value=42')
        before = self.session.snapshot()
        output, _ = self.tool('%%test\nassert(value==42)\nvalue=99')
        self.assertIn('PASS', output)
        with self.assertRaises(RuntimeError):
            self.tool('%%test\nassert(value==0)')
        self.assertEqual(self.session.snapshot(), before)
        Path('test_sample.das').write_text('''options gen2
require dastest/testing_boost
[test]
def notebook_case(t:T?) { t |> equal(2+2,4) }
''')
        output, _ = self.tool('%test test_sample.das --failures-only')
        self.assertIn('PASS', output.upper())

    def test_lsp_diagnostics_hover_and_definition_use_session_types_without_execution(self):
        self.session.execute('var value=42\ndef add_one(x:int):int{return x+1}')
        before = self.session.snapshot()
        _, metadata = self.tool('%%check\nvalue=99\nmissing_notebook_function()')
        errors = [d for d in metadata['language']['diagnostics'] if d['severity'] == 1]
        self.assertTrue(any(d.get('line') == 3 for d in errors), errors)
        _, metadata = self.tool('%%lsp hover 3\nadd_one(value)')
        self.assertIn('add_one', metadata['language']['contents'])
        _, metadata = self.tool('%definition add_one(value)')
        self.assertTrue(metadata['language']['locations'], metadata)
        self.assertEqual(self.session.snapshot(), before)

    def test_coverage_maps_cells_filters_sdk_and_exports_usable_lcov(self):
        self.session.execute('var value=42')
        before = self.session.snapshot()
        output, metadata = self.tool('%%coverage --output notebook.lcov\nif (value==42) {value+=1} else {value=0}')
        self.assertIn('Branches: 1/2', output)
        self.assertTrue(metadata['coverage'])
        report = Path('notebook.lcov').read_text()
        self.assertNotIn('C:/src/daScript/daslib/', report)
        self.assertIn('BRDA:', report)
        self.assertTrue(Path('notebook.source.das').is_file())
        self.assertEqual(self.session.snapshot(), before)

    def test_debug_breakpoint_step_stack_evaluate_and_unchanged_notebook(self):
        self.session.execute('var value=42')
        before = self.session.snapshot()
        output, _ = self.tool('%%debug --break 2\nvalue+=1\nprint("{value}\\n")')
        self.assertEqual(json.loads(output)['event']['event'], 'stopped')
        output, _ = self.tool('%debug stack')
        self.assertEqual(json.loads(output)['stackFrames'][0]['cellLine'], 2)
        output, _ = self.tool('%debug eval value')
        self.assertEqual(json.loads(output)['result'], '42')
        self.tool('%debug next')
        output, _ = self.tool('%debug eval value')
        self.assertEqual(json.loads(output)['result'], '43')
        output, _ = self.tool('%debug continue')
        self.assertIn(json.loads(output)['event'], ('terminated', 'exited'))
        self.assertIsNone(self.debug)
        self.assertEqual(self.session.snapshot(), before)
        self.assertEqual(list(Path.cwd().glob('.djnb-*.das')), [])

    def test_local_package_install_lockfile_and_require_without_restart(self):
        package = Path('fixture_package')
        package.mkdir()
        (package/'.das_package').write_text('''options gen2
require daslib/daspkg
[export]
def package() {package_name("fixture_package");package_description("Notebook fixture")}
[export]
def build() {no_build()}
''')
        (package/'.das_module').write_text('''options gen2
require daslib/fio
[export]
def initialize(path:string) { register_native_path("nbfixture", "answer", "{path}/answer.das") }
''')
        (package/'answer.das').write_text('options gen2\nmodule answer public\ndef answer():int{return 42}\n')
        output, _ = self.tool('%pkg install ./fixture_package')
        self.assertTrue(Path('daspkg.lock').is_file(), output)
        self.assertEqual(self.session.execute('require nbfixture/answer\nanswer()'), '42')
        output, _ = self.tool('%pkg list')
        self.assertIn('fixture_package', output)
        with self.assertRaises(ValueError):
            self.tool('%pkg install anything --global')

    def test_worker_rich_output_error_reset_and_editor_help(self):
        worker = WorkerClient()
        try:
            result = worker.execute('display(fixed_array(1,2))')
            self.assertEqual(result['status'], 'ok', result)
            self.assertIn('text/html', result['display_data'][0]['data'])
            result = worker.execute('display(42)\npanic("expected")')
            self.assertEqual(result['status'], 'error')
            self.assertEqual(len(result['display_data']), 1)
            result = worker.execute('%reset')
            self.assertEqual(result.get('display_data'), [])
        finally:
            worker.close()
        editor = EditorServices(lambda: {})
        self.assertTrue(editor.inspect('%export file.das', 4)['found'])
        self.assertEqual(editor.is_complete('%%test\nassert(true)')['status'], 'complete')
        self.assertEqual(editor.complete('%%cov', 5)['matches'], ['%%coverage'])

    def test_jupyter_rich_display_variable_view_and_compiler_hover(self):
        manager = KernelManager()
        manager.kernel_spec.argv = [sys.executable, str(Path(__file__).with_name('kernel.py')), '-f', '{connection_file}']
        manager.start_kernel(env={**os.environ, 'DAS_SDK': os.environ['DAS_SDK']})
        client = manager.client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=30)
            def execute(code):
                request = client.execute(code)
                while True:
                    reply = client.get_shell_msg(timeout=30)
                    if reply['parent_header'].get('msg_id') == request:
                        self.assertEqual(reply['content']['status'], 'ok', reply)
                        break
                output = []
                while True:
                    message = client.get_iopub_msg(timeout=30)
                    if message['parent_header'].get('msg_id') != request:
                        continue
                    output.append(message)
                    if message['msg_type'] == 'status' and message['content']['execution_state'] == 'idle':
                        return output
            output = execute('var value=42\ndef square(x:int):int{return x*x}\ndisplay(value)')
            self.assertTrue(any('text/html' in item['content'].get('data', {}) for item in output))
            output = execute('%view value')
            self.assertTrue(any(item['msg_type'] == 'display_data' for item in output))
            execute('%lsp on')
            request = client.inspect('square(value)', cursor_pos=3)
            while True:
                response = client.get_shell_msg(timeout=30)
                if response['parent_header'].get('msg_id') == request:
                    break
            self.assertTrue(response['content']['found'], response)
            self.assertIn('text/markdown', response['content']['data'])
            self.assertIn('square', response['content']['data']['text/markdown'])
        finally:
            client.stop_channels()
            manager.shutdown_kernel(now=True)


if __name__ == '__main__':
    unittest.main()
