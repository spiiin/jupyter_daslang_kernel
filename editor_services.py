"""Read-only notebook editor services. Never compile or execute user code."""
import re
if __package__:
    from .cell_parser import CellParser, CellSyntaxError, definition_key, definition_name
    from .persistent_types import SCALARS, parse_type
    from .file_magics import parse_writefile, WRITEFILE_HELP
    from .timing_magic import HELP as TIMING_HELP, parse_timing
    from .inspection_magic import HELP as INSPECTION_HELP, parse_inspection
    from .strudel_magic import parse_strudel, STRUDEL_HELP
else:
    from cell_parser import CellParser, CellSyntaxError, definition_key, definition_name
    from persistent_types import SCALARS, parse_type
    from file_magics import parse_writefile, WRITEFILE_HELP
    from timing_magic import HELP as TIMING_HELP, parse_timing
    from inspection_magic import HELP as INSPECTION_HELP, parse_inspection
    from strudel_magic import parse_strudel, STRUDEL_HELP


KEYWORDS = set("var let def struct if elif else for while return yield break continue pass true false null unsafe const typeinfo typedecl array table".split()) | SCALARS
BUILTINS = {
    "print": "print(text)\nWrites text to notebook stdout.",
    "length": "length(value)\nLength of an array, table or string.",
    "clear": "array |> clear()\nRemoves its elements.",
    "reserve": "array |> reserve(count)\nReserves capacity.",
    "push": "array |> push(value)\nAppends an element.",
    "push_clone": "array |> push_clone(value)\nAppends a cloned element, including containers and structs.",
    "next": "next(iterator, value)\nAdvances a mutable iterator and writes its next value.",
    "range": "range(end) or range(begin, end)\nCreates a range for iteration.",
    "panic": "panic(message)\nRaises a recoverable daslang exception.",
}
MAGICS = {"%%strudel": STRUDEL_HELP, "%%writefile": WRITEFILE_HELP, "%eval": "Evaluate an expression explicitly.", "%reset": "Discard all variables and definitions."}
MAGICS.update(INSPECTION_HELP)
MAGICS.update(TIMING_HELP)
PREFIX = "[export]\ndef djnb_editor() {\n"


def lexical_state(code):
    stack = []
    interpolations = []
    mode = "code"
    index = 0
    invalid = False
    while index < len(code):
        char, pair = code[index], code[index:index + 2]
        if mode == "line_comment":
            if char == "\n": mode = "code"
        elif mode == "block_comment":
            if pair == "*/": mode = "code"; index += 1
        elif mode == "string":
            if char == "\\": index += 1
            elif char == '"': mode = "code"
            elif char == "{":
                stack.append("{")
                interpolations.append(len(stack))
                mode = "code"
        elif pair == "//": mode = "line_comment"; index += 1
        elif pair == "/*": mode = "block_comment"; index += 1
        elif char == '"': mode = "string"
        elif char in "([{": stack.append(char)
        elif char in ")]}":
            depth = len(stack)
            if not stack or stack.pop() != {")": "(", "]": "[", "}": "{"}[char]:
                invalid = True
            elif char == "}" and interpolations and interpolations[-1] == depth:
                interpolations.pop()
                mode = "string"
        index += 1
    return mode, stack, invalid


class EditorServices:
    def __init__(self, state):
        self.state = state
        self.parser = CellParser()

    def symbols(self, code="", cursor=None):
        state = self.state()
        variables = dict(state.get("variables", {}))
        definitions = {}
        for key, source in state.get("definitions", {}).items():
            function = next((n for n in self.parser.parse(source).children if n.kind == "function_declaration"), None)
            definitions[definition_key(function) if function else key] = source
        schemas = dict(state.get("schemas", {}))
        if not code:
            return variables, definitions, schemas
        cursor = len(code) if cursor is None else cursor
        source = (PREFIX + code + "\n}\n").encode()
        point = len((PREFIX + code[:cursor]).encode())
        root = self.parser.parse(source.decode())

        def visit(node, visible=True):
            if node.kind == "block_expression":
                visible = visible and node.start <= point <= node.end
            if node.kind == "function_declaration":
                name = node.get("name")
                if name and name.text != "djnb_editor" and not node.error:
                    definitions[definition_key(node)] = node.text
                body = node.get("body")
                visible = visible and bool(body and body.start <= point <= body.end)
            if node.kind == "variable_declaration" and visible and node.start <= point:
                names = node.fields.get("name", [])
                typ = node.get("type")
                for name in names:
                    variables[name.text] = re.sub(r"\s+", "", typ.text) if typ else None
            if node.kind == "struct_declaration" and not node.error and node.get("name"):
                schemas[node.get("name").text] = [
                    [field.get("name").text, field.get("type").text]
                    for field in node.fields.get("fields", []) if field.get("name") and field.get("type")]
            for child in node.children:
                visit(child, visible)
        visit(root)
        # File parsing recovers top-level definitions that cannot occur in a block.
        for node in self.parser.parse(code).children:
            if node.kind == "function_declaration" and not node.error and node.get("name"):
                definitions[definition_key(node)] = node.text
            if node.kind == "struct_declaration" and not node.error and node.get("name"):
                schemas[node.get("name").text] = [[f.get("name").text, f.get("type").text]
                    for f in node.fields.get("fields", []) if f.get("name") and f.get("type")]
        return variables, definitions, schemas

    @staticmethod
    def resolve(chain, variables, schemas):
        match = re.match(r"[A-Za-z_]\w*", chain)
        if not match: return None
        typ = variables.get(match[0])
        position = match.end()
        try:
            while typ and position < len(chain):
                if chain[position] == ".":
                    field = re.match(r"\.([A-Za-z_]\w*)", chain[position:])
                    if not field: return None
                    name, args = parse_type(typ)
                    typ = dict(schemas.get(name, [])).get(field[1]) if not args else None
                    position += field.end()
                elif chain[position] == "[":
                    end = chain.find("]", position)
                    if end < 0: return None
                    name, args = parse_type(typ)
                    def render(node):
                        return node[0] + ("<" + ";".join(render(a) for a in node[1]) + ">" if node[1] else "")
                    typ = render(args[0] if name == "array" else args[1]) if name in ("array", "table") else None
                    position = end + 1
                else: return None
        except (ValueError, IndexError):
            return None
        return typ

    def complete(self, code, cursor):
        cursor = max(0, min(cursor, len(code)))
        prefix = code[:cursor]
        mode, _, _ = lexical_state(prefix)
        match = re.search(r"[A-Za-z_]\w*$", prefix)
        word = match[0] if match else ""
        start = cursor - len(word)
        reply = {"status": "ok", "matches": [], "cursor_start": start, "cursor_end": cursor, "metadata": {}}
        if mode != "code": return reply
        magic = re.search(r"%{1,2}[A-Za-z_]*$", prefix)
        if magic:
            reply.update(matches=[name for name in MAGICS if name.startswith(magic[0])], cursor_start=magic.start())
            return reply
        variables, definitions, schemas = self.symbols(code, cursor)
        field = re.search(r"([A-Za-z_]\w*(?:(?:\.[A-Za-z_]\w*)|(?:\[[^\]\n]*\]))*)\.([A-Za-z_]\w*)?$", prefix)
        if field:
            typ = self.resolve(field[1], variables, schemas)
            try:
                name, args = parse_type(typ) if typ else (None, [])
                candidates = [name for name, typ in schemas.get(name, [])] if not args else []
            except ValueError:
                candidates = []
        else:
            candidates = set(variables) | {definition_name(key) for key in definitions} | set(schemas) | KEYWORDS | set(BUILTINS)
            candidates |= {match[1] for requirement in self.state().get("requires", {}).values()
                           if (match := re.search(r"\bas\s+(\w+)", requirement))}
        reply["matches"] = sorted(name for name in candidates if name.startswith(word) and not name.startswith("djnb_"))
        return reply

    def inspect(self, code, cursor, detail_level=0):
        cursor = max(0, min(cursor, len(code)))
        reply = {"status": "ok", "found": False, "data": {}, "metadata": {}}
        if cursor <= len(code.partition("\n")[0]) and re.match(r"^\s*%%writefile\b", code):
            reply.update(found=True, data={"text/plain": WRITEFILE_HELP})
            return reply
        if cursor <= len(code.partition("\n")[0]):
            command = code.partition("\n")[0].strip().split(None, 1)
            if command and command[0] in (INSPECTION_HELP | TIMING_HELP):
                reply.update(found=True, data={"text/plain": (INSPECTION_HELP | TIMING_HELP)[command[0]]})
                return reply
        if lexical_state(code[:cursor])[0] != "code": return reply
        tokens = list(re.finditer(r"[A-Za-z_]\w*(?:(?:\.[A-Za-z_]\w*)|(?:\[[^\]\n]*\]))*", code))
        token = next((m[0] for m in tokens if m.start() <= cursor <= m.end()), None)
        if token is None:
            call = re.search(r"([A-Za-z_]\w*)\s*\(\s*$", code[:cursor])
            token = call[1] if call else None
        if not token: return reply
        variables, definitions, schemas = self.symbols(code, cursor)
        text = None
        typ = self.resolve(token, variables, schemas)
        if token in variables or typ:
            qualifier = "let " if token in self.state().get("constants", []) else ""
            text = f"{qualifier}{token} : {typ or 'type resolved when executed'}"
            try: name, args = parse_type(typ) if typ else (None, [])
            except ValueError: name, args = None, []
            if name in schemas and not args:
                text += "\nFields:\n" + "\n".join(f"  {field} : {typ}" for field, typ in schemas[name])
        elif token in schemas:
            text = f"struct {token}\n" + "\n".join(f"  {field} : {typ}" for field, typ in schemas[token])
        elif any(definition_name(key) == token for key in definitions):
            descriptions = []
            for key, definition in definitions.items():
                if definition_name(key) != token:
                    continue
                tree = self.parser.parse(definition)
                function = next((n for n in tree.children if n.kind == "function_declaration"), None)
                body = function.get("body") if function else None
                text = definition.encode()[:body.start].decode().strip() if body else definition
                saved = self.state()
                saved_key = next((k for k, value in saved.get("definitions", {}).items() if value == definition), None)
                origin = saved.get("definition_origins", {}).get(saved_key) if saved_key is not None else None
                if origin:
                    docs = []
                    for line in reversed(origin["code"].splitlines()[:origin["line"] - 1]):
                        if line.strip().startswith("//!"): docs.append(line.strip()[3:].strip())
                        else: break
                    if docs: text += "\n" + "\n".join(reversed(docs))
                    text += f"\nDefined in {origin['label']}, line {origin['line']}"
                if body:
                    docs = re.match(r"\{\s*((?://![^\n]*\n\s*)+)", body.text)
                    if docs: text += "\n" + "\n".join(line.strip()[3:].strip() for line in docs[1].splitlines() if line.strip().startswith("//!"))
                if detail_level: text += "\n\n" + definition
                descriptions.append(text)
            text = "\n\n".join(descriptions)
        elif token in BUILTINS:
            text = BUILTINS[token]
        if text:
            reply.update(found=True, data={"text/plain": text})
        return reply

    def is_complete(self, code):
        first = code.partition("\n")[0].strip()
        if first in TIMING_HELP and not code.partition("\n")[2].strip():
            return {"status": "incomplete", "indent": ""}
        try:
            timing = parse_timing(code)
        except ValueError:
            return {"status": "invalid"}
        if timing is not None:
            return self.is_complete(timing[2])
        header = code.partition("\n")[0].strip()
        if header in ("%expand", "%%ast", "%%expand") and not code.partition("\n")[2].strip():
            return {"status": "incomplete", "indent": ""}
        try:
            inspection = parse_inspection(code)
        except ValueError:
            return {"status": "invalid"}
        if inspection is not None:
            _, body = inspection
            # Check the fragment's delimiters, not the magic header.
            return self.is_complete(body)
        try:
            request = parse_strudel(code)
            if request is not None:
                options, body = request
                return {"status": "complete"} if options.get("stop") else self.is_complete(body)
        except ValueError:
            return {"status": "invalid"}
        try:
            if parse_writefile(code) is not None:
                return {"status": "complete"}
        except ValueError:
            return {"status": "invalid"}
        stripped = code.strip()
        if not stripped or stripped == "%reset": return {"status": "complete"}
        if stripped.startswith("%eval "): code = code[code.index("%eval") + 6:]
        if re.search(r"(?:^|\n)\s*require\s*$", code) or re.search(r"\brequire[^\n]*\bas\s*$", code):
            return {"status": "incomplete", "indent": ""}
        mode, stack, invalid = lexical_state(code)
        if invalid: return {"status": "invalid"}
        last = code.split("\n")[-1]
        base = len(last) - len(last.lstrip(" "))
        if stack or mode in ("string", "block_comment"):
            indent = max(base, stack.count("{") * 4 + (4 if any(c in "([" for c in stack) else 0))
            return {"status": "incomplete", "indent": " " * indent}
        tree = self.parser.parse(code)
        masked = bytearray(code.encode())
        def remove_comments(node):
            if "comment" in node.kind:
                masked[node.start:node.end] = bytes(10 if byte == 10 else 32 for byte in masked[node.start:node.end])
            else:
                for child in node.children: remove_comments(child)
        remove_comments(tree)
        active_code = masked.decode()
        if re.search(r"\b[A-Za-z_]\w*\.\s*$", active_code):
            return {"status": "incomplete", "indent": " " * base}
        if not tree.error or not self.parser.parse(PREFIX + code + "\n}\n").error:
            return {"status": "complete"}
        try:
            self.parser.split(code)
            return {"status": "complete"}
        except (CellSyntaxError, ValueError):
            pass
        generic = re.search(r"\b(?:array|table|iterator|generator|tuple|variant|smart_ptr|fixed_array)\s*<[^=()\[\]{}]*$", active_code)
        if generic and generic[0].count("<") > generic[0].count(">"):
            return {"status": "incomplete", "indent": " " * (base + 4)}
        if re.search(r"(?:[+*/%=,:.]|<-|=>|\|>|&&|\|\||\^\^|-|[<>])\s*$", active_code):
            return {"status": "incomplete", "indent": " " * base}
        if re.search(r"(?:def\s+\w+\([^\n]*\)(?:\s*:\s*\w+)?|(?:if|while|for)\s*\([^\n]*\)|else|recover|struct\s+\w+)\s*$", active_code):
            return {"status": "incomplete", "indent": " " * (base + 4)}
        return {"status": "invalid"}
