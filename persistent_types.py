"""Supported value types and their schema dependencies."""
import re


SCALARS = {"bool", "int", "uint", "int8", "uint8", "int16", "uint16", "int64", "uint64", "float", "double", "string"}
TABLE_KEYS = SCALARS


def parse_type(text):
    normalized = re.sub(r"\s+", "", text).replace(",", ";")
    tokens = re.findall(r"[A-Za-z_]\w*|[<>;]", normalized)
    if "".join(tokens) != normalized:
        raise ValueError(f"Unsupported persistent type: {text}")
    index = 0
    def parse():
        nonlocal index
        if index == len(tokens) or not re.fullmatch(r"[A-Za-z_]\w*", tokens[index]):
            raise ValueError(f"Invalid persistent type: {text}")
        name = tokens[index]; index += 1
        arguments = []
        if index < len(tokens) and tokens[index] == "<":
            index += 1
            arguments.append(parse())
            while index < len(tokens) and tokens[index] == ";":
                index += 1
                arguments.append(parse())
            if index == len(tokens) or tokens[index] != ">":
                raise ValueError(f"Invalid persistent type: {text}")
            index += 1
        return name, arguments
    result = parse()
    if index != len(tokens):
        raise ValueError(f"Invalid persistent type: {text}")
    return result


def dependencies(text, schemas):
    visited = set()
    def visit(node, active):
        name, arguments = node
        if name in SCALARS and not arguments:
            return
        if name == "array" and len(arguments) == 1:
            visit(arguments[0], active); return
        if name == "table" and len(arguments) == 2:
            key, key_args = arguments[0]
            if key not in TABLE_KEYS or key_args:
                raise ValueError("Table keys must be scalar values")
            visit(arguments[1], active); return
        if name in schemas and not arguments:
            if name in active:
                raise ValueError(f"Recursive persistent schema is not supported yet: {name}")
            visited.add(name)
            for field, typ in schemas[name]:
                visit(parse_type(typ), active | {name})
            return
        raise ValueError(f"Unsupported persistent type: {name}; use scalars, arrays, tables or plain structs")
    visit(parse_type(text), set())
    return visited


def validate_schemas(schemas):
    for name in schemas:
        dependencies(name, schemas)
