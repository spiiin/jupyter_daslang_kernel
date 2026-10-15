import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from strudel_magic import StrudelMagic, parse_strudel, das_string
from worker_client import WorkerClient


class StrudelTests(unittest.TestCase):
    def test_header_and_syntax(self):
        self.assertIsNone(parse_strudel('def x() {}'))
        self.assertEqual(parse_strudel('%%strudel --exe "C:\\a b\\live.exe"\ndef x() {}')[0]['exe'], 'C:\\a b\\live.exe')
        with self.assertRaises(ValueError): parse_strudel('%%strudel --port 99999')
        with self.assertRaises(ValueError): parse_strudel('%%strudel --stop\ndef x() {}')
        m=StrudelMagic()
        self.assertIn('x',m.functions('def x() { print("ok") }'))
        for code in ('def x(a : int) {}', 'var x=1', 'def x() : int { return 2 }', 'def djnb_x() {}'):
            with self.subTest(code=code), self.assertRaises(ValueError): m.functions(code)

    def test_dependencies_and_editor(self):
        definitions={'a':'def a() { b() }','b':'def b() {}','c':'def c() {}'}
        self.assertEqual(StrudelMagic.dependencies('a',definitions),{'a','b'})
        self.assertEqual(StrudelMagic.dependencies('c',definitions),{'c'})
        from editor_services import EditorServices
        editor=EditorServices(lambda: {})
        self.assertIn('%%strudel',editor.complete('%%str',5)['matches'])
        self.assertEqual(editor.is_complete('%%strudel\ndef x() {}')['status'],'complete')
        self.assertEqual(editor.is_complete('%%strudel\ndef x() {')['status'],'incomplete')

    def test_kernel_dispatch_without_audio(self):
        from kernel import DaslangKernel
        from unittest.mock import MagicMock
        kernel=DaslangKernel()
        kernel.strudel=MagicMock()
        kernel.strudel.execute.return_value={'status':'ok','result':None}
        try:
            self.assertEqual(kernel.do_execute('%%strudel\ndef x() {}',True)['status'],'ok')
            kernel.strudel.execute.assert_called_once()
            self.assertIsNone(kernel.worker_client.worker)
        finally:
            kernel.do_shutdown(False)

if __name__=='__main__': unittest.main()
