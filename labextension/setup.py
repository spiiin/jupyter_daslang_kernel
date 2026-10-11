"""Install prebuilt frontend assets in Jupyter's extension search path."""
from pathlib import Path
import json
from setuptools import setup

root = Path("labextension")
if not (root / "package.json").is_file():
    raise RuntimeError("Build the frontend first: npm ci; npm run build")
metadata = json.loads((root / "package.json").read_text(encoding="utf-8"))
entry = metadata.get("jupyterlab", {}).get("_build", {}).get("load")
if not entry or not (root / entry.replace("\\", "/")).is_file():
    raise RuntimeError("Missing compiled extension entry point: run npm run build")
data_files = []
for folder in [root, *sorted(path for path in root.rglob("*") if path.is_dir())]:
    files = sorted(str(path) for path in folder.iterdir() if path.is_file())
    if files:
        target = Path("share/jupyter/labextensions/jupyterlab-daslang") / folder.relative_to(root)
        data_files.append((target.as_posix(), files))
setup(data_files=data_files)
