# jupyterlab-daslang

Prebuilt syntax highlighting extension for JupyterLab 4.2–4.x and CodeMirror 6.
It registers `daslang`/`dascript`, `.das` files and `text/x-dascript` or
`text/x-daslang`. Notebook code cells pick the mode from kernel language metadata.
Colors follow the selected JupyterLab theme.

The mode highlights keywords, types, function/type declarations, fields, numeric
and character literals, strings, string interpolation, operators, comments and
the notebook magics `%eval`/`%reset`. Nested block comments and interpolation
expressions retain state across lines. It is a lexical highlighter; semantic
errors and unsupported kernel constructs remain the kernel/compiler's concern.
Custom reader bodies and annotation-specific coloring are not implemented.

Install the frontend wheel in the Python environment that runs the JupyterLab
server (which may differ from the kernel's environment):

```powershell
python -m pip install jupyterlab_daslang-0.1.0-py3-none-any.whl
python -m jupyter labextension list
```

Restart the JupyterLab server and reload its browser page. Open a notebook with
the daslang kernel or a `.das` file. No Node.js installation or JupyterLab rebuild
is needed when installing the prebuilt wheel.

Build from source (Node.js 20+ and a JupyterLab 4.2 build environment):

```powershell
npm ci
npm test
npm run build
python -m build --wheel --no-isolation
```

The token sets are derived from the matching upstream daslang grammar, commit
`f916e0092ef38032dd81e8a55a231c3c4b4a5533`; its BSD notice ships with the frontend.
Source dependencies are pinned in package-lock.json. The extension uses the
[JupyterLab language registry](https://jupyterlab.readthedocs.io/en/stable/api/interfaces/codemirror.IEditorLanguageRegistry.html)
and [CodeMirror stream parser](https://codemirror.net/docs/ref/#language.StreamParser).
Distribution follows the documented
[prebuilt extension mechanism](https://jupyterlab.readthedocs.io/en/4.2.x/extension/extension_dev.html#distributing-a-prebuilt-extension).
