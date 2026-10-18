"""Owned SDK subprocesses; never execute user arguments through a shell."""
import ctypes as C
import os
from pathlib import Path
import subprocess

class ToolError(RuntimeError): pass

class ProcessJob:
    def __init__(self, process):
        self.handle=None
        if os.name!='nt': return
        from ctypes import wintypes as W
        class BASIC(C.Structure):
            _fields_=[('perProcess',C.c_int64),('perJob',C.c_int64),('flags',W.DWORD),('min',C.c_size_t),('max',C.c_size_t),('active',W.DWORD),('affinity',C.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]
        class IO(C.Structure):
            _fields_=[(name,C.c_uint64) for name in ['readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes']]
        class EXTENDED(C.Structure):
            _fields_=[('basic',BASIC),('io',IO),('processMemory',C.c_size_t),('jobMemory',C.c_size_t),('peakProcess',C.c_size_t),('peakJob',C.c_size_t)]
        self.api=C.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.restype=W.HANDLE
        self.api.CreateJobObjectW.argtypes=[C.c_void_p,W.LPCWSTR]
        self.api.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]
        self.api.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
        self.api.CloseHandle.argtypes=[W.HANDLE]
        self.handle=self.api.CreateJobObjectW(None,None)
        limits=EXTENDED();limits.basic.flags=0x2000
        if not self.handle or not self.api.SetInformationJobObject(self.handle,9,C.byref(limits),C.sizeof(limits)) or not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
            self.close();process.kill();process.wait()
            raise OSError('Cannot place SDK subprocess in a kill-on-close job')
    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle);self.handle=None

class SDKTools:
    def __init__(self,sdk):
        self.sdk=Path(sdk).resolve()
        self.exe=next((p for p in [self.sdk/'bin/Release/daslang.exe',self.sdk/'bin/daslang.exe'] if p.exists()),None)
        if not self.exe: raise FileNotFoundError('SDK does not contain daslang.exe')
    def start(self,args):
        process=subprocess.Popen([str(self.exe),*map(str,args)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        return process,ProcessJob(process)
    def run(self,args,timeout=120):
        process,job=self.start(args)
        try:
            try: output,_=process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                job.close();process.kill();process.wait()
                raise ToolError('SDK tool timed out after '+str(timeout)+' seconds') from None
            text=output.decode('utf-8',errors='replace')
            if process.returncode: raise ToolError('SDK tool exited '+str(process.returncode)+'\n'+text[-12000:])
            return text
        finally: job.close()
    def script(self,name,args=(),timeout=120):
        script=self.sdk/name
        if not script.is_file(): raise FileNotFoundError('SDK tool missing: '+str(script))
        return self.run(['-project_root',str(Path.cwd()),str(script),'--',*args],timeout)
