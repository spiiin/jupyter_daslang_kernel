import os,time,json,unittest
from strudel_magic import StrudelMagic,parse_strudel
from worker_client import WorkerClient


class SharedStrudelTests(unittest.TestCase):
    def test_definitions_and_dependencies(self):
        m=StrudelMagic()
        self.assertIsNone(parse_strudel('bells()'))
        self.assertEqual(parse_strudel('%%strudel --port 9010\ndef x() {}')[0]['port'],9010)
        self.assertIn('x',m.functions('def x() { pass }'))
        for source in ('def x(a:int) {}','var a=1','def x() : int { return 1 }'):
            with self.assertRaises(ValueError): m.functions(source)
        defs={'a':'def a() { b() }','b':'def b() { pass }','c':'def c() { pass }'}
        self.assertEqual(m.dependencies('a',defs),{'a','b'})

    @unittest.skipUnless(os.environ.get('DASLANG_LIVE'),'Set DASLANG_LIVE for live integration')
    def test_shared_clock_swap_stop_errors_gc_and_shutdown(self):
        m=StrudelMagic(); w=WorkerClient()
        def define(source):
            return m.execute(parse_strudel('%%strudel\n'+source),w,lambda n,t:print(t,flush=True),'shared-test')
        def call(source):
            r=w.execute(source)
            self.assertEqual(r['status'],'ok',r.get('error'))
            return r.get('result')
        def state(): return m.request('djnb_state')
        def wait_for(predicate,timeout=10):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                s=state()
                if predicate(s): return s
                time.sleep(.1)
            self.fail('Timed out: '+json.dumps(s)+'\n'+m.tail())
        try:
            define('def bells() { strudel_add_track(note("a4 ~ c5 e5", "sine") |> gain(0.02)) }\n'
                   'def bass() { strudel_add_track(note("a2 ~ e2 ~", "sine") |> gain(0.02)) }')
            process=m.process; pid=process.pid
            self.assertIn('queued: bells',call('bells()'))
            call('bass()')
            before=wait_for(lambda s: all(s['parts'][n]['current']>0 and s['parts'][n]['events']>0 for n in ('bells','bass')))
            bass_id=before['parts']['bass']['current']; old_bells=before['parts']['bells']['current']
            self.assertAlmostEqual(before['parts']['bells']['last_gain'],.02,places=5)
            define('def bells() { strudel_add_track(note("c5 e5 g5 ~", "triangle") |> gain(0.02)) }')
            after=wait_for(lambda s: s['parts']['bells']['current'] not in (0,old_bells) and not s['parts']['bells']['waiting'])
            self.assertEqual(m.process.pid,pid)
            self.assertGreater(after['seconds'],before['seconds'])
            self.assertEqual(after['parts']['bass']['current'],bass_id)
            self.assertEqual(after['parts']['bass']['swaps'],before['parts']['bass']['swaps'])
            self.assertGreater(after['parts']['bass']['queries'],before['parts']['bass']['queries'])
            self.assertEqual(after['parts']['bells']['boundary']%1,0)
            for part in after['parts'].values():
                self.assertEqual(part['rewinds'],0)
                self.assertLess(part['max_gap'],1e-7)
            # Both wrappers use exactly the same absolute scheduler span.
            self.assertLess(abs(after['parts']['bells']['last_stop']-after['parts']['bass']['last_stop']),.05)
            wait_for(lambda s: s['retained_contexts']==2)
            previous=m.definitions.copy()
            with self.assertRaises(RuntimeError): define('def bells() { no_such_function() }')
            self.assertEqual(m.definitions,previous)
            self.assertEqual(state()['parts']['bass']['current'],bass_id)
            with self.assertRaises(RuntimeError): define('def bells() { panic("bad construction") }')
            self.assertEqual(m.definitions,previous)
            self.assertIn('stop queued',call('strudel_stop("bells")'))
            stopped=wait_for(lambda s:s['parts']['bells']['current']==0 and not s['parts']['bells']['waiting'])
            self.assertEqual(stopped['parts']['bass']['current'],bass_id)
            call('bells()')
            wait_for(lambda s:s['parts']['bells']['current']>0 and not s['parts']['bells']['waiting'])
            # Batch replacement uses one boundary, not one independently chosen per track.
            define('def bells() { strudel_add_track(note("a4 e5", "sine") |> gain(0.02)) }\n'
                   'def bass() { strudel_add_track(note("a2 e2", "sine") |> gain(0.02)) }')
            batch=wait_for(lambda s:not any(p['waiting'] for p in s['parts'].values()) and s['parts']['bass']['current']!=bass_id)
            self.assertEqual(batch['parts']['bells']['boundary'],batch['parts']['bass']['boundary'])
            wait_for(lambda s:s['retained_contexts']==2)
            self.assertIn('cycle',call('strudel_status()'))
            self.assertNotIn('reloading',m.tail())
            self.assertNotIn('pattern query failed',m.tail())
            m.close()
            self.assertIsNotNone(process.poll())
            self.assertEqual(w.execute('bells()')['status'],'error')
        except BaseException:
            print(m.tail(),flush=True)
            raise
        finally:
            m.close();w.close()

if __name__=='__main__': unittest.main()
