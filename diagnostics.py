"""Translate compiler locations to notebook cell lines."""
import re


def map_diagnostics(message, line_map):
    lines = message.splitlines()
    output = []
    index = 0
    while index < len(lines):
        location = re.fullmatch(r"notebook\.das:(\d+):(\d+)\s*", lines[index])
        mapped = line_map.get(int(location[1])) if location else None
        if mapped:
            label, line, source = mapped
            output.append(f"{label}, line {line}")
            if index + 2 < len(lines) and re.fullmatch(r"\s*[\^~]+\s*", lines[index + 2]):
                output.append(source)
                index += 3
                continue
        else:
            output.append(lines[index])
        index += 1
    return "\n".join(output)


def runtime_error(message, filename, line, trace, line_map):
    output = [message.rstrip()]
    mapped = line_map.get(line) if filename == "notebook.das" else None
    if mapped:
        label, row, source = mapped
        output += [f"{label}, line {row}", source]
    elif filename:
        output.append(f"{filename}:{line}")
    def replace(match):
        location = line_map.get(int(match[1]))
        if location:
            return f"{location[0]}, line {location[1]}"
        return match[0]
    if trace.strip():
        mapped_trace = re.sub(r"notebook\.das:(\d+)(?::\d+)?", replace, trace)
        mapped_trace = re.sub(r"\(sp=\d+,sptr=0x[0-9a-fA-F]+\)", "", mapped_trace)
        mapped_trace = mapped_trace.replace("djnb_cell", "cell entry")
        output += ["daslang stack:", mapped_trace.rstrip()]
    return "\n".join(output)
