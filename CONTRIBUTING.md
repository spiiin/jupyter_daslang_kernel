# Contributing

The supported development environment is Windows x64, Python 3.12, Visual Studio
2022 C++ tools, CMake 3.20+, and an AVX2 CPU. Frontend builds use Node.js 22 and
JupyterLab 4.2.5. See `RELEASE.md` for the pinned runtime and build commands.

Run `python -m unittest discover -v` with `DAS_SDK` set to the runtime source tree
after building both native components. Run `npm ci`, `npm test` and `npm run build`
in `labextension`. CI also tests installed wheels outside the source checkout.

Include a minimal notebook cell or `.das` module when reporting a problem, plus
Python/JupyterLab versions, runtime commit, and complete error text. Avoid putting
private notebook data in reports. Changes to checkpoint formats, module lifecycle
or native ownership should include regression tests for failure and recovery.
