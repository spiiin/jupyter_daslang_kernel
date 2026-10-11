"""Start and stop a private JupyterLab server from the clean release environment."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
token = secrets.token_hex(24)
base = f"http://127.0.0.1:{port}"


def get(path):
    request = urllib.request.Request(base + path,
                                     headers={"Authorization": "token " + token})
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.read()


with open("jupyterlab-test.log", "w", encoding="utf-8") as log:
    server = subprocess.Popen([sys.executable, "-m", "jupyterlab", "--no-browser",
                               "--ServerApp.ip=127.0.0.1", f"--ServerApp.port={port}",
                               "--ServerApp.port_retries=0", f"--IdentityProvider.token={token}"],
                              stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                metadata = json.loads(get("/api/kernelspecs"))
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("JupyterLab did not start; see jupyterlab-test.log")
                time.sleep(0.3)
        assert os.environ["DAS_TEST_KERNEL_NAME"] in metadata["kernelspecs"]
        assert b"jupyter" in get("/lab").lower()
        package = Path(sys.prefix) / "share/jupyter/labextensions/jupyterlab-daslang/package.json"
        entry = json.loads(package.read_text())["jupyterlab"]["_build"]["load"]
        assert len(get("/lab/extensions/jupyterlab-daslang/" + entry)) > 100
        print("JupyterLab server: notebook shell, installed kernelspec and frontend JavaScript served.")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
