# Changelog

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
