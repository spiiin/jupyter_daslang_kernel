"""Execute the shipped module notebook using the installed test kernelspec."""
import asyncio
import os
from pathlib import Path
import nbformat
from nbclient import NotebookClient

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
path = Path("module-state-demo.ipynb")
notebook = nbformat.read(path, as_version=4)
NotebookClient(notebook, kernel_name=os.environ["DAS_TEST_KERNEL_NAME"], timeout=60,
               resources={"metadata": {"path": str(Path.cwd())}}).execute()
nbformat.write(notebook, "module-state-demo.executed.ipynb")
print("Module-state notebook executed successfully.")
