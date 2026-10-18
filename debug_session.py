"""Owned standalone snapshot debuggee using the SDK's TCP DAP server."""
from collections import deque
import json
import queue
import socket
import threading
import time
if __package__:
    from .portable import SourceFile
    from .sdk_tools import SDKTools
else:
    from portable import SourceFile
    from sdk_tools import SDKTools

class DebugSession:
    def __init__(self,sdk,source,mapping,break_lines):
        self.file=SourceFile(source);self.mapping=mapping
        self.process=self.job=self.socket=None
        self.events=queue.Queue();self.replies={};self.lock=threading.Lock();self.sequence=0
        self.output=deque(maxlen=200);self.thread_id=None
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            self.process,self.job=SDKTools(sdk).start(['--das-wait-debugger','-no-optimization','-project_root',str(self.file.path.parent),str(self.file.path),'--','--das-debug-port',str(port),'--das-stepping-debugger'])
            def drain():
                for line in iter(self.process.stdout.readline,b''):
                    self.output.append(line.decode('utf-8',errors='replace'))
            threading.Thread(target=drain,daemon=True).start()
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if self.process.poll() is not None: raise RuntimeError('Debuggee exited: '+''.join(self.output))
                try:
                    self.socket=socket.create_connection(('127.0.0.1',port),timeout=.2);break
                except OSError: time.sleep(.05)
            if self.socket is None: raise TimeoutError('Debugger did not listen within 30 seconds')
            self.socket.settimeout(None)
            threading.Thread(target=self.read,daemon=True).start()
            self.request('initialize',{'clientID':'jupyter-daslang','adapterID':'daslang','linesStartAt1':True,'columnsStartAt1':True,'pathFormat':'path','supportsVariableType':True})
            self.request('launch',{'cwd':str(self.file.path.parent)})
            mapped=[]
            for line in break_lines:
                match=next((row for row,where in mapping.items() if where[0]=='cell' and where[1]==line),None)
                if match is None: raise ValueError('No executable source for cell line '+str(line))
                mapped.append({'line':match})
            self.breakpoints=self.request('setBreakpoints',{'source':{'path':str(self.file.path)},'breakpoints':mapped})
            self.request('threads')
            self.request('configurationDone')
        except BaseException:
            self.close();raise
    def read(self):
        try:
            reader=self.socket.makefile('rb')
            while True:
                length=None
                while True:
                    line=reader.readline()
                    if not line: return
                    if line in (b'\r\n',b'\n'): break
                    key,_,value=line.partition(b':')
                    if key.lower()==b'content-length': length=int(value)
                if length is None or length>16*1024*1024: raise ValueError('Invalid DAP frame')
                payload=reader.read(length)
                if len(payload)!=length: return
                message=json.loads(payload)
                if message.get('type')=='response':
                    waiter=self.replies.get(message['request_seq'])
                    if waiter: waiter.put(message)
                elif message.get('type')=='event':
                    self.events.put(message)
        except (OSError,ValueError): pass
    def request(self,command,arguments=None,timeout=15):
        with self.lock:
            self.sequence+=1;seq=self.sequence
            waiter=queue.Queue();self.replies[seq]=waiter
            payload=json.dumps({'seq':seq,'type':'request','command':command,'arguments':arguments or {}}).encode()
            self.socket.sendall(('Content-Length: '+str(len(payload))+'\r\n\r\n').encode()+payload)
        try:
            response=waiter.get(timeout=timeout)
            if not response.get('success'): raise RuntimeError(response.get('message','DAP request failed: '+command))
            return response.get('body') or {}
        except queue.Empty: raise TimeoutError('DAP request timed out: '+command) from None
        finally: self.replies.pop(seq,None)
    def wait(self,timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try: event=self.events.get(timeout=.1)
            except queue.Empty:
                if self.process.poll() is not None: return {'event':'terminated','body':{'output':''.join(self.output)}}
                continue
            if event['event'] in ('stopped','terminated','exited'):
                if event['event']=='stopped': self.thread_id=event.get('body',{}).get('threadId')
                return event
        return {'event':'running','body':{}}
    def stack(self):
        result=self.request('stackTrace',{'threadId':self.thread_id or self.request('threads')['threads'][0]['id'],'startFrame':0,'levels':100})
        for frame in result.get('stackFrames',[]):
            if frame.get('source',{}).get('path')==str(self.file.path):
                where=self.mapping.get(frame['line'])
                if where: frame['cell']=where[0];frame['cellLine']=where[1]
        return result
    def close(self):
        if self.socket:
            try: self.socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.socket.close();self.socket=None
        if self.job: self.job.close();self.job=None
        if self.process:
            if self.process.poll() is None: self.process.kill()
            self.process.wait(timeout=5)
            self.process.stdout.close();self.process=None
        self.file.close()
