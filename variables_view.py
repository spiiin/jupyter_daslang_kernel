"""Read-only bounded views of live root globals via native RTTI, no evaluation."""
import html
import json


def read_value(session,name,start=0,count=50,depth=4):
    if name not in session.variables:raise ValueError('Unknown notebook variable: '+name)
    function=getattr(session.runtime.bridge,'nb_view_value',None)
    if function is None:raise RuntimeError('Rebuild native adapter for %vars and %view')
    raw=function(session.current.context,name.encode(),start,count,depth)
    node=json.loads(raw.decode('utf-8',errors='replace'))
    if 'error' in node:raise ValueError(node['error'])
    return node


def summary(node):
    kind=node['kind']
    if kind=='scalar':return json.dumps(node.get('value'),ensure_ascii=False)
    if kind=='array':
        values=[summary(item['value']) for item in node.get('items',[])[:5]]
        return '['+', '.join(values)+(', …' if node['size']>len(values) else '')+'] · '+str(node['size'])+' elements'
    if kind=='table':return str(node['size'])+' entries'
    if kind=='struct':return str(node['size'])+' fields'
    return '…'


def tree(node,label='',opened=False):
    kind=node['kind'];label=html.escape(label+(' : '+node['type'] if label and node.get('type') else ''))
    if kind in ('scalar','truncated'):
        return '<span>'+label+(': ' if label else '')+html.escape(summary(node))+'</span>'
    children=[]
    if kind=='struct':
        children=[tree(field['value'],field['name']) for field in node['fields']]
    elif kind=='array':
        children=[tree(item['value'],'['+str(item['index'])+']') for item in node['items']]
    elif kind=='table':
        children=[tree(item['value'],summary(item['key'])) for item in node['items']]
    details='<details'+(' open' if opened else '')+'><summary>'+label+' — '+html.escape(summary(node))+'</summary>'
    details+='<ul>'+''.join('<li>'+child+'</li>' for child in children)+'</ul>'
    if kind in ('array','table'):
        details+='<p>Shown '+str(len(node['items']))+' / '+str(node['size'])+'; start '+str(node.get('start',0))+'. Use --start / --count for another range.</p>'
        if node.get('scanLimited'):details+='<p>Sparse table scan reached its safety limit.</p>'
    return details+'</details>'


def array_table(node):
    items=node.get('items',[])
    if not items or not all(item['value']['kind']=='struct' for item in items):return None
    fields=list(dict.fromkeys(f['name'] for item in items for f in item['value']['fields']))[:30]
    out='<table><tr><th>#</th>'+''.join('<th>'+html.escape(f)+'</th>' for f in fields)+'</tr>'
    for item in items:
        values={field['name']:field['value'] for field in item['value']['fields']}
        out+='<tr><td>'+str(item['index'])+'</td>'+''.join('<td>'+tree(values[f])+'</td>' if f in values else '<td></td>' for f in fields)+'</tr>'
    return out+'</table><p>Shown '+str(len(items))+' / '+str(node['size'])+'; start '+str(node.get('start',0))+'.</p>'


def variable_views(session,name=None,start=0,count=50,depth=4):
    if name is not None:
        node=read_value(session,name,start,count,depth)
        label=name+' : '+session.variables[name]
        text=label+'\n'+json.dumps(node,ensure_ascii=False,indent=2)
        rendered=array_table(node) if node['kind']=='array' else None
        rendered='<p>'+html.escape(label)+'</p>'+(rendered or tree(node,name,True))
        return text,{'data':{'text/plain':text,'text/html':rendered},'metadata':{}}
    names=list(session.variables)[start:start+count]
    rows=[]
    for key in names:
        node=read_value(session,key,0,3,1)
        rows.append((key,session.variables[key],summary(node),key in session.constants))
    text='Name | Type | Binding | Preview\n'+'\n'.join(key+' | '+typ+' | '+('let' if const else 'var')+' | '+preview for key,typ,preview,const in rows)
    out='<table><tr><th>Name</th><th>Type</th><th>Binding</th><th>Preview</th></tr>'
    for key,typ,preview,const in rows:
        out+='<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in (key,typ,'let' if const else 'var',preview))+'</tr>'
    suffix=str(len(names))+' / '+str(len(session.variables))+' variables; start '+str(start)
    text+='\n'+suffix;out+='</table><p>'+suffix+'</p>'
    return text,{'data':{'text/plain':text,'text/html':out},'metadata':{}}
