"""Keep direct module-global reads in initializers as runtime reads."""
import ctypes as C
import hashlib
from pathlib import Path

if __package__:
    from .cell_parser import CellParser
else:
    from cell_parser import CellParser


SOURCE_CALLBACK = C.CFUNCTYPE(C.c_void_p, C.c_char_p, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32))


class ModuleSources:
    def __init__(self, sdk):
        self.sdk = Path(sdk).resolve()
        self.parser = CellParser()
        self.buffers = []
        self.error = None
        self.callback = SOURCE_CALLBACK(self.transform)

    def transform(self, filename, address, length, output_length):
        try:
            path = Path(filename.decode()).resolve()
            if path.suffix.lower() != ".das" or path.is_relative_to(self.sdk):
                return None
            raw = C.string_at(address, length)
            text = raw.decode("utf-8-sig")
            tree = self.parser.parse(text)
            if tree.error:
                return None
            replacements = []
            helper = "djnb_read_global_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
            for node in tree.children:
                if node.kind != "global_let":
                    continue
                for declaration in node.fields.get("variables", []):
                    init = declaration.get("init")
                    if not init or init.kind != "variable":
                        continue
                    before = text.encode()[declaration.start:init.start].rstrip()
                    if before.endswith(b"<-") or before.endswith(b":=") or before.endswith(b"&="):
                        continue
                    replacements.append((init.start, init.end, f"{helper}({init.text})".encode()))
            if not replacements:
                return None
            # never_inline prevents inference from seeing a bare global reference
            # and substituting its source default before saved values are loaded.
            source = text.encode()
            for start, end, replacement in sorted(replacements, reverse=True):
                source = source[:start] + replacement + source[end:]
            source += f"\n[never_inline]\ndef private {helper}(value : auto) {{ return value }}\n".encode()
            buffer = C.create_string_buffer(source)
            self.buffers.append(buffer)
            output_length[0] = len(source)
            return C.addressof(buffer)
        except Exception as error:
            self.error = str(error)
            return None
