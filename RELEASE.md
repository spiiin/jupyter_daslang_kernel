# Release procedure

Repository: https://github.com/spiiin/jupyter_daslang_kernel

Supported and locally verified: **Windows x64, AVX2, Python 3.12, JupyterLab 4.2.5**.
daslang is pinned to `f916e0092ef38032dd81e8a55a231c3c4b4a5533`.
Other Python/JupyterLab versions in dependency ranges are not yet certified.
Linux/macOS kernel execution is unsupported. JupyterLab frontend is platform independent.

## Install a release

Download the kernel wheel, frontend wheel, and `daslang-runtime-win_amd64.zip`
from **the same GitHub release**. Install the Microsoft Visual C++ 2015–2022 x64
Redistributable if absent. Extract the runtime ZIP to a permanent location:

```powershell
Expand-Archive .\daslang-runtime-win_amd64.zip -DestinationPath C:\tools\daslang-jupyter
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install ".\daslang_jupyter-0.10.0a1-py3-none-win_amd64.whl[lab]" .\jupyterlab_daslang-0.1.0-py3-none-any.whl "jupyterlab==4.2.5" "httpx==0.27.2"
.\.venv\Scripts\python -m daslang_jupyter check --sdk C:\tools\daslang-jupyter\daslang-runtime
.\.venv\Scripts\python -m daslang_jupyter install --sdk C:\tools\daslang-jupyter\daslang-runtime --sys-prefix
.\.venv\Scripts\python -m jupyterlab
```

Create a notebook using **daslang (stateful prototype)**. Run `var x = 40`, then
`x += 2` and `x` in subsequent cells; the result should be `42`. The source
repository contains demonstration notebooks and the module example.

This distribution does not require Visual Studio, CMake or Node on the target
machine. Runtime files must remain available at the path recorded in kernelspec.
Moving them requires reinstalling the kernelspec. Do not mix a wheel with a
runtime ZIP from another build, even at the same source commit: the adapter
verifies the exact runtime DLL hash. `manifest.json` records bundled file hashes.
The tested JupyterLab 4.2.5 environment pins httpx 0.27.2 because its extension
manager uses the older proxy API. Newer JupyterLab releases need separate testing.

## Build from source

```powershell
git clone https://github.com/spiiin/jupyter_daslang_kernel.git
git clone --recursive https://github.com/GaijinEntertainment/daScript.git daslang
git -C daslang checkout f916e0092ef38032dd81e8a55a231c3c4b4a5533
git -C daslang submodule update --init --recursive
cd jupyter_daslang_kernel
$env:DAS_SDK = (Resolve-Path ..\daslang).Path
python -m pip install -r requirements-build.txt
cmake -S $env:DAS_SDK -B "$env:DAS_SDK/build-jupyter" -G "Visual Studio 17 2022" -A x64 "-DDAS_MODULES_INCLUDE=" -DDAS_TOOLS_DISABLED=ON -DDAS_TESTS_DISABLED=ON -DDAS_TUTORIAL_DISABLED=ON -DDAS_AOT_EXAMPLES_DISABLED=ON -DDAS_FLEX_BISON_DISABLED=ON "-DCMAKE_PROJECT_INCLUDE=$((Get-Location).Path.Replace('\','/'))/msvc-compat.cmake"
cmake --build "$env:DAS_SDK/build-jupyter" --config Release --target libDaScriptDyn --parallel 4 -- /nodeReuse:false
python build_package.py --sdk $env:DAS_SDK
python -m build --sdist --no-isolation
python build_runtime_bundle.py --sdk $env:DAS_SDK
python -m unittest discover -v
cd labextension
npm ci
npm test
npm run build
python -m build --no-isolation
```

The runtime bundle script refuses another commit or tracked runtime modifications.
Sources and npm dependencies are pinned; native output is not promised to be
bit-for-bit reproducible across MSVC versions. Each build pairs its own runtime
and adapter and validates that pair.

## CI and publication

`build.yml` runs on pushes, pull requests, and manually. It builds the pinned
runtime on Windows, tests the session/protocol, builds both wheels and source
archives, checks package metadata, tests clean wheel installation with the
extracted runtime, and uploads artifacts with SHA-256 checksums. The frontend
build uses `npm ci` and runs its six CodeMirror tests. Existing manual browser
checks cover notebook cells, `.das` files, and both themes on JupyterLab 4.2.5.
CI does not currently repeat those browser checks.

Only after CI passes, create a tag matching the kernel version (for example
`v0.10.0a1`). The tag workflow creates a **GitHub prerelease** containing both
wheels, both source archives, the paired runtime ZIP, and checksums. It requires
GitHub Actions to be enabled and the repository token to have release access.
Tag builds rerun all checks before publishing. A manual build does not publish.

PyPI/npm publishing is intentionally not configured: GitHub releases are the
initial distribution channel. A future PyPI upload needs account authorization
or a configured trusted publisher and must keep the paired runtime download clear.
