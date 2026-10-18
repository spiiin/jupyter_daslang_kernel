"""Standalone session snapshots and compile-only virtual source documents."""
import base64
from pathlib import Path
import tempfile

if __package__:
    from .cell_parser import definition_name
else:
    from cell_parser import definition_name


def analysis_source(session,code):
    cell=session.parser.split(code,persist_declarations=False,return_last=True)
    origin={'label':'cell','code':code}
    source=session.source(dict(session.variables),{**session.definitions,**cell.definitions},cell.statements,
        requires={**session.requires,**cell.requires},origin=origin,statement_lines=cell.statement_lines,
        definition_origins={**session.definition_origins,**{key:{**origin,'line':cell.definition_lines[key]} for key in cell.definitions}})
    source=source.replace('def djnb_cell() {\ntry {','def djnb_cell() {\n').replace('} recover { djnb_failed = true; djnb_error = string(this_context().last_exception) }\n}', '\n}')
    return source,dict(session.last_line_map)


def portable_source(session,code='',values=True):
    if any(definition_name(key)=='main' for key in session.definitions):
        raise ValueError('Rename the stored main function before standalone export')
    state=session.snapshot() if values else None
    if state and state.get('module_values'):
        raise ValueError('Portable export cannot restore imported mutable module globals; use --definitions-only or reset those imports')
    cell=session.parser.split(code,persist_declarations=False)
    definitions={**session.definitions,**cell.definitions}
    if any(definition_name(key)=='main' for key in definitions):
        raise ValueError('Export body must not define main; it is generated as the entry point')
    source=session.source(dict(session.variables),definitions,cell.statements,
                          requires={**session.requires,**cell.requires},origin={'label':'cell','code':code},
                          statement_lines=cell.statement_lines,constants=session.constants)
    mapping=dict(session.last_line_map)
    loads=[]
    for name,encoded in (state['values'].items() if state else []):
        loads.append('if (base64_decode(\"'+encoded+'\",djnb_blob)<0) {panic(\"Invalid snapshot\")}')
        loads.append('djnb_load_'+name+'()')
    source='require daslib/base64;\n'+source
    mapping={row+1:value for row,value in mapping.items()}
    source+='\n[export]\ndef main() {\n'+'\n'.join(loads)+'\ndjnb_cell()\nif (djnb_failed) { panic(djnb_error) }\n}\n'
    return source,mapping

class SourceFile:
    def __init__(self,source):
        # Keep relative require paths rooted in the notebook working directory.
        handle=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='.djnb-',suffix='.das',dir=Path.cwd(),delete=False)
        self.path=Path(handle.name)
        with handle: handle.write(source)
    def close(self): self.path.unlink(missing_ok=True)
    def __enter__(self): return self.path
    def __exit__(self,*args): self.close()
