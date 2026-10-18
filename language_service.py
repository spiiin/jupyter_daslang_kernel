"""Compiler-backed notebook diagnostics and navigation using SDK LSP subtools."""
import json
import re
if __package__:
    from .portable import analysis_source,SourceFile
    from .sdk_tools import SDKTools
else:
    from portable import analysis_source,SourceFile
    from sdk_tools import SDKTools


def json_output(text):
    # Compiler/macro messages can precede the subtool's single JSON result.
    decoder=json.JSONDecoder()
    for pos in reversed([m.start() for m in re.finditer(r'(?m)^\{',text)]):
        try: return decoder.raw_decode(text[pos:])[0]
        except ValueError: pass
    raise ValueError('LSP tool returned no JSON: '+text[-2000:])


def language_query(session,op,code,cursor=0):
    source,mapping=analysis_source(session,code)
    line=code[:cursor].count('\n')+1
    col=cursor-(code.rfind('\n',0,cursor)+1)
    candidates=[row for row,loc in mapping.items() if loc[0]=='cell' and loc[1]==line]
    generated=source.splitlines()
    row=next((r for r in candidates if generated[r-1].strip() and not generated[r-1].strip() in ('return (',')')), candidates[0] if candidates else 1)
    original=code.split('\n')[line-1]
    # Preserve UTF-16 columns used by LSP (Jupyter cursor positions are codepoints).
    column=len(original[:col].encode('utf-16-le'))//2
    tools=SDKTools(session.runtime.sdk)
    with SourceFile(source) as path:
        if op=='check':
            result=json_output(tools.script('utils/lsp/subtools/validate.das',[str(path)]))
        else:
            result=json_output(tools.script('utils/lsp/subtools/nav.das',[op,str(path),str(row-1),str(column)]))
        def translate(location):
            pos=location.get('range',{}).get('start',{})
            mapped=mapping.get(pos.get('line',-1)+1)
            if mapped:
                location['cell']=mapped[0]
                location['line']=mapped[1]
            if location.get('path')==str(path): location['path']='<notebook>'
        for item in result.get('diagnostics',[]): translate(item)
        if 'diagnostics' in result:
            result['diagnostics']=[item for item in result['diagnostics'] if 'cell' in item or item.get('severity')==1]
        if result.get('contents'):result['contents']=result['contents'].replace(str(path).replace('\\','/'),'<notebook>').replace(str(path),'<notebook>')
        for item in result.get('locations',[]):
            if Path(item.get('path','')).resolve()==path.resolve(): translate(item);item['path']='<notebook>'
        return result

from pathlib import Path
