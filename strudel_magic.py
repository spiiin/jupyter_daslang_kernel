"""Compile notebook music functions in an owned daslang-live process.

Functions compile into retained contexts in one host. The contexts only emit
Hap data; a permanent audio scheduler queries them on a single sample clock.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid

if __package__:
    from .cell_parser import CellParser
    from .file_magics import writefile
else:
    from cell_parser import CellParser
    from file_magics import writefile

STRUDEL_HELP = """%%strudel [--exe PATH] [--sf2 PATH] [--port N]
Define zero-argument daStrudel functions in one permanent audio player.
Call name() in a normal cell; edit and rerun its definition to replace an active
pattern on the next safe common-cycle boundary. Other tracks keep their clocks,
voices and effects. strudel_stop("name") stops one part; strudel_status() shows
the shared transport. %%strudel --stop closes the notebook's player.
"""


def parse_strudel(code):
    header, _, body = code.partition('\n')
    words = header.strip().split(None, 1)
    if not words or words[0] != '%%strudel':
        return None
    tokens = shlex.split(words[1] if len(words) > 1 else '', posix=False)
    options = {}
    while tokens:
        key = tokens.pop(0)
        if key == '--stop':
            options['stop'] = True
        elif key in ('--exe', '--sf2', '--port') and tokens:
            value = tokens.pop(0)
            if len(value) > 1 and value[0] == value[-1] and value[0] in '\"\'':
                value = value[1:-1]
            options[key[2:]] = value
        else:
            raise ValueError(STRUDEL_HELP)
    if options.get('stop') and (body.strip() or len(options) != 1):
        raise ValueError('--stop must be used alone')
    if 'port' in options:
        options['port'] = int(options['port'])
        if not 1 <= options['port'] <= 65535:
            raise ValueError('Port must be between 1 and 65535')
    return options, body


def das_string(value):
    return json.dumps(value, ensure_ascii=False).replace('{', r'\{').replace('}', r'\}')


class StrudelMagic:
    def __init__(self):
        self.definitions = {}
        self.options = {}
        self.process = None
        self.folder = None
        self.log = None
        self.port = None
        self.token = uuid.uuid4().hex

    def functions(self, body):
        tree = CellParser().parse(body)
        if tree.error:
            raise ValueError('Invalid daScript syntax in %%strudel')
        result = {}
        for node in tree.children:
            if 'comment' in node.kind:
                continue
            if node.kind != 'function_declaration':
                raise ValueError('%%strudel accepts function definitions only; put calls in a normal cell')
            match = re.match(r'def\s+([A-Za-z_]\w*)\s*\(\s*\)\s*(?::\s*void\s*)?\{', node.text)
            if not match or match[1].startswith('djnb_'):
                raise ValueError('Use def name() { ... } (no parameters, annotations or return value; djnb_ is reserved)')
            if match[1] in result:
                raise ValueError('Duplicate function: ' + match[1])
            result[match[1]] = node.text
        if not result:
            raise ValueError('Add at least one def name() { ... }')
        return result

    @staticmethod
    def dependencies(name, definitions):
        found = set()
        pending = [name]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            source = definitions[current]
            pending.extend(n for n in definitions if n not in found and
                           re.search(r'\b' + re.escape(n) + r'\s*\(', source))
        return found

    def request(self, command, **args):
        body = dict(name=command, args=dict(token=self.token, **args))
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/command',
            json.dumps(body).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)

    def tail(self):
        return (self.folder/'player.log').read_text(encoding='utf-8', errors='replace')[-10000:] if self.folder else ''

    def start(self, exe, sf2, port):
        self.folder = Path(tempfile.mkdtemp(prefix='daslang-strudel-shared-'))
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port or 0))
            self.port = probe.getsockname()[1]
        source = Path(__file__).with_name('strudel_host.das').read_text(encoding='utf-8')
        load = ('if (!strudel_load_sf2(' + das_string(sf2) + ')) { panic("Cannot load SF2") }') if sf2 else ''
        source = source.replace('@TOKEN@',self.token).replace('@SF2@',load)
        path=self.folder/'main.das'
        writefile((path,source,False))
        self.log=(self.folder/'player.log').open('w',encoding='utf-8')
        self.process=subprocess.Popen([str(Path(exe).resolve()), '-no-module-cache',
            '--live-port',str(self.port),str(path)],cwd=self.folder,stdout=self.log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        end=time.monotonic()+45
        while time.monotonic()<end:
            if self.process.poll() is not None:
                raise RuntimeError('Player exited:\n'+self.tail())
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/status',timeout=1) as r:
                    status=json.load(r)
                if status.get('has_error'):
                    raise RuntimeError('Player compile failed:\n'+self.tail())
                if self.request('djnb_ready')==self.token:
                    return
            except (OSError,ValueError):
                pass
            time.sleep(.15)
        raise TimeoutError('Player did not initialize:\n'+self.tail())

    def child_source(self, name, definitions):
        needed=self.dependencies(name,definitions)
        return '''options gen2
options persistent_heap
options gc
require strudel/strudel
require daslib/jobque_boost
struct DjnbPacket { haps : array<Hap> }
var djnb_patterns : array<Pattern>
var djnb_queries : int = 0
def djnb_scale(var pat : Pattern; level : float) : Pattern {
    return @capture(<- pat, = level) (span : TimeSpan) : array<Hap> {
        var haps <- pat(span)
        for (h in haps) { h.value.gain *= level }
        return <- haps
    }
}
def strudel_add_track(var pat : Pattern; level : float = 1.0) : int {
    djnb_patterns |> push(djnb_scale(pat, level))
    return length(djnb_patterns)-1
}
'''+'\n\n'.join(definitions[n] for n in definitions if n in needed)+f'''
[export]
def djnb_build() {{ {name}() }}
[export]
def djnb_query(a,b : double; ch : Channel?) {{
    djnb_queries ++
    if (djnb_queries % 64 == 0) {{ unsafe(heap_collect(false, false)) }}
    var packet : DjnbPacket
    for (pat in djnb_patterns) {{
        var haps <- pat(TimeSpan(start=a, stop=b))
        for (h in haps) {{ packet.haps |> push_clone(h) }}
    }}
    ch |> push_clone(packet)

}}
'''

    def wrappers(self, names):
        curl=shutil.which('curl.exe' if os.name=='nt' else 'curl')
        if not curl: raise RuntimeError('curl is required for notebook calls')
        argv=[curl.replace('\\','/'),'--silent','--show-error','--fail-with-body','--max-time','10',
              '-H','Content-Type: application/json','--data-binary']
        code='require daslib/fio\n'+f'''def strudel_nb_request(payload : string) : string {{
    var response : string
    let code = run_and_capture([{', '.join(map(das_string,argv))}, payload,
        "http://127.0.0.1:{self.port}/command"], response, 12.0)
    if (code != 0) {{ panic("strudel player unavailable: {{response}}") }}
    return response
}}
'''
        def payload(command,name=''):
            return das_string(json.dumps(dict(name=command,args=dict(token=self.token,name=name))))
        code+='def strudel_status() : string { return strudel_nb_request('+payload('djnb_state')+') }\n'
        code+='def strudel_stop(name : string) : string {\n'
        for name in names:
            code+='    if (name == '+das_string(name)+') { return strudel_nb_request('+payload('djnb_stop',name)+') }\n'
        code+='    return "unknown pattern"\n}\n'
        for name in names:
            code+='def '+name+'() : string { return strudel_nb_request('+payload('djnb_call',name)+') }\n'
        return code

    def execute(self, request, worker, publish, cell_label):
        options,body=request
        if options.get('stop'):
            self.close()
            publish('stdout','Shared strudel player stopped.\n')
            return {'status':'ok','result':None}
        added=self.functions(body)
        for name in added:
            if name in ('strudel_stop','strudel_status','strudel_add_track'):
                raise ValueError('Reserved name: '+name)
            if name in worker.state.get('variables',{}) or (name not in self.definitions and any(
                    key.split('(',1)[0]==name for key in worker.state.get('definitions',{}))):
                raise ValueError('Notebook name already exists: '+name)
        config={**self.options,**options}
        if self.process and config!=self.options:
            raise ValueError('Use %%strudel --stop before changing player options')
        exe=config.get('exe') or os.environ.get('DASLANG_LIVE') or shutil.which('daslang-live')
        if not exe or not Path(exe).is_file():
            raise ValueError('Set %%strudel --exe "path/to/daslang-live.exe" or DASLANG_LIVE')
        sf2=str(Path(config['sf2']).resolve()).replace('\\','/') if config.get('sf2') else ''
        if sf2 and not Path(sf2).is_file(): raise ValueError('SF2 file does not exist: '+sf2)
        candidate={**self.definitions,**added}
        changed={n for n in added if added[n]!=self.definitions.get(n)}
        starting=self.process is None
        affected=set(candidate) if starting else {n for n in candidate if self.dependencies(n,candidate)&changed}
        staged=[]
        try:
            if starting:
                publish('stdout','Starting one shared audio player...\n')
                self.start(exe,sf2,config.get('port'))
            elif self.process.poll() is not None:
                raise RuntimeError('Player exited. Use %%strudel --stop, then rerun definitions.\n'+self.tail())
            for name in sorted(affected):
                revision=uuid.uuid4().hex
                path=self.folder/(name+'-'+revision+'.das')
                writefile((path,self.child_source(name,candidate),False))
                publish('stdout','Compiling '+name+'; shared audio continues...\n')
                try:
                    result=self.request('djnb_compile',name=name,path=path.as_posix(),revision=revision)
                finally:
                    path.unlink(missing_ok=True)
                if not isinstance(result,int) or isinstance(result,bool):
                    raise RuntimeError(str(result))
                staged.append(result)
            # Wrappers always use the same permanent endpoint. Register before
            # commit; compile errors leave the live version and transport intact.
            reply=worker.execute(self.wrappers(candidate),on_output=publish,cell_label=cell_label)
            if reply['status']!='ok': raise RuntimeError(reply['error']['evalue'])
            if staged and self.request('djnb_commit',ids=staged) is not True:
                raise RuntimeError('Could not commit compiled versions')
        except BaseException:
            if starting:
                self.close()
            elif staged:
                self.request('djnb_discard',ids=staged)
            raise
        self.definitions=candidate
        self.options=config
        publish('stdout','Ready: '+', '.join(n+'()' for n in added)+
                '. Active changes switch on the shared cycle boundary; other tracks continue.\n')
        return reply

    def close(self):
        if self.process:
            try:
                req=urllib.request.Request(f'http://127.0.0.1:{self.port}/shutdown',data=b'')
                with urllib.request.urlopen(req,timeout=3): pass
                self.process.wait(timeout=5)
            except (OSError,ValueError,subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.wait(timeout=5)
        if self.log: self.log.close()
        self.process=self.log=None
        if self.folder: shutil.rmtree(self.folder,ignore_errors=True)
        self.folder=None
