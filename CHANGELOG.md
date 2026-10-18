# Changelog

## 0.12.0a1 — unreleased

- `%vars` and `%view` read bounded previews through native RTTI, with structure trees, arrays-of-struct tables and paginated ranges. Read-only tool replies preserve the parent checkpoint without resending it.

- Explicit rich display for tables, HTML/Markdown, PNG/JPEG/SVG and WAV.
- Standalone .das snapshot export, isolated assertion cells, SDK test runner and notebook-filtered LCOV coverage.
- Project-local daspkg commands and dynamic module registration.
- SDK LSP diagnostics, definition lookup and optional compiler-backed hover.
- Isolated DAP debugger with breakpoints, stack/scopes, evaluation and stepping; original notebook state is preserved.
- SDK pin updated to `4651e8f3f541b933dfac403874a348d777e733cf`; runtime bundle includes the CLI, SDK tools and daspkg XML dependency.

## 0.11.0a1 — unreleased

- `%time` / `%%time` report separate compilation, context setup, state restoration,
  module initialization and execution wall times. `%timeit` / `%%timeit` benchmark
  independent checkpoint copies without changing the original session.
- `%%profile` uses native daslang function and allocation instrumentation, shows
  call counts and inclusive/self time and byte statistics, and optionally exports
  Chrome/Perfetto traces. Added editor help, tests and a profiling notebook.

- `%expand`, `%%expand` and `%%ast` inspect compiler output after macro expansion
  and type inference, without running the fragment or changing session state.
  Includes overloads, generated structure fields, mapped diagnostics and notebook examples.

- `%%writefile [-a] PATH` saves a UTF-8 file directly from a notebook cell,
  without compiling the body or changing session state. Quoted paths, parent
  directories, atomic replacement and append mode are supported.
- Macro modules can be written in notebook cells, required by subsequent cells,
  and edited without restarting the kernel. Added a native AST-verified macro
  example and an end-to-end notebook demonstrating reload and error recovery.
- Completion, inspection and completeness checks recognize the file cell magic.

## 0.10.0a1 — experimental alpha

First public release. Windows x64 and AVX2 only; verified with Python 3.12,
JupyterLab 4.2.5, and daslang commit
`f916e0092ef38032dd81e8a55a231c3c4b4a5533`.

- Stateful variables, readonly `let`, plain structs, nested arrays and tables.
- Function overloads and redefinition; persistent `require` directives.
- Imported script-module globals, initialization once, finalize on reset and
  graceful idle shutdown; structural compatibility checks during restoration.
- Isolated worker, streaming stdout/stderr, Windows Interrupt, checkpoint
  recovery after interruption or a native crash, and mapped compiler diagnostics.
- Last-expression results, completion, inspection and completeness checks.
- Separate `jupyterlab-daslang` 0.1.0 frontend for notebook and `.das` highlighting.
- Native wheels paired with a runtime bundle, source distributions, CI builds,
  clean-environment installation checks, and retained third-party notices.

Checkpoints are held in memory and disappear on kernel restart. Pointers,
resources, closures, persistent generators, object identity and schema migration
are unsupported. External side effects are not rolled back. Finalization is not
guaranteed after hard termination. This is an alpha, not a production-ready kernel.
