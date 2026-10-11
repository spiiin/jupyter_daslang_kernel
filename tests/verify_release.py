"""Install release artifacts into a fresh venv and test outside the checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = args.work.resolve()
    if work.exists():
        parser.error("--work must be a new directory, to ensure a clean environment")
    work.mkdir(parents=True)
    with zipfile.ZipFile(args.artifacts / "daslang-runtime-win_amd64.zip") as archive:
        archive.extractall(work)
    sdk = work / "daslang-runtime"
    manifest = json.loads((sdk / "manifest.json").read_text())
    for filename, digest in manifest["sha256"].items():
        assert hashlib.sha256((sdk / filename).read_bytes()).hexdigest() == digest, filename
    venv.create(work / "venv", with_pip=True)
    python = work / "venv/Scripts/python.exe"
    env = {k: v for k, v in os.environ.items() if k not in
           ("PYTHONPATH", "DAS_PARSER", "DAS_RUNTIME_BRIDGE", "DAS_SDK", "JUPYTER_PATH")}
    env.update(DAS_SDK=str(sdk), DAS_TEST_KERNEL_NAME="daslang-release-test",
               PYTHONNOUSERSITE="1", JUPYTER_CONFIG_DIR=str(work / "jupyter-config"),
               JUPYTER_DATA_DIR=str(work / "jupyter-data"))
    env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")

    def run(*command):
        subprocess.run([str(python), *map(str, command)], cwd=work, env=env, check=True)

    kernels = list(args.artifacts.resolve().glob("daslang_jupyter-*.whl"))
    frontends = list(args.frontend.resolve().glob("jupyterlab_daslang-*.whl"))
    if len(kernels) != 1 or len(frontends) != 1:
        parser.error("Provide exactly one kernel wheel and one frontend wheel")
    kernel, frontend = kernels[0], frontends[0]
    run("-m", "pip", "install", kernel, frontend, "jupyterlab==4.2.5",
        "httpx==0.27.2", "nbclient==0.10.2", "nbformat==5.10.4",
        "ipykernel==6.29.5", "jupyter-client==8.6.3")
    run("-m", "pip", "check")
    run("-m", "daslang_jupyter", "check", "--sdk", sdk)
    run("-m", "daslang_jupyter", "install", "--sdk", sdk,
        "--sys-prefix", "--name", env["DAS_TEST_KERNEL_NAME"])
    shutil.copy2(root / "tests/verify_installed_package.py", work)
    run("verify_installed_package.py")
    run("-m", "jupyter", "labextension", "list")
    shutil.copy2(root / "tests/verify_server.py", work)
    run("verify_server.py")
    shutil.copy2(root / "module-state-demo.ipynb", work)
    shutil.copytree(root / "examples", work / "examples")
    shutil.copy2(root / "tests/execute_release_notebook.py", work)
    run("execute_release_notebook.py")
    print("Release verified: isolated venv, relocated runtime, installed kernel, frontend and module notebook.")


if __name__ == "__main__":
    main()
