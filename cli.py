"""Validate native components and register the installed kernel."""
import argparse
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from jupyter_client.kernelspec import KernelSpecManager

if __package__:
    from .paths import native_path
    from .worker_client import WorkerClient, WorkerCrashed
else:
    from paths import native_path
    from worker_client import WorkerClient, WorkerCrashed


def configuration(sdk, parser=None, bridge=None):
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise ValueError("This kernel requires Windows and 64-bit Python.")
    if not sdk:
        raise ValueError("Specify --sdk PATH or set DAS_SDK to the matching daslang tree.")
    root = Path(sdk).resolve()
    binary = next((root / folder for folder in ("bin/Release", "bin")
                   if (root / folder / "libDaScriptDyn.dll").is_file()), None)
    if binary is None:
        raise ValueError(f"Missing libDaScriptDyn.dll under {root}/bin[/Release]")
    required = [binary / "libDaScriptDyn_runtime.dll", root / "daslib"]
    grammar = Path(parser or os.environ.get("DAS_PARSER") or
                   native_path("notebook_parser.dll", "parser")).resolve()
    adapter = Path(bridge or os.environ.get("DAS_RUNTIME_BRIDGE") or
                   native_path("notebook_runtime.dll", "native")).resolve()
    for path in required + [grammar, adapter]:
        if not path.exists():
            raise ValueError(f"Missing component: {path}")
    return {"DAS_SDK": str(root), "DAS_PARSER": str(grammar),
            "DAS_RUNTIME_BRIDGE": str(adapter)}


def check_runtime(env):
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    client = WorkerClient()
    try:
        for code, expected in (("var check_value = 40", None),
                               ("check_value += 2\ncheck_value", "42")):
            reply = client.execute(code)
            if reply["status"] != "ok":
                raise ValueError(reply.get("error", {}).get("evalue", str(reply)))
            if reply.get("result") != expected:
                raise ValueError("State preservation check failed")
    finally:
        client.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def kernelspec(env):
    return {"argv": [sys.executable, "-m", "daslang_jupyter.kernel",
                     "-f", "{connection_file}"],
            "display_name": "daslang (stateful prototype)", "language": "daslang",
            "interrupt_mode": "message", "env": env}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "install"):
        sub = commands.add_parser(command)
        sub.add_argument("--sdk", default=os.environ.get("DAS_SDK"))
        sub.add_argument("--parser", help="Override bundled parser DLL")
        sub.add_argument("--bridge", help="Override bundled context adapter DLL")
        if command == "install":
            sub.add_argument("--name", default="daslang")
            location = sub.add_mutually_exclusive_group()
            location.add_argument("--sys-prefix", action="store_true")
            location.add_argument("--prefix", type=Path)
    args = parser.parse_args(argv)
    try:
        env = configuration(args.sdk, args.parser, args.bridge)
        check_runtime(env)
        print("OK: native components, runtime compatibility and persistent state (42).")
        if args.command == "install":
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, "kernel.json").write_text(
                    json.dumps(kernelspec(env), indent=2), encoding="utf-8")
                prefix = sys.prefix if args.sys_prefix else args.prefix
                installed = KernelSpecManager().install_kernel_spec(
                    directory, args.name, user=prefix is None,
                    prefix=str(prefix) if prefix else None)
                print(f"Installed kernel: {installed}")
    except (ValueError, OSError, RuntimeError, WorkerCrashed) as error:
        parser.exit(1, f"daslang-jupyter: {error}\n")


if __name__ == "__main__":
    main()
