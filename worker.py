"""Runtime process. Stdout carries JSON replies; native output is captured."""
import json
import os
import sys
import threading
import ctypes as C
if __package__:
    from .native_output import capture_native_output, stream_native_output
    from .session import Runtime, NotebookSession
    from .inspection_magic import parse_inspection
else:
    from native_output import capture_native_output, stream_native_output
    from session import Runtime, NotebookSession
    from inspection_magic import parse_inspection


protocol = None
send_lock = threading.Lock()


def send(message):
    with send_lock:
        protocol.write(json.dumps(message, ensure_ascii=True) + "\n")
        protocol.flush()


def watch_parent():
    """Avoid orphaned runtimes when Jupyter force-kills the kernel process."""
    pid = int(os.environ["DAS_WORKER_PARENT_PID"])
    if os.name == "nt":
        api = C.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [C.c_uint32, C.c_bool, C.c_uint32]
        api.OpenProcess.restype = C.c_void_p
        api.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
        api.WaitForSingleObject.restype = C.c_uint32
        api.CloseHandle.argtypes = [C.c_void_p]
        api.CloseHandle.restype = C.c_bool
        handle = api.OpenProcess(0x00100000, False, pid)
        if not handle:
            os._exit(2)
        api.WaitForSingleObject(handle, 0xFFFFFFFF)
        api.CloseHandle(handle)
        os._exit(2)
    else:
        import time
        while os.getppid() == pid:
            time.sleep(0.5)
        os._exit(2)


def main():
    global protocol
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    threading.Thread(target=watch_parent, daemon=True).start()
    runtime = session = None
    try:
        for line in sys.stdin:
            request = json.loads(line)
            reply = {"id": request["id"], "status": "ok", "result": None}
            send({"id": request["id"], "event": "running"})
            def publish(name, text):
                send({"id": request["id"], "event": "stream", "name": name, "text": text})
            with stream_native_output(publish):
                try:
                    if runtime is None:
                        runtime = Runtime(os.environ["DAS_SDK"])
                        session = NotebookSession(runtime)
                    if request["op"] == "restore":
                        session.restore(request["state"])
                    elif request["op"] == "shutdown":
                        session.close()
                    elif request["op"] == "execute":
                        code = request["code"].strip()
                        inspection = parse_inspection(request["code"])
                        if inspection is not None:
                            reply["result"] = session.inspect_compilation(*inspection, cell_label=request.get("cell_label"))
                        elif code == "%reset":
                            session.close()
                        elif code.startswith("%eval "):
                            reply["result"] = session.evaluate(code[6:], cell_label=request.get("cell_label"))
                        elif code:
                            reply["result"] = session.execute(request["code"], cell_label=request.get("cell_label"))
                    else:
                        raise ValueError("Unknown worker operation")
                except Exception as error:
                    reply.update(status="error", error={"ename": type(error).__name__,
                                 "evalue": str(error), "traceback": str(error).splitlines()})
                try:
                    if session is not None:
                        reply["state"] = session.snapshot()
                except Exception as error:
                    reply.update(status="checkpoint_error", error={"ename": "CheckpointError",
                                 "evalue": str(error), "traceback": str(error).splitlines()})
            reply["output"] = {}
            send(reply)
            if request["op"] == "shutdown":
                break
    finally:
        with capture_native_output():
            if session is not None:
                session.close()
            if runtime is not None:
                runtime.close()


if __name__ == "__main__":
    main()
