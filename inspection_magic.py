"""Compiler inspection magic parsing shared by execution and editor services."""
HELP = {
    "%expand": "%expand EXPRESSION or %expand FUNCTION\nShow inferred code after macro expansion without running the expression. Compile-time macros still run.",
    "%%expand": "%%expand\nCODE\nShow inferred code for a cell without running it. Compile-time macros still run.",
    "%%ast": "%%ast\nCODE\nShow inferred expression AST nodes and types without running the cell. Compile-time macros still run.",
}


def parse_inspection(code):
    header, _, body = code.partition("\n")
    words = header.strip().split(None, 1)
    if not words or words[0] not in HELP:
        return None
    command = words[0]
    if command == "%expand":
        if len(words) < 2 or not words[1].strip() or body.strip():
            raise ValueError(HELP[command])
        return "expression", words[1].strip()
    if len(words) != 1 or not body.strip():
        raise ValueError(HELP[command])
    return ("ast" if command == "%%ast" else "code"), body
