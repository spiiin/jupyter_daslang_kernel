"""Jupyter adapter with an isolated, interruptible daslang runtime."""
from ipykernel.kernelbase import Kernel
from ipykernel.kernelapp import IPKernelApp
if __package__:
    from .worker_client import WorkerClient, WorkerInterrupted, WorkerCrashed
    from .editor_services import EditorServices
    from .file_magics import parse_writefile, writefile
    from .strudel_magic import StrudelMagic, parse_strudel
else:
    from worker_client import WorkerClient, WorkerInterrupted, WorkerCrashed
    from editor_services import EditorServices
    from file_magics import parse_writefile, writefile
    from strudel_magic import StrudelMagic, parse_strudel


class DaslangKernel(Kernel):
    implementation = "daslang-jupyter"
    implementation_version = "0.12.0a1"
    language = "daslang"
    language_version = "master"
    language_info = {"name": "daslang", "mimetype": "text/x-dascript",
                     "file_extension": ".das"}
    banner = "Stateful daslang: mixed cells, inferred types, Interrupt, %eval, %reset, %%writefile, %%strudel, %expand, %%ast, %time, %timeit, %%profile, display(), %export, %pkg, %%test, %%coverage, %%check, %%debug, %vars, %view."

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.worker_client = WorkerClient()
        self.strudel = StrudelMagic()
        self.lsp_enabled = False
        self.editor = EditorServices(lambda: self.worker_client.state)

    def do_complete(self, code, cursor_pos):
        return self.editor.complete(code, cursor_pos)

    def do_inspect(self, code, cursor_pos, detail_level=0, omit_sections=()):
        fallback = self.editor.inspect(code, cursor_pos, detail_level)
        if self.lsp_enabled and self.worker_client.active is None and not code.lstrip().startswith('%'):
            try:
                response=self.worker_client.execute(f"%%lsp hover {cursor_pos}\n{code}")
                language=(response.get("tool_metadata") or {}).get("language",{})
                if language.get("ok") and language.get("contents"):
                    return {"status":"ok","found":True,"data":{"text/markdown":language["contents"],"text/plain":language["contents"]},"metadata":{}}
            except Exception:
                pass  # Keep local metadata help if the optional SDK LSP tools fail.
        return fallback

    def do_is_complete(self, code):
        return self.editor.is_complete(code)

    def do_execute(self, code, silent, store_history=True, user_expressions=None,
                   allow_stdin=False, **kwargs):
        try:
            def publish(name, text):
                if not silent:
                    self.send_response(self.iopub_socket, "stream", {"name": name, "text": text})
            strudel_request = parse_strudel(code)
            file_request = parse_writefile(code)
            if strudel_request is not None:
                reply = self.strudel.execute(strudel_request, self.worker_client, publish,
                                             f"In[{self.execution_count}]")
            elif file_request is not None:
                publish("stdout", writefile(file_request))
                reply = {"status": "ok", "result": None}
            else:
                if code.strip() == "%reset":
                    self.strudel.close()
                    self.strudel = StrudelMagic()
                reply = self.worker_client.execute(code, on_output=publish,
                                                   cell_label=f"In[{self.execution_count}]")
        except (WorkerInterrupted, WorkerCrashed) as error:
            reply = {"status": "error", "output": {}, "error": {
                "ename": "KeyboardInterrupt" if isinstance(error, WorkerInterrupted) else "RuntimeProcessError",
                "evalue": str(error), "traceback": str(error).splitlines()}}
        except Exception as error:
            reply = {"status": "error", "output": {}, "error": {
                "ename": type(error).__name__, "evalue": str(error), "traceback": str(error).splitlines()}}
        metadata=reply.get("tool_metadata") or {}
        if "lsp_enabled" in metadata:
            self.lsp_enabled=metadata["lsp_enabled"]
        if not silent:
            for display in reply.get("display_data",[]):
                self.send_response(self.iopub_socket,"display_data",display)
            for stream, value in reply.get("output", {}).items():
                if value:
                    self.send_response(self.iopub_socket, "stream", {"name": stream, "text": value})
            if reply["status"] == "error":
                self.send_response(self.iopub_socket, "error", reply["error"])
            elif reply.get("result") is not None:
                self.send_response(self.iopub_socket, "execute_result", {
                    "execution_count": self.execution_count,
                    "data": {"text/plain": reply["result"]}, "metadata": {}})
        if reply["status"] == "error":
            return {"status": "error", "execution_count": self.execution_count, **reply["error"]}
        return {"status": "ok", "execution_count": self.execution_count,
                "payload": [], "user_expressions": {}}

    async def interrupt_request(self, stream, ident, parent):
        self.worker_client.interrupt()
        self.session.send(stream, "interrupt_reply", {"status": "ok"}, parent, ident=ident)

    def do_shutdown(self, restart):
        self.strudel.close()
        self.worker_client.close()
        return {"status": "ok", "restart": restart}


if __name__ == "__main__":
    IPKernelApp.launch_instance(kernel_class=DaslangKernel)
