import json
import os
import unittest

from session import Runtime, NotebookSession
from tools_magic import parse_tools, run_tools
from variables_view import read_value
from worker_client import WorkerClient


class VariableViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = Runtime(os.environ['DAS_SDK'])

    @classmethod
    def tearDownClass(cls):
        cls.runtime.close()

    def setUp(self):
        self.session = NotebookSession(self.runtime)

    def tearDown(self):
        self.session.close()

    def test_struct_array_table_and_let_views_are_readonly(self):
        self.session.execute('''struct Player {name:string; score:int; values:array<int>}
var player:Player
player.name="<Alice>"
player.score=42
player.values |> push(7)
var players:array<Player>
players |> push_clone(player)
let limit=100
var scores:table<string;int>
scores["Alice"]=42
var calls=0
def bump():int {calls+=1;return calls}''')
        before = self.session.snapshot()
        result, meta, _ = run_tools(self.session, parse_tools('%vars'))
        self.assertIsNone(result)
        self.assertIn('let', meta['display_data'][0]['data']['text/plain'])
        _, meta, _ = run_tools(self.session, parse_tools('%view players'))
        html = meta['display_data'][0]['data']['text/html']
        self.assertIn('<table>', html)
        self.assertIn('&lt;Alice&gt;', html)
        self.assertIn('<details', html)
        table = read_value(self.session, 'scores')
        self.assertEqual(table['items'][0]['key']['value'], 'Alice')
        self.assertEqual(table['items'][0]['value']['value'], 42)
        self.assertEqual(read_value(self.session, 'limit')['value'], 100)
        self.assertEqual(self.session.snapshot(), before)
        self.assertEqual(self.session.execute('calls'), '0')

    def test_large_array_ranges_and_depth_limits(self):
        self.session.execute('var values:array<int>\nfor (i in range(100000)) {values |> push(i)}')
        node = read_value(self.session, 'values', 99995, 3, 2)
        self.assertEqual(node['size'], 100000)
        self.assertEqual([item['value']['value'] for item in node['items']], [99995, 99996, 99997])
        self.assertEqual(read_value(self.session, 'values', 100001, 3, 2)['items'], [])
        shallow = read_value(self.session, 'values', 0, 2, 0)
        self.assertEqual(shallow['items'][0]['value']['kind'], 'truncated')

    def test_strings_unicode_special_floats_and_invalid_requests(self):
        self.session.execute('var text="Привет 🌍"\nvar nan_value=0.0/0.0')
        self.assertEqual(read_value(self.session, 'text')['value'], 'Привет 🌍')
        self.assertIsInstance(read_value(self.session, 'nan_value')['value'], str)
        for command in ('%view text --count 0', '%view text --depth 99', '%view text --start -1', '%view bump()', '%vars --count 1000', '%view text --start 99999999999'):
            with self.assertRaises(ValueError):
                run_tools(self.session, parse_tools(command))

    def test_readonly_worker_preserves_checkpoint_without_reserializing_it(self):
        worker = WorkerClient()
        try:
            worker.execute('var value=42')
            checkpoint = worker.state
            reply = worker.execute('%view value')
            self.assertEqual(reply['status'], 'ok', reply)
            self.assertTrue(reply['readonly'])
            self.assertNotIn('state', reply)
            self.assertIs(worker.state, checkpoint)
            self.assertIn('text/html', reply['display_data'][0]['data'])
            reply = worker.execute('%view missing')
            self.assertEqual(reply['status'], 'error')
            self.assertIs(worker.state, checkpoint)
            self.assertEqual(worker.execute('value')['result'], '42')
        finally:
            worker.close()


if __name__ == '__main__':
    unittest.main()
