"""Build matching native components and a platform-specific wheel."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True,
                        help="daslang source tree with Release runtime already built")
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sdk = args.sdk.resolve()
    if sys.platform != "win32":
        parser.error("Only Windows x64 is supported")
    if not (sdk / "bin/Release/libDaScriptDyn_runtime.dll").is_file():
        parser.error("Build libDaScriptDyn Release in the daslang tree first; see README")
    output = args.output.resolve()
    bundle = root / "_bin"
    bundle.mkdir(exist_ok=True)
    for component, name in (("parser", "notebook_parser"), ("native", "notebook_runtime")):
        source = root / component
        build = source / "build"
        subprocess.run(["cmake", "-S", str(source), "-B", str(build),
                        "-G", "Visual Studio 17 2022", "-A", "x64",
                        f"-DDAS_SOURCE={sdk.as_posix()}"], check=True)
        subprocess.run(["cmake", "--build", str(build), "--config", "Release",
                        "--parallel", "4", "--", "/nodeReuse:false"], check=True)
        shutil.copy2(build / "Release" / (name + ".dll"), bundle)
    shutil.copy2(sdk / "LICENSE", bundle / "daslang-LICENSE.txt")
    shutil.copy2(sdk / "modules/dasTreeSitter/3rdparty/tree-sitter/LICENSE",
                 bundle / "tree-sitter-LICENSE.txt")
    shutil.copy2(sdk / "tree-sitter-daslang/LICENSE", bundle / "grammar-LICENSE.txt")
    shutil.copy2(sdk / "modules/dasTreeSitter/3rdparty/tree-sitter/lib/src/unicode/LICENSE",
                 bundle / "unicode-LICENSE.txt")
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation",
                    "--outdir", str(output)], cwd=root, check=True)


if __name__ == "__main__":
    main()
