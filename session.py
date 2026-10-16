"""Experimental stateful daslang session, using the Windows SDK C ABI."""
from __future__ import annotations
import ctypes as C
import os
from pathlib import Path
if __package__:
    from .paths import native_path
    from .module_sources import ModuleSources, SOURCE_CALLBACK
else:
    from paths import native_path
    from module_sources import ModuleSources, SOURCE_CALLBACK
import re
import base64
import hashlib
import json
import time
if __package__:
    from .cell_parser import CellParser, CellSyntaxError, definition_name, definition_key
    from .diagnostics import map_diagnostics, runtime_error
    from .persistent_types import dependencies, validate_schemas
else:
    from cell_parser import CellParser, CellSyntaxError, definition_name, definition_key
    from diagnostics import map_diagnostics, runtime_error
    from persistent_types import dependencies, validate_schemas


class CompileError(Exception):
    pass


class ExecutionError(Exception):
    pass


class Array(C.Structure):
    _fields_ = [("data", C.c_void_p), ("size", C.c_uint64),
                ("capacity", C.c_uint64), ("magic", C.c_uint32),
                ("lock", C.c_uint32), ("flags", C.c_uint32), ("pad", C.c_uint32)]


class Vec(C.Structure):
    _fields_ = [("bytes", C.c_byte * 16)]


class Runtime:
    """One runtime per process; use on its creating thread."""
    def __init__(self, sdk):
        self.sdk = Path(sdk).resolve()
        binary_dir = next((path for path in (self.sdk / "bin/Release", self.sdk / "bin")
                           if (path / "libDaScriptDyn.dll").is_file()), None)
        if binary_dir is None:
            raise FileNotFoundError(f"No daslang DLL in {self.sdk}/bin[/Release]")
        self.dll_path = os.add_dll_directory(str(binary_dir))
        self.dll = C.CDLL(str(binary_dir / "libDaScriptDyn.dll"))
        signatures = {
            "initialize": (None, []), "shutdown": (None, []),
            "environment_get_bound": (C.c_void_p, []),
            "environment_set_bound": (None, [C.c_void_p]),
            "reuse_cache_guard_create": (C.c_void_p, []),
            "reuse_cache_guard_release": (None, [C.c_void_p]),
            "set_root": (None, [C.c_char_p]),
            "text_make_writer": (C.c_void_p, []), "text_release": (None, [C.c_void_p]),
            "modulegroup_make": (C.c_void_p, []), "modulegroup_release": (None, [C.c_void_p]),
            "fileaccess_make_default": (C.c_void_p, []), "fileaccess_release": (None, [C.c_void_p]),
            "fileaccess_introduce_file": (None, [C.c_void_p, C.c_char_p, C.c_char_p, C.c_int]),
            "program_compile": (C.c_void_p, [C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p]),
            "policies_make": (C.c_void_p, []), "policies_release": (None, [C.c_void_p]),
            "program_compile_policies": (C.c_void_p, [C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]),
            "program_release": (None, [C.c_void_p]), "program_err_count": (C.c_int, [C.c_void_p]),
            "program_get_error": (C.c_void_p, [C.c_void_p, C.c_int]),
            "error_report": (None, [C.c_void_p, C.c_void_p, C.c_int]),
            "program_context_stack_size": (C.c_int, [C.c_void_p]),
            "program_simulate": (C.c_int, [C.c_void_p, C.c_void_p, C.c_void_p]),
            "context_make": (C.c_void_p, [C.c_int]), "context_release": (None, [C.c_void_p]),
            "context_find_function": (C.c_void_p, [C.c_void_p, C.c_char_p]),
            "context_eval_with_catch_unaligned": (None, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_void_p]),
            "context_get_exception": (C.c_char_p, [C.c_void_p]),
            "context_find_variable": (C.c_int, [C.c_void_p, C.c_char_p]),
            "context_get_variable": (C.c_void_p, [C.c_void_p, C.c_int]),
            "context_get_variable_type": (C.c_void_p, [C.c_void_p, C.c_int]),
            "type_info_get_description": (C.c_char_p, [C.c_void_p]),
            "type_info_get_hash": (C.c_uint64, [C.c_void_p]),
            "array_resize_i64": (None, [C.c_void_p, C.c_void_p, C.c_uint64, C.c_uint32, C.c_int]),
        }
        for name, (result, args) in signatures.items():
            fn = getattr(self.dll, "das_" + name)
            fn.restype, fn.argtypes = result, args
            setattr(self, name, fn)
        bridge_path = os.environ.get("DAS_RUNTIME_BRIDGE", str(
            native_path("notebook_runtime.dll", "native")))
        self.bridge = C.CDLL(bridge_path)
        for name, result, args in (
                ("nb_context_make", C.c_void_p, [C.c_int]),
                ("nb_clear_trace", None, [C.c_void_p]),
                ("nb_get_trace", C.c_char_p, [C.c_void_p]),
                ("nb_exception_line", C.c_uint32, [C.c_void_p]),
                ("nb_exception_file", C.c_char_p, [C.c_void_p]),
                ("nb_runtime_hash", C.c_char_p, []),
                ("nb_init_stack_size", C.c_uint32, [C.c_void_p]),
                ("nb_prepare_program", None, [C.c_void_p, C.c_void_p]),
                ("nb_compile_policy", None, [C.c_void_p, C.c_int]),
                ("nb_native_modules", C.c_char_p, []),
                ("nb_fileaccess_make", C.c_void_p, [SOURCE_CALLBACK]),
                ("nb_module_metadata", C.c_char_p, [C.c_void_p]),
                ("nb_root_index", C.c_int, [C.c_void_p, C.c_char_p]),
                ("nb_module_value_error", C.c_char_p, [C.c_void_p]),
                ("nb_save_module_value", C.c_char_p, [C.c_void_p, C.c_int]),
                ("nb_module_bytes", C.c_void_p, [C.c_void_p]),
                ("nb_module_bytes_size", C.c_uint64, [C.c_void_p]),
                ("nb_load_module_value", C.c_char_p, [C.c_void_p, C.c_int, C.c_void_p, C.c_uint64]),
                ("nb_skip_module_init", None, [C.c_void_p, C.c_char_p]),
                ("nb_run_module_init", C.c_int, [C.c_void_p]),
                ("nb_run_module_shutdown", C.c_int, [C.c_void_p])):
            function = getattr(self.bridge, name)
            function.restype, function.argtypes = result, args
        for name, result, args in (
                ("nb_profile_start", None, [C.c_void_p]),
                ("nb_profile_stop", C.c_char_p, [C.c_void_p]),
                ("nb_inspection_policy", None, [C.c_void_p]),
                ("nb_inspect_program", C.c_char_p, [C.c_void_p, C.c_char_p, C.c_int])):
            function = getattr(self.bridge, name, None)
            if function is not None:
                function.restype, function.argtypes = result, args
        self.context_make = self.bridge.nb_context_make
        actual_hash = hashlib.sha256((binary_dir / "libDaScriptDyn_runtime.dll").read_bytes()).hexdigest()
        if actual_hash != self.bridge.nb_runtime_hash().decode():
            raise RuntimeError("Runtime DLL changed: rebuild native/build against the matching daslang headers and runtime")
        self.initialize()
        self.native_modules = set(json.loads(self.bridge.nb_native_modules()))
        self.environment = self.environment_get_bound()
        self.set_root(str(self.sdk).encode())

    def close(self):
        self.shutdown()
        self.dll_path.close()


class Program:
    def __init__(self, runtime, source, line_map=None, simulate=True, fresh_modules=False, inspection=False):
        self.r = runtime
        self.line_map = line_map or {}
        self.sources = ModuleSources(self.r.sdk)
        self.access = self.r.bridge.nb_fileaccess_make(self.sources.callback)
        self.group = self.r.modulegroup_make()
        self.writer = self.r.text_make_writer()
        self.program = self.context = None
        self.module_info = {"modules": [], "variables": []}
        self.timings = {}
        compile_start = time.perf_counter_ns()
        try:
            self.r.fileaccess_introduce_file(self.access, b"notebook.das", source.encode(), 1)
            policy = self.r.policies_make()
            try:
                self.r.bridge.nb_compile_policy(policy, int(fresh_modules))
                if inspection:
                    self.r.bridge.nb_inspection_policy(policy)
                self.program = self.r.program_compile_policies(b"notebook.das", self.access, self.writer, self.group, policy)
            finally:
                self.r.policies_release(policy)
            if not self.program:
                raise CompileError("Compiler returned no program")
            self.check_errors()
            if self.sources.error:
                raise CompileError("Module source preprocessing failed: " + self.sources.error)
            self.timings["compile_ns"] = time.perf_counter_ns() - compile_start
            if not simulate:
                return
            simulate_start = time.perf_counter_ns()
            self.context = self.r.context_make(max(self.r.program_context_stack_size(self.program),
                self.r.bridge.nb_init_stack_size(self.program)))
            self.r.bridge.nb_prepare_program(self.program, self.context)
            if not self.r.program_simulate(self.program, self.context, self.writer):
                self.check_errors()
                raise CompileError("Simulation failed")
            self.timings["simulate_ns"] = time.perf_counter_ns() - simulate_start
            self.module_info = json.loads(self.r.bridge.nb_module_metadata(self.context))
            error = self.r.bridge.nb_module_value_error(self.context)
            if error:
                raise CompileError(error.decode())
        except BaseException:
            self.close()
            raise

    def check_errors(self):
        messages = []
        for i in range(self.r.program_err_count(self.program)):
            buf = C.create_string_buffer(16384)
            self.r.error_report(self.r.program_get_error(self.program, i), buf, len(buf))
            messages.append(buf.value.decode(errors="replace"))
        if messages:
            raise CompileError(map_diagnostics("\n".join(messages), self.line_map))

    def call(self, name):
        fn = self.r.context_find_function(self.context, name.encode())
        if not fn:
            raise ExecutionError(f"Missing exported function: {name}")
        result = Vec()
        self.r.bridge.nb_clear_trace(self.context)
        self.r.context_eval_with_catch_unaligned(self.context, fn, None, 0, C.byref(result))
        error = self.r.context_get_exception(self.context)
        if error:
            raise ExecutionError(self.runtime_error(error.decode(errors="replace")))
        return result

    def runtime_error(self, message):
        bridge = self.r.bridge
        return runtime_error(message, bridge.nb_exception_file(self.context).decode(errors="replace"),
                             bridge.nb_exception_line(self.context),
                             bridge.nb_get_trace(self.context).decode(errors="replace"), self.line_map)

    def variable(self, name):
        idx = self.r.bridge.nb_root_index(self.context, name.encode())
        if idx < 0:
            raise ExecutionError(f"Missing global: {name}")
        return self.r.context_get_variable(self.context, idx)

    def variable_type(self, name):
        idx = self.r.bridge.nb_root_index(self.context, name.encode())
        if idx < 0:
            raise ExecutionError(f"Missing type information for {name}")
        info = self.r.context_get_variable_type(self.context, idx)
        if not info:
            raise ExecutionError(f"No RTTI for {name}")
        description = self.r.type_info_get_description(info).decode()
        return re.sub(r"\s+", "", re.sub(r"\s+const$", "", description)).replace(",", ";")

    def variable_type_hash(self, name):
        index = self.r.bridge.nb_root_index(self.context, name.encode())
        info = self.r.context_get_variable_type(self.context, index)
        if not info:
            raise ExecutionError(f"No type hash for {name}")
        return self.r.type_info_get_hash(info)

    def save(self, name):
        self.call("djnb_save_" + name)
        blob = Array.from_address(self.variable("djnb_blob"))
        return C.string_at(blob.data, blob.size)

    def load(self, name, data):
        address = self.variable("djnb_blob")
        self.r.array_resize_i64(self.context, address, len(data), 1, 0)
        error = self.r.context_get_exception(self.context)
        if error:
            raise ExecutionError(error.decode(errors="replace"))
        blob = Array.from_address(address)
        if data:
            C.memmove(blob.data, data, len(data))
        self.call("djnb_load_" + name)

    @staticmethod
    def module_key(variable):
        return json.dumps([variable["module"], variable["name"]], separators=(",", ":"), ensure_ascii=False)

    def save_module(self, variable):
        error = self.r.bridge.nb_save_module_value(self.context, variable["index"])
        if error:
            raise ExecutionError(error.decode())
        return C.string_at(self.r.bridge.nb_module_bytes(self.context),
                           self.r.bridge.nb_module_bytes_size(self.context))

    def load_module(self, variable, data):
        buffer = C.create_string_buffer(data)
        error = self.r.bridge.nb_load_module_value(self.context, variable["index"], buffer, len(data))
        if error:
            raise ExecutionError(error.decode())

    def initialize(self, initialized_modules):
        for module in initialized_modules:
            self.r.bridge.nb_skip_module_init(self.context, module.encode())
        self.r.bridge.nb_clear_trace(self.context)
        if not self.r.bridge.nb_run_module_init(self.context):
            message = self.r.context_get_exception(self.context)
            raise ExecutionError(self.runtime_error(message.decode() if message else "module initialization failed"))

    def shutdown(self):
        self.r.bridge.nb_clear_trace(self.context)
        if not self.r.bridge.nb_run_module_shutdown(self.context):
            message = self.r.context_get_exception(self.context)
            raise ExecutionError(self.runtime_error(message.decode() if message else "module shutdown failed"))

    def close(self):
        if self.context:
            self.r.context_release(self.context)
            self.context = None
        if self.program:
            self.r.program_release(self.program)
            self.program = None
        for attr, release in [("group", self.r.modulegroup_release),
                              ("access", self.r.fileaccess_release),
                              ("writer", self.r.text_release)]:
            handle = getattr(self, attr, None)
            if handle:
                release(handle)
                setattr(self, attr, None)


class NotebookSession:
    """Mixed declaration, definition and statement cells.

    Rebuilds code, restores serialized values, executes only the new cell.
    Runtime errors commit declarations and partial mutations, like a notebook.
    """
    def __init__(self, runtime):
        self.runtime = runtime
        self.variables = {}
        self.definitions = {}
        self.current = None
        self.last_source = ""
        self.parser = CellParser()
        self.definition_origins = {}
        self.cell_counter = 0
        self.last_line_map = {}
        self.schemas = {}
        self.type_hashes = {}
        self.constants = set()
        self.requires = {}
        self.require_origins = {}
        self.module_values = {}
        self.initialized_modules = set()

    def inspect_compilation(self, mode, code, cell_label=None):
        """Compile an isolated fragment against session types, without simulating it."""
        if not hasattr(self.runtime.bridge, "nb_inspect_program"):
            raise RuntimeError("Rebuild the native adapter to enable %expand and %%ast")
        named = (mode == "expression" and re.fullmatch(r"[A-Za-z_]\w*", code)
                 and code not in self.variables
                 and any(definition_name(key) == code for key in self.definitions))
        cell = self.parser.split("" if named or mode == "expression" else code,
                                 persist_declarations=False, return_last=True)
        definitions = {**self.definitions, **cell.definitions}
        selection = ({code} if named else {"djnb_inspection"})
        selection.update(definition_name(key) for key in cell.definitions)
        if not named:
            selection.add("*")
            selection.update("-" + definition_name(key) for key in self.definitions
                             if key not in cell.definitions)
        requires = {**self.requires, **cell.requires}
        lines, mapping = [], {}
        label = cell_label or "inspection"

        def append(text, origin=None, first=1):
            for offset, line in enumerate(text.split("\n")):
                lines.append(line)
                if origin:
                    old_lines = origin["code"].split("\n")
                    row = first + offset
                    excerpt = old_lines[row - 1] if 0 < row <= len(old_lines) else line
                    mapping[len(lines)] = (origin["label"], row, excerpt)

        append("options gen2\noptions rtti\noptions persistent_heap\noptions gc\noptions disable_auto_inline\noptions disable_inline\noptions no_fast_call")
        for key, requirement in requires.items():
            where = self.require_origins.get(key) if key not in cell.requires else None
            if key in cell.requires:
                where = {"code": "\n" + code, "label": label, "line": cell.require_lines[key] + 1}
            append(requirement.strip().rstrip(";") + ";", where, where["line"] if where else 1)
        for key, definition in definitions.items():
            where = self.definition_origins.get(key) if key not in cell.definitions else None
            if key in cell.definitions:
                where = {"code": "\n" + code, "label": label, "line": cell.definition_lines[key] + 1}
            # Keep an explicitly requested, otherwise unused function in the output.
            if named and definition_name(key) == code and re.search(r"\bdef\s", definition):
                if not re.search(r"\[[^\]]*\bexport\b", definition):
                    append("[export]")
            append(definition, where, where["line"] if where else 1)
        for name, typ in self.variables.items():
            append(f"{'let' if name in self.constants else 'var'} {name} : {typ}")
        append("[export]\ndef djnb_inspection() : auto {")
        if mode == "expression" and not named:
            append("return (" + code + ")", {"code": code, "label": label})
        elif not named:
            # The parser leaves local declarations intact in inspection mode.
            start = len(lines)
            append(cell.statements)
            original_lines = code.split("\n")
            for offset, _ in enumerate(cell.statements.split("\n")):
                row = cell.statement_lines[offset]
                excerpt = original_lines[row - 1] if 0 < row <= len(original_lines) else ""
                mapping[start + offset + 1] = (label, row + 1, excerpt)
        append("}")
        program = Program(self.runtime, "\n".join(lines), mapping, simulate=False,
                          fresh_modules=True, inspection=True)
        try:
            raw = self.runtime.bridge.nb_inspect_program(
                program.program, "\n".join(sorted(selection)).encode(), int(mode == "ast"))
            data = json.loads(raw)
        finally:
            program.close()
        output = ["// After type inference and macro expansion; optimizer disabled."]
        for item in data["functions"]:
            name = "<cell>" if item["name"] == "djnb_inspection" else item["name"]
            if mode == "ast":
                output.append("Function " + name)
                for node in item.get("nodes", []):
                    details = (" : " + node["type"]) if node["type"] else ""
                    if "value" in node:
                        details += "  " + node["value"].replace("\n", " ")
                    output.append("  " * min(node["depth"] + 1, 80) + node["kind"] + details)
                if item.get("truncated"):
                    output.append("  ... truncated after 10000 nodes")
            else:
                output.append(item["source"].replace("djnb_inspection", "inspected_cell"))
        for item in data.get("structures", []):
            if mode == "ast":
                output.append("Structure " + item["name"])
                output.extend("  Field " + field["name"] + " : " + field["type"] +
                              (" = " + field["initializer"] if "initializer" in field else "")
                              for field in item["fields"])
            else:
                output.append("struct " + item["name"] + " {\n" + "\n".join(
                    "    " + field["name"] + " : " + field["type"] +
                    (" = " + field["initializer"] if "initializer" in field else "")
                    for field in item["fields"]) + "\n}")
        return "\n\n".join(output) if mode != "ast" else "\n".join(output)

    @staticmethod
    def validate_name(name):
        if not re.fullmatch(r"[A-Za-z_]\w*", name) or name.startswith("djnb_"):
            raise ValueError("Invalid or reserved session name")

    def source(self, variables, definitions, code, inferred=(), origin=None,
               definition_origins=None, declaration_lines=None, inferred_lines=None, statement_lines=None,
               constants=None, requires=None, require_origins=None):
        lines = []
        self.last_line_map = {}
        def append(text, source_origin=None, first_line=1, row_numbers=None):
            original = source_origin["code"].split("\n") if source_origin else []
            for offset, line in enumerate(text.split("\n")):
                lines.append(line)
                row = row_numbers[offset] if row_numbers else first_line + offset
                if source_origin and 1 <= row <= len(original):
                    self.last_line_map[len(lines)] = (source_origin["label"], row, original[row - 1])
        append("\n".join(["options gen2", "options rtti", "options disable_auto_inline", "options disable_inline",
                 "options no_fast_call",
                 "options persistent_heap", "options gc",
                 "require daslib/archive;", "require daslib/rtti;"]))
        for key, requirement in (self.requires if requires is None else requires).items():
            where = (self.require_origins if require_origins is None else require_origins).get(key)
            append(re.sub(r"\s+", " ", requirement).strip().rstrip(";") + ";", where, where["line"] if where else 1)
        append('var djnb_blob : array<uint8>\nvar djnb_result : string = ""\nvar djnb_error : string = ""\nvar djnb_failed : bool = false\nvar djnb_has_result : bool = false')
        constants = self.constants if constants is None else constants
        append('[export]\ndef djnb_result_present() : bool { return djnb_has_result }')
        append('''def djnb_render(value : auto) : string {
return "{value}"
}''')
        for name, definition in definitions.items():
            where = (definition_origins or self.definition_origins).get(name)
            append(definition, where, where["line"] if where else 1)
        for index, (name, expression) in enumerate(inferred):
            append(f"var djnb_type_{index} : typedecl({expression}) -const -&", origin,
                   inferred_lines[index] if inferred_lines else 1)
            append(f"[export]\ndef djnb_probe_{index}() : string {{ return djnb_render(djnb_type_{index}) }}")
        for name, typ in variables.items():
            row = (declaration_lines or {}).get(name)
            target = f"unsafe(reinterpret<typedecl({name}) -const>({name}))" if name in constants else name
            if name in constants and typ.startswith("typedecl("):
                typ += " const"
            append(f"{'let' if name in constants else 'var'} {name} : {typ}", origin if row else None, row or 1)
            append(f"[export]\ndef djnb_save_{name}() {{ djnb_blob <- mem_archive_save({target}) }}")
            append(f"[export]\ndef djnb_load_{name}() {{ mem_archive_load(djnb_blob, {target}) }}")
        append("[export]\ndef djnb_cell() {\ntry {")
        append(code, origin, row_numbers=statement_lines)
        append("} recover { djnb_failed = true; djnb_error = string(this_context().last_exception) }\n}")
        return "\n".join(lines)

    def _run(self, variables, definitions, code, inferred=(), schemas=None, expected_hashes=None, constants=None, requires=None, require_origins=None,
             restored_values=None, restored_module_values=None, initialized_modules=None, **mapping):
        if not hasattr(self, "last_timings"):
            self.last_timings = {}
        constants = self.constants if constants is None else constants
        requires = self.requires if requires is None else requires
        require_origins = self.require_origins if require_origins is None else require_origins
        schemas = self.schemas if schemas is None else schemas
        validate_schemas(schemas)
        self.last_source = self.source(variables, definitions, code, inferred, constants=constants, requires=requires,
                                       require_origins=require_origins, **mapping)
        fresh = False
        for requirement in requires.values():
            match = re.match(r"require\s+(?:\?\S+\s+)?([^\s;]+)", requirement)
            name = match[1] if match else ""
            if name not in self.runtime.native_modules and not name.startswith(("daslib/", "daslib.")):
                fresh = True
        if self.current:
            sdk = str(self.runtime.sdk).replace("\\", "/").lower().rstrip("/") + "/"
            fresh |= any(not key.split("|", 1)[-1].lower().startswith(sdk)
                         for key in self.current.module_info["modules"])
        candidate = Program(self.runtime, self.last_source, self.last_line_map, fresh_modules=fresh)
        self.last_timings.update(candidate.timings)
        restore_start = time.perf_counter_ns()
        modules = set(self.initialized_modules if initialized_modules is None else initialized_modules)
        module_values = {key: dict(value) for key, value in (
            self.module_values if restored_module_values is None else restored_module_values).items()}
        try:
            if self.current:
                for variable in self.current.module_info["variables"]:
                    key = self.current.module_key(variable)
                    module_values[key] = {**variable, "data": base64.b64encode(self.current.save_module(variable)).decode("ascii")}
            resolved = {name: candidate.variable_type(name) for name in variables}
            hashes = {name: candidate.variable_type_hash(name) for name in variables}
            for name, typ in resolved.items():
                self.validate_type(typ, schemas)
                if name in self.variables and self.variables[name] != typ:
                    raise ValueError(f"Type change for {name} requires restarting the session")
                previous = (expected_hashes if expected_hashes is not None else self.type_hashes).get(name)
                if previous is not None and previous != hashes[name]:
                    raise ValueError(f"Schema changed for {name}; use %reset before changing its type layout")
            for index, (name, expression) in enumerate(inferred):
                inferred_type = candidate.variable_type(f"djnb_type_{index}")
                if inferred_type != resolved[name]:
                    raise ValueError(f"Type change for {name}: {resolved[name]} -> {inferred_type}; restart the session")
            for variable in candidate.module_info["variables"]:
                key = candidate.module_key(variable)
                if key in module_values and module_values[key]["hash"] != variable["hash"]:
                    raise ValueError(f"Imported global {variable['name']} changed type layout; use %reset")
            if self.current:
                for name in self.variables:
                    candidate.load(name, self.current.save(name))
            elif restored_values is not None:
                for name in variables:
                    candidate.load(name, base64.b64decode(restored_values[name], validate=True))
            for variable in candidate.module_info["variables"]:
                key = candidate.module_key(variable)
                if key in module_values:
                    candidate.load_module(variable, base64.b64decode(module_values[key]["data"], validate=True))
            self.last_timings["restore_ns"] = time.perf_counter_ns() - restore_start
            initialize_start = time.perf_counter_ns()
            candidate.initialize(modules)
            self.last_timings["initialize_ns"] = time.perf_counter_ns() - initialize_start
        except BaseException:
            candidate.close()
            raise
        old = self.current
        self.current, self.variables, self.definitions = candidate, resolved, definitions
        self.module_values = module_values
        self.initialized_modules = modules | set(candidate.module_info["modules"])
        self.schemas = dict(schemas)
        self.type_hashes = hashes
        self.constants = set(constants)
        self.requires = dict(requires)
        self.require_origins = dict(require_origins)
        if mapping.get("definition_origins") is not None:
            self.definition_origins = mapping["definition_origins"]
        if old:
            old.close()
        profiling = getattr(self, "profile_requested", False)
        if profiling:
            self.runtime.bridge.nb_profile_start(candidate.context)
        execute_start = time.perf_counter_ns()
        try:
            candidate.call("djnb_cell")
        finally:
            self.last_timings["execute_ns"] = time.perf_counter_ns() - execute_start
            if profiling:
                self.last_profile = json.loads(self.runtime.bridge.nb_profile_stop(candidate.context))
        error = C.c_char_p.from_address(candidate.variable("djnb_error")).value
        if C.c_bool.from_address(candidate.variable("djnb_failed")).value:
            raise ExecutionError(candidate.runtime_error(error.decode(errors="replace") if error else "daslang runtime exception"))

    def declare(self, name, typ, initializer=None, move=False):
        self.validate_name(name)
        typ = typ.strip()
        self.validate_type(typ, self.schemas)
        if name in self.variables and self.variables[name] != typ:
            raise ValueError(f"Type change for {name} requires restarting the session")
        variables = {**self.variables, name: typ}
        code = f"{name} {'<-' if move else '='} {initializer}" if initializer is not None else ""
        self._run(variables, dict(self.definitions), code)

    def define(self, key, source):
        self.validate_name(key)
        match = re.match(r"(?:\[.*?\]\s*)?def\s+(\w+)\s*\(", source.strip(), re.S)
        if not match or match.group(1) != key:
            raise ValueError("Only function definitions are supported by this prototype")
        cell = self.parser.split(source)
        if len(cell.definitions) != 1 or cell.schemas or cell.requires or cell.declarations:
            raise ValueError("Expected exactly one function definition")
        self._run(dict(self.variables), {**self.definitions, **cell.definitions}, "")

    @staticmethod
    def validate_type(typ, schemas=None):
        dependencies(typ, schemas or {})

    def execute(self, code, cell_label=None):
        self.last_timings = {}
        self.last_profile = None
        started = time.perf_counter_ns()
        try:
            return self._execute(code, cell_label)
        finally:
            self.last_timings["total_ns"] = time.perf_counter_ns() - started

    def _execute(self, code, cell_label=None):
        self.cell_counter += 1
        origin = {"label": cell_label or f"Cell {self.cell_counter}", "code": code}
        try:
            cell = self.parser.split(code, display_last=True)
        except CellSyntaxError as error:
            if re.match(r"\s*(?:\[[^\]]+\]\s*)*def\s+", code):
                self.diagnose_definition(code, origin)
                raise CellSyntaxError(f"{origin['label']}: The compiler accepts this definition, but the notebook parser does not support its syntax yet") from None
            raise CellSyntaxError(f"{origin['label']}: {error}") from None
        variables = dict(self.variables)
        constants = set(self.constants)
        requires = {**self.requires, **cell.requires}
        require_origins = dict(self.require_origins)
        for key in cell.requires:
            require_origins[key] = {**origin, "line": cell.require_lines[key]}
        definitions = dict(self.definitions)
        schemas = {**self.schemas, **cell.schemas}
        validate_schemas(schemas)
        changed = {name for name in cell.schemas if name in self.schemas and cell.schemas[name] != self.schemas[name]}
        for name, typ in self.variables.items():
            if dependencies(typ, self.schemas) & changed:
                raise ValueError(f"Schema used by {name} changed; use %reset before redefining it")
        inferred = []
        inferred_lines = []
        declaration_lines = {}
        definition_origins = dict(self.definition_origins)
        source = code.encode("utf-8")
        for name, source in cell.definitions.items():
            base = definition_name(name)
            self.validate_name(base)
            if name in self.definitions and ((name in self.schemas) != (name in cell.schemas)):
                raise ValueError(f"Cannot replace a struct with a function or vice versa: {name}")
            definitions[name] = source
            definition_origins[name] = {**origin, "line": cell.definition_lines[name]}
        source = code.encode("utf-8")
        for declaration in cell.declarations:
            name, typ = declaration.name, declaration.type
            self.validate_name(name)
            if name in variables and ((name in constants) != declaration.constant):
                raise ValueError(f"Binding kind changed for {name}; use %reset before switching let/var")
            if declaration.constant:
                constants.add(name)
            row = source[:declaration.initializer_start or declaration.start].count(b"\n") + 1
            declaration_lines[name] = row
            if typ:
                if declaration.constant:
                    typ = re.sub(r"\s+const$", "", typ.strip())
                typ = re.sub(r"\s+", "", typ)
                self.validate_type(typ, schemas)
                if (name in variables and variables[name] != typ
                        and not variables[name].startswith("typedecl(")):
                    raise ValueError(f"Type change for {name} requires restarting the session")
                variables[name] = typ
            elif declaration.initializer is not None:
                inferred.append((name, declaration.initializer))
                inferred_lines.append(row)
                if name not in variables:
                    variables[name] = f"typedecl({declaration.initializer}) -const -&"
            else:
                raise ValueError(f"Cannot infer {name} without an initializer")
        self._run(variables, definitions, cell.statements, inferred, origin=origin,
                  schemas=schemas, constants=constants, requires=requires, require_origins=require_origins,
                  definition_origins=definition_origins, declaration_lines=declaration_lines,
                  inferred_lines=inferred_lines, statement_lines=cell.statement_lines)
        if C.c_uint32.from_buffer(self.current.call("djnb_result_present")).value:
            result = C.c_char_p.from_address(self.current.variable("djnb_result")).value
            return result.decode() if result else ""
        return None

    def diagnose_definition(self, code, origin):
        """Use the compiler for rejected definition syntax, without simulation."""
        header = self.source(self.variables, self.definitions, "")
        mapping = dict(self.last_line_map)
        start = header.count("\n") + 2
        for offset, line in enumerate(code.split("\n")):
            mapping[start + offset] = (origin["label"], offset + 1, line)
        program = Program(self.runtime, header + "\n" + code, mapping, simulate=False)
        program.close()

    def evaluate(self, expression, cell_label=None):
        origin = {"label": cell_label or "Expression", "code": "%eval " + expression}
        self._run(dict(self.variables), dict(self.definitions), f"djnb_result = djnb_render({expression})", origin=origin)
        value = C.c_char_p.from_address(self.current.variable("djnb_result")).value
        return value.decode() if value else ""

    def snapshot(self):
        """A value checkpoint, without replayable execution history."""
        modules = {key: dict(value) for key, value in self.module_values.items()}
        if self.current:
            for variable in self.current.module_info["variables"]:
                modules[self.current.module_key(variable)] = {
                    **variable, "data": base64.b64encode(self.current.save_module(variable)).decode("ascii")}
        return {"module_values": modules, "initialized_modules": sorted(self.initialized_modules),
                "variables": dict(self.variables), "definitions": dict(self.definitions),
                "schemas": {name: [list(field) for field in fields] for name, fields in self.schemas.items()},
                "type_hashes": dict(self.type_hashes), "constants": sorted(self.constants),
                "requires": dict(self.requires), "require_origins": {key: dict(origin) for key, origin in self.require_origins.items()},
                "definition_origins": {key: dict(origin) for key, origin in self.definition_origins.items()}, "cell_counter": self.cell_counter,
                "values": {name: base64.b64encode(self.current.save(name)).decode("ascii")
                           for name in self.variables}}

    def restore(self, state):
        """Restore a checkpoint into an empty session without executing cells."""
        if self.current or self.variables or self.definitions:
            raise ValueError("Restore requires an empty session")
        variables, definitions = state["variables"], state["definitions"]
        schemas = state.get("schemas", {})
        validate_schemas(schemas)
        self.definition_origins = {}
        normalized = {}
        for old_key, source in definitions.items():
            function = next((n for n in self.parser.parse(source).children if n.kind == "function_declaration"), None)
            key = definition_key(function) if function else old_key
            normalized[key] = source
            if old_key in state.get("definition_origins", {}):
                self.definition_origins[key] = state["definition_origins"][old_key]
        definitions = normalized
        constants = set(state.get("constants", []))
        requires = dict(state.get("requires", {}))
        if not constants <= variables.keys():
            raise ValueError("Checkpoint has unknown let bindings")
        self.cell_counter = state.get("cell_counter", 0)
        if not variables and not definitions and not requires:
            return
        for name, typ in variables.items():
            self.validate_name(name)
            self.validate_type(typ, schemas)
        self._run(dict(variables), dict(definitions), "", schemas=schemas, expected_hashes=state.get("type_hashes", {}),
                  constants=constants, requires=requires, require_origins=state.get("require_origins", {}),
                  restored_values=state["values"], restored_module_values=state.get("module_values", {}),
                  initialized_modules=state.get("initialized_modules", []))

    def close(self):
        try:
            if self.current:
                self.current.shutdown()
        finally:
            if self.current:
                self.current.close()
                self.current = None
            self.variables.clear()
            self.definitions.clear()
            self.definition_origins.clear()
            self.schemas.clear()
            self.type_hashes.clear()
            self.constants.clear()
            self.requires.clear()
            self.require_origins.clear()
            self.module_values.clear()
            self.initialized_modules.clear()
