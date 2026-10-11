"""Create a relocatable runtime paired with this release's native wheel."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

RUNTIME_COMMIT = "f916e0092ef38032dd81e8a55a231c3c4b4a5533"
NOTICES = {
    "LICENSE": "LICENSE",
    "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
    "3rdparty/uriparser/COPYING": "licenses/URIPARSER.LICENSE",
    "include/dag_noise/LICENSE": "licenses/DAG_NOISE.LICENSE",
    "include/vecmath/LICENSE": "licenses/VEC_MATH.LICENSE",
    "3rdparty/fmt/LICENSE": "licenses/FMT.LICENSE",
    "include/fast_float/LICENSE": "licenses/FAST_FLOAT.LICENSE",
    "src/misc/LUAU.LICENSE": "licenses/LUAU.LICENSE",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    commit = subprocess.check_output(["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True).strip()
    if commit != RUNTIME_COMMIT:
        parser.error(f"Expected runtime commit {RUNTIME_COMMIT}, got {commit}")
    changes = subprocess.check_output(["git", "-C", str(sdk), "status", "--porcelain", "--untracked-files=no"], text=True)
    if changes.strip():
        parser.error("Runtime has tracked changes; use a clean pinned checkout")
    files = {sdk / source: target for source, target in NOTICES.items()}
    for name in ("libDaScriptDyn.dll", "libDaScriptDyn_runtime.dll"):
        files[sdk / "bin/Release" / name] = "bin/" + name
    for file in sorted((sdk / "daslib").rglob("*")):
        if file.is_file() and file.suffix == ".das":
            files[file] = file.relative_to(sdk).as_posix()
    manifest = {"commit": commit, "platform": "win_amd64", "cpu": "AVX2",
                "sha256": {target: hashlib.sha256(source.read_bytes()).hexdigest()
                           for source, target in files.items()}}
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / "daslang-runtime-win_amd64.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, target in files.items():
            archive.write(source, "daslang-runtime/" + target)
        archive.writestr("daslang-runtime/manifest.json", json.dumps(manifest, indent=2) + "\n")
        archive.writestr("daslang-runtime/README.txt",
                         "Use only with the kernel wheel from the SAME release.\n"
                         "Windows x64 / AVX2 / Microsoft Visual C++ 2015-2022 x64 Redistributable required.\n"
                         "Set DAS_SDK to this extracted daslang-runtime directory.\n"
                         "Optional native modules are disabled. See manifest.json and licenses/.\n")
    print(destination)


if __name__ == "__main__":
    main()
