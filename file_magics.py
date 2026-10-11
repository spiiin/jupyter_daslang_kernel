"""File cell magics run in the kernel without compiling or changing checkpoints."""
from pathlib import Path
import os
import shlex
import tempfile

WRITEFILE_HELP = "%%writefile [-a] PATH\nWrite the remaining cell to a UTF-8 file; -a appends. Relative paths use the kernel working directory."


def parse_writefile(code):
    header, _, body = code.partition("\n")
    words = header.strip().split(None, 1)
    if not words or words[0] != "%%writefile":
        return None
    try:
        arguments = shlex.split(words[1] if len(words) > 1 else "", posix=False)
    except ValueError as error:
        raise ValueError(WRITEFILE_HELP) from error
    append = bool(arguments and arguments[0] == "-a")
    if append:
        arguments.pop(0)
    if len(arguments) != 1:
        raise ValueError(WRITEFILE_HELP)
    filename = arguments[0]
    if len(filename) >= 2 and filename[0] == filename[-1] and filename[0] in "\"'":
        filename = filename[1:-1]
    if not filename or filename.startswith("-"):
        raise ValueError(WRITEFILE_HELP)
    return Path(filename), body, append


def writefile(request):
    path, body, append = request
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = body.encode("utf-8")
    if append:
        with path.open("ab") as file:
            file.write(data)
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return ("Appended to" if append else "Wrote") + f" {path}\n"
