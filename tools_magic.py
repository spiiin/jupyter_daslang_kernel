"""Notebook operations backed by real SDK tools and isolated session snapshots."""
import json
from pathlib import Path
import re
import shlex
import tempfile
if __package__:
    from .variables_view import variable_views
    from .coverage_report import notebook_coverage
    from .portable import portable_source,SourceFile
    from .sdk_tools import SDKTools
    from .language_service import language_query
    from .debug_session import DebugSession
else:
    from variables_view import variable_views
    from coverage_report import notebook_coverage
    from portable import portable_source,SourceFile
    from sdk_tools import SDKTools
    from language_service import language_query
    from debug_session import DebugSession

HELP={
 '%vars':'%vars [--start N] [--count N]\nList root notebook variables, types, binding kinds and bounded previews without executing code.',
 '%view':'%view NAME [--start N] [--count N] [--depth N]\nRead a structure, array range or table without evaluating an expression.',

 '%export':'%export [--definitions-only] PATH\nExport current definitions and portable root-global values as a standalone .das program.',
 '%%export':'%%export [--definitions-only] PATH\nCODE\nExport a session snapshot with CODE as its main body, without executing it.',
 '%pkg':'%pkg list|search|install|update|upgrade|build|check|doctor [ARGS]\nRun daspkg in the notebook project. Native updates can require a kernel restart.',
 '%modules':'%modules\nList registered native modules and project module folders.',
 '%%test':'%%test\nCODE\nRun assertions in a standalone copy of the current root-global snapshot.',
 '%test':'%test PATH [SDK TEST OPTIONS]\nRun the SDK dastest runner on a test file or directory.',
 '%%coverage':'%%coverage [--output PATH]\nCODE\nRun a standalone snapshot with SDK branch/line coverage; do not commit state.',
 '%%check':'%%check\nCODE\nCompiler-backed LSP diagnostics without executing the cell.',
 '%definition':'%definition EXPRESSION\nUse SDK LSP navigation to find a symbol definition.',
 '%lsp':'%lsp on|off\nEnable compiler-backed hover in Jupyter inspection; local completion remains available.',
 '%%debug':'%%debug [--break LINE[,LINE...]]\nCODE\nDebug an isolated snapshot via the SDK DAP server. Body starts on cell line 2.',
 '%debug':'%debug stack|scopes|variables REF|eval EXPR|next|in|out|continue|pause|wait|stop\nControl the current isolated DAP debug session.',
}

def words(text):
    return [s[1:-1] if len(s)>1 and s[0]==s[-1] and s[0] in ("'",'"') else s for s in shlex.split(text,posix=False)]

def parse_tools(code):
    header,_,body=code.partition('\n');parts=header.strip().split(None,1)
    if not parts or (parts[0] not in HELP and parts[0]!='%%lsp'):return None
    command=parts[0];args=parts[1] if len(parts)>1 else ''
    if command.startswith('%%') and not body.strip():raise ValueError(HELP.get(command,'A cell body is required'))
    if not command.startswith('%%') and body.strip():raise ValueError('Line magic must be on one line')
    return command,args,body

def run_tools(session,request,debug=None):
    command,args,body=request
    if command in ('%vars','%view'):
        tokens=words(args);name=None;options={'start':0,'count':50,'depth':4}
        if command=='%view':
            if not tokens:raise ValueError(HELP[command])
            name=tokens.pop(0)
        while tokens:
            option=tokens.pop(0)
            if option not in ('--start','--count','--depth') or not tokens:raise ValueError(HELP[command])
            key=option[2:];options[key]=int(tokens.pop(0))
        if not 0<=options['start']<=4294967295 or not 1<=options['count']<=100 or not 0<=options['depth']<=8:raise ValueError('start >= 0, count 1..100, depth 0..8 required')
        text,display=variable_views(session,name,**options)
        return None,{'display_data':[display]},debug
    sdk=SDKTools(session.runtime.sdk)
    if command in ('%export','%%export'):
        tokens=words(args);values=True
        if tokens and tokens[0]=='--definitions-only':values=False;tokens.pop(0)
        if len(tokens)!=1:raise ValueError(HELP[command])
        source,_=portable_source(session,'\n'+body if body else '',values)
        target=Path(tokens[0]).expanduser().resolve()
        if target.suffix.lower()!='.das':raise ValueError('Export path must end with .das')
        # Compile before replacing a requested file; don't simulate or run macros twice at execution.
        if __package__:from .session import Program
        else:from session import Program
        program=Program(session.runtime,source,simulate=False,fresh_modules=True)
        program.close()
        handle=tempfile.NamedTemporaryFile(prefix=target.name+'.',suffix='.tmp',dir=target.parent,delete=False)
        temporary=Path(handle.name);handle.close()
        try:temporary.write_text(source,encoding='utf-8');temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
        return 'Exported '+str(target),None,debug
    if command=='%pkg':
        tokens=words(args)
        if not tokens or tokens[0] not in ('list','search','install','update','upgrade','build','check','doctor'):raise ValueError(HELP[command])
        if any(t in ('--global','-g','--root','--force') or t.startswith(('--root=','--global=','--force=')) for t in tokens):raise ValueError('Notebook packages use the current project; global/root/force overrides are not supported')
        output=sdk.script('utils/daspkg/main.das',tokens,timeout=300)
        if tokens[0] in ('install','update','upgrade','build'):session.runtime.register_project_modules()
        return output,None,debug
    if command=='%lsp':
        if args not in ('on','off'):raise ValueError(HELP[command])
        return 'Compiler-backed hover '+args,{'lsp_enabled':args=='on'},debug
    if command=='%modules':
        if args:raise ValueError(HELP[command])
        folders=sorted(p.name for p in (Path.cwd()/'modules').iterdir() if p.is_dir()) if (Path.cwd()/'modules').exists() else []
        return 'Registered native modules:\n'+', '.join(sorted(session.runtime.native_modules))+'\nProject modules:\n'+', '.join(folders),None,debug
    if command=='%test':
        tokens=words(args)
        if not tokens:raise ValueError(HELP[command])
        return sdk.script('dastest/dastest.das',['--test',tokens[0],*tokens[1:]],timeout=300),None,debug
    if command in ('%%test','%%coverage'):
        source,mapping=portable_source(session,'\n'+body)
        with SourceFile(source) as path:
            if command=='%%test':
                if args:raise ValueError(HELP[command])
                return 'PASS: isolated notebook assertions\n'+sdk.run([str(path)]),None,debug
            tokens=words(args)
            if tokens and (len(tokens)!=2 or tokens[0]!='--output'):raise ValueError(HELP[command])
            report=Path(tokens[1]).expanduser().resolve() if tokens else path.with_suffix('.lcov')
            try:
                output=sdk.script('utils/dascov/main.das',[str(report),str(path)])
                summary,rows,records=notebook_coverage(report.read_text(encoding='utf-8'),path,mapping)
                if tokens:
                    saved_source=report.with_suffix('.source.das')
                    saved_source.write_text(source,encoding='utf-8')
                    report.write_text('SF:'+saved_source.as_posix()+'\n'+'\n'.join(records)+'\nend_of_record\n',encoding='utf-8')
                return output+'\n'+summary+('\nLCOV: '+str(report) if tokens else ''),{'coverage':rows},debug
            finally:
                if not tokens:report.unlink(missing_ok=True)
    if command in ('%%check','%definition','%%lsp'):
        op='check' if command=='%%check' else 'definition'
        cursor=0;fragment='\n'+body if body else args
        if command=='%%check' and args:raise ValueError(HELP[command])
        if command=='%%lsp':
            tokens=words(args)
            if len(tokens)!=2 or tokens[0] not in ('hover','definition'):raise ValueError('%%lsp hover|definition CURSOR')
            op=tokens[0];cursor=int(tokens[1]);fragment=body
        result=language_query(session,op,fragment,cursor)
        return json.dumps(result,ensure_ascii=False,indent=2),{'language':result},debug
    if command=='%%debug':
        tokens=words(args);breaks=[2]
        if tokens:
            if len(tokens)!=2 or tokens[0]!='--break':raise ValueError(HELP[command])
            breaks=[int(n) for n in tokens[1].split(',')]
        source,mapping=portable_source(session,'\n'+body)
        if debug:debug.close()
        debug=DebugSession(session.runtime.sdk,source,mapping,breaks)
        return json.dumps({'breakpoints':debug.breakpoints,'event':debug.wait()},ensure_ascii=False,indent=2),None,debug
    if command=='%debug':
        tokens=args.split(None,1);op=tokens[0] if tokens else '';value=tokens[1] if len(tokens)>1 else ''
        if op=='stop':
            if debug:debug.close()
            return 'Debug session stopped',None,None
        if debug is None:raise ValueError('Start an isolated session with %%debug first')
        if op=='stack':result=debug.stack()
        elif op=='wait':result=debug.wait()
        elif op=='scopes':
            frames=debug.stack()['stackFrames']
            result=debug.request('scopes',{'frameId':frames[0]['id']})
        elif op=='variables':result=debug.request('variables',{'variablesReference':int(value),'start':0,'count':100})
        elif op=='eval':
            frames=debug.stack()['stackFrames']
            result=debug.request('evaluate',{'expression':value,'frameId':frames[0]['id'],'context':'watch'})
        elif op in ('next','in','out','continue','pause'):
            debug.request({'next':'next','in':'stepIn','out':'stepOut'}.get(op,op),{'threadId':debug.thread_id})
            result=debug.wait()
        else:raise ValueError(HELP[command])
        if result.get('event') in ('terminated','exited'):
            debug.close();debug=None
        return json.dumps(result,ensure_ascii=False,indent=2),None,debug
    raise ValueError('Unknown tool operation')
