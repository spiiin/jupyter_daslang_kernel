"""Parse notebook cells with the upstream daslang Tree-sitter grammar."""
from __future__ import annotations
import ctypes as C
from dataclasses import dataclass, field
import os
import re
from pathlib import Path
if __package__:
    from .paths import native_path
else:
    from paths import native_path


class CellSyntaxError(ValueError):
    pass


class TSNode(C.Structure):
    _fields_ = [("context", C.c_uint32 * 4), ("id", C.c_void_p), ("tree", C.c_void_p)]


@dataclass
class Node:
    kind: str
    start: int
    end: int
    text: str
    children: list[Node] = field(default_factory=list)
    fields: dict[str, list[Node]] = field(default_factory=dict)
    error: bool = False
    missing: bool = False

    def get(self, name):
        values = self.fields.get(name, [])
        return values[0] if values else None


@dataclass
class Declaration:
    name: str
    type: str | None
    initializer: str | None
    operator: str
    start: int
    end: int
    initializer_start: int | None = None
    constant: bool = False


@dataclass
class Cell:
    declarations: list[Declaration]
    definitions: dict[str, str]
    statements: str
    definition_lines: dict[str, int] = field(default_factory=dict)
    statement_lines: list[int] = field(default_factory=list)
    last_expression: str | None = None
    schemas: dict[str, list[list[str]]] = field(default_factory=dict)
    requires: dict[str, str] = field(default_factory=dict)
    require_lines: dict[str, int] = field(default_factory=dict)


def definition_name(key):
    return key.split("(", 1)[0]


def definition_key(node):
    """Declaration identity excludes parameter names, defaults and return type."""
    name = node.get("name").text
    parameters = []
    aliases = {}
    for argument in node.fields.get("arguments", []):
        typ = argument.get("type")
        text = typ.text if typ else "auto"
        def alias(match):
            value = match[1]
            aliases.setdefault(value, f"@{len(aliases)}")
            return f"auto({aliases[value]})"
        text = re.sub(r"auto\s*\(\s*(\w+)\s*\)", alias, text)
        mutable = bool(re.match(r"var\b", argument.text))
        if not mutable:
            text = re.sub(r"\s+const\s*$", "", text)
        text = re.sub(r"\s+", "", text).replace(",", ";")
        text = ("var " if mutable else "") + text
        parameters.extend([text] * max(1, len(argument.fields.get("name", []))))
    for alias_name, canonical in aliases.items():
        parameters = [re.sub(r"\b" + re.escape(alias_name) + r"\b", lambda _: canonical, value)
                      for value in parameters]
    return name + ("(" + ";".join(parameters) + ")" if parameters else "")


class CellParser:
    def __init__(self, dll=None):
        path = dll or os.environ.get("DAS_PARSER") or (
            native_path("notebook_parser.dll", "parser"))
        self.dll = C.CDLL(str(path))
        signatures = {
            "tree_sitter_daslang": (C.c_void_p, []),
            "ts_parser_new": (C.c_void_p, []),
            "ts_parser_delete": (None, [C.c_void_p]),
            "ts_parser_set_language": (C.c_bool, [C.c_void_p, C.c_void_p]),
            "ts_parser_parse_string": (C.c_void_p, [C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint32]),
            "ts_tree_delete": (None, [C.c_void_p]),
            "ts_tree_root_node": (TSNode, [C.c_void_p]),
            "ts_node_type": (C.c_char_p, [TSNode]),
            "ts_node_start_byte": (C.c_uint32, [TSNode]),
            "ts_node_end_byte": (C.c_uint32, [TSNode]),
            "ts_node_child_count": (C.c_uint32, [TSNode]),
            "ts_node_child": (TSNode, [TSNode, C.c_uint32]),
            "ts_node_field_name_for_child": (C.c_char_p, [TSNode, C.c_uint32]),
            "ts_node_is_named": (C.c_bool, [TSNode]),
            "ts_node_has_error": (C.c_bool, [TSNode]),
            "ts_node_is_missing": (C.c_bool, [TSNode]),
        }
        for name, (result, args) in signatures.items():
            fn = getattr(self.dll, name)
            fn.restype, fn.argtypes = result, args
            setattr(self, name, fn)

    def parse(self, text):
        source = text.encode("utf-8")
        parser = self.ts_parser_new()
        tree = None
        try:
            if not self.ts_parser_set_language(parser, self.tree_sitter_daslang()):
                raise CellSyntaxError("Incompatible Tree-sitter runtime and daslang grammar")
            tree = self.ts_parser_parse_string(parser, None, source, len(source))
            if not tree:
                raise CellSyntaxError("Parser returned no syntax tree")

            def copy(node):
                start, end = self.ts_node_start_byte(node), self.ts_node_end_byte(node)
                result = Node(self.ts_node_type(node).decode(), start, end,
                              source[start:end].decode(), error=(self.ts_node_has_error(node)
                                                               or self.ts_node_is_missing(node)),
                              missing=self.ts_node_is_missing(node))
                for index in range(self.ts_node_child_count(node)):
                    child = self.ts_node_child(node, index)
                    name = self.ts_node_field_name_for_child(node, index)
                    if name or self.ts_node_is_named(child) or self.ts_node_is_missing(child):
                        value = copy(child)
                        if self.ts_node_is_named(child) or self.ts_node_is_missing(child):
                            result.children.append(value)
                        if name:
                            result.fields.setdefault(name.decode(), []).append(value)
                return result

            return copy(self.ts_tree_root_node(tree))
        finally:
            if tree:
                self.ts_tree_delete(tree)
            self.ts_parser_delete(parser)

    def split(self, text, display_last=False, persist_declarations=True, return_last=False):
        source = text.encode("utf-8")
        definitions = {}
        schemas = {}
        definition_lines = {}
        requires, require_lines = {}, {}
        masked = bytearray(source)
        prefix = "[export]\ndef djnb_parse() {\n"
        shift = len(prefix.encode())
        file_tree = self.parse(text)
        block_tree = self.parse(prefix + text + "\n}\n")
        protected = []

        def protect(node, offset):
            if "comment" in node.kind or node.kind in ("string_builder", "constant_string"):
                protected.append((max(0, node.start - offset), min(len(source), node.end - offset)))
            else:
                for child in node.children:
                    protect(child, offset)

        protect(block_tree, shift)
        # CST string spans include interpolation, including nested quotes.
        neutral = bytearray(source)
        for start, end in protected:
            if start < end:
                neutral[start:end] = b" " * (end - start)
        depths = []
        depth = 0
        for byte in neutral:
            depths.append(depth)
            if byte in b"([{":
                depth += 1
            elif byte in b")]}":
                depth -= 1
        candidates = [(node, 0) for node in file_tree.children]
        candidates += [(node, shift) for node in block_tree.children]
        spans = set()
        # The file grammar recognizes definitions; the block grammar below
        # validates the executable remainder and determines statement scopes.
        for node, offset in candidates:
            start, end = node.start - offset, node.end - offset
            if (node.kind not in ("function_declaration", "struct_declaration", "require_declaration") or node.error
                    or (node.kind != "require_declaration" and not node.get("name"))
                    or start < 0 or end > len(source) or start >= len(depths) or depths[start] != 0):
                continue
            if (start, end) in spans:
                continue
            spans.add((start, end))
            if node.kind == "require_declaration":
                key = re.sub(r"\s+", " ", node.text).strip()
                requires[key] = node.text
                require_lines[key] = source[:start].count(b"\n") + 1
                masked[start:end] = bytes(10 if b == 10 else 32 for b in source[start:end])
                continue
            name = definition_key(node) if node.kind == "function_declaration" else node.get("name").text
            if node.kind == "struct_declaration" and persist_declarations:
                if (node.get("parent") or node.fields.get("methods") or node.fields.get("aliases")
                        or any(n.kind == "annotation_list" for n in node.children)
                        or re.match(r"struct\s+(?:template|sealed)\b", node.text)):
                    raise CellSyntaxError("Persistent structs currently support plain fields only")
                schema = []
                for member in node.fields.get("fields", []):
                    names = member.fields.get("name", [])
                    typ = member.get("type")
                    if (not typ or len(names) != 1 or member.get("init") or member.get("aka")
                            or member.fields.get("annotation")
                            or re.match(r"(?:static|override|sealed)\b", member.text)):
                        raise CellSyntaxError("Struct fields need explicit types and no defaults, annotations or aliases; initialize them in a cell")
                    schema.append([names[0].text, re.sub(r"\s+", "", typ.text).replace(",", ";")])
                schemas[name] = schema
            annotations = node.get("annotations")
            if persist_declarations and annotations and any(a.get("name") and a.get("name").text in ("init", "finalize")
                                   for a in annotations.children):
                raise CellSyntaxError("Lifecycle init/finalize annotations are not supported in notebook definitions")
            if name in definitions:
                raise CellSyntaxError("Duplicate definition/signature in the same cell")
            definitions[name] = node.text
            definition_lines[name] = source[:start].count(b"\n") + 1
            masked[start:end] = bytes(10 if b == 10 else 32 for b in source[start:end])
        wrapped = prefix + masked.decode("utf-8") + "\n}\n"
        tree = self.parse(wrapped)
        if tree.error:
            def first_error(node):
                if node.kind == "ERROR" or (node.error and not node.children):
                    return node
                for child in node.children:
                    if child.error:
                        return first_error(child)
                return node
            error = first_error(tree)
            offset = max(0, error.start - len(prefix.encode()))
            line = source[:offset].count(b"\n") + 1
            raise CellSyntaxError(f"Cell syntax error at line {line}: {error.text[:120]}")
        function = next(n for n in tree.children if n.kind == "function_declaration")
        body = function.get("body")
        declarations = []
        replacements = []
        for node in body.children:
            if not persist_declarations or node.kind != "let_expression":
                continue
            constant = bool(re.match(r"let\b", node.text))
            values = node.fields.get("variables", [])
            if len(values) != 1 or len(values[0].fields.get("name", [])) != 1:
                raise CellSyntaxError("Use one variable per declaration; several declarations per cell are supported")
            value = values[0]
            name_node = value.get("name")
            if (not name_node or name_node.kind != "identifier" or value.get("aka")
                    or value.fields.get("annotation") or re.match(r"(?:var|let)\s+inscope\b", node.text)):
                raise CellSyntaxError("Annotated, aliased and destructured persistent declarations are not supported yet")
            type_node, init_node = value.get("type"), value.get("init")
            before_init = wrapped.encode()[value.start:init_node.start] if init_node else b""
            if not type_node and re.search(rb"&\s*(?:=|<-|:=)\s*$", before_init):
                raise CellSyntaxError("Persistent reference bindings are not supported yet")
            operator = "<-" if before_init.rstrip().endswith(b"<-") else (
                ":=" if before_init.rstrip().endswith(b":=") else "=")
            start, end = node.start - len(prefix.encode()), node.end - len(prefix.encode())
            declaration = Declaration(name_node.text, type_node.text if type_node else None,
                                      init_node.text if init_node else None, operator, start, end,
                                      init_node.start - shift if init_node else None, constant)
            declarations.append(declaration)
            leading_lines = source[start:declaration.initializer_start].count(b"\n") if init_node else 0
            target = (f"unsafe(reinterpret<typedecl({declaration.name}) -const>({declaration.name}))"
                      if constant else declaration.name)
            assignment = (f"{target} {operator} " + "\n" * leading_lines + declaration.initializer
                          if init_node else "pass")
            original = source[start:end].decode()
            assignment += "\n" * max(0, original.count("\n") - assignment.count("\n"))
            row = source[:start].count(b"\n") + 1
            replacements.append((start, end, assignment.encode(), list(range(row, row + assignment.count("\n") + 1))))
        meaningful = [n for n in body.children if "comment" not in n.kind]
        last = meaningful[-1] if meaningful else None
        expression_kinds = {"variable", "call", "named_call", "invoke", "field", "safe_field", "at", "safe_at",
                            "parenthesized_expression", "binary_operation", "unary_operation", "ternary_operation",
                            "null_coalescing", "pipe", "string_builder", "make_array", "make_table", "make_tuple",
                            "make_struct", "make_variant", "array_comprehension", "cast_expression", "type_info",
                            "is_expression", "is_variant", "as_variant", "safe_as_variant", "interval"}
        expression = None
        if last and (display_last or return_last) and (last.kind in expression_kinds or last.kind.startswith("constant_")):
            start, end = last.start - shift, last.end - shift
            operator = last.get("operator")
            assignment = operator and operator.text in {"+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                                                        "&&=", "||=", "^^=", "<<=", ">>=", "<<<=", ">>>="}
            suppressed = source[end:].lstrip().startswith(b";") or any(stop > end for begin, stop in spans)
            if not assignment and (return_last or not suppressed):
                expression = last.text
                row = source[:start].count(b"\n") + 1
                expr_lines = [(line, row + index) for index, line in enumerate(expression.split("\n"))]
                final_row = expr_lines[-1][1]
                if return_last:
                    wrapper = [("return (", row)] + expr_lines + [(")", final_row)]
                else:
                    wrapper = [("static_if (typeinfo is_void(", row)] + expr_lines + [(")) {", final_row)] + expr_lines
                    wrapper += [("} else {", final_row), ("djnb_result = djnb_render(", row)] + expr_lines
                    wrapper += [(")", final_row), ("djnb_has_result = true", final_row), ("}", final_row)]
                replacements.append((start, end, "\n".join(line for line, _ in wrapper).encode(),
                                     [line for _, line in wrapper]))
        parts, line_numbers = [], [1]
        def append(part, rows):
            parts.append(part)
            line_numbers[-1] = rows[0]
            line_numbers.extend(rows[1:])
        position = 0
        for start, end, replacement, rows in sorted(replacements):
            segment = bytes(masked[position:start])
            row = source[:position].count(b"\n") + 1
            append(segment, list(range(row, row + segment.count(b"\n") + 1)))
            append(replacement, rows)
            position = end
        segment = bytes(masked[position:])
        row = source[:position].count(b"\n") + 1
        append(segment, list(range(row, row + segment.count(b"\n") + 1)))
        return Cell(declarations, definitions, b"".join(parts).decode(), definition_lines, line_numbers,
                    expression, schemas, requires, require_lines)
