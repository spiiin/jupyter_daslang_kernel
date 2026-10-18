"""Bounded Jupyter MIME bundles for explicit daslang display calls."""
import base64
import html
import json
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50
PRELUDE = r'''require daslib/json_boost;
var djnb_displays : array<string>
var djnb_display_bytes = 0
def djnb_emit(kind:string; value:string) {
 if (length(djnb_displays)>=100 || djnb_display_bytes+length(value)>8388608) { panic("Notebook display limit exceeded") }
 djnb_display_bytes+=length(value)
 djnb_displays |> push(sprint_json(fixed_array(kind,value),false))
}
def display(value:auto) { djnb_emit("json",sprint_json(value,false)) }
def display_html(value:string) { djnb_emit("html",value) }
def display_markdown(value:string) { djnb_emit("markdown",value) }
def display_image(path:string) { djnb_emit("image",path) }
def display_audio(path:string) { djnb_emit("audio",path) }
[export]
def djnb_take_displays():string {
 let output=sprint_json(djnb_displays,false)
 djnb_displays |> clear
 djnb_display_bytes=0
 return output
}
'''

def table(value):
    rows = value if isinstance(value,list) else [value]
    shown = rows[:MAX_ROWS]
    columns = list(dict.fromkeys(k for row in shown if isinstance(row,dict) for k in row))[:30] if all(isinstance(r,dict) for r in shown) else []
    if not columns:
        columns=['value']
        shown=[{'value':v} for v in shown]
    def escape(v):
        text=json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)
        return html.escape(text[:1000] + ('…' if len(text)>1000 else ''))
    header='<tr><th>#</th>'+''.join('<th>'+html.escape(str(k))+'</th>' for k in columns)+'</tr>'
    body=''.join('<tr><td>'+str(i)+'</td>'+''.join('<td>'+escape(row.get(k,''))+'</td>' for k in columns)+'</tr>' for i,row in enumerate(shown))
    return '<table>'+header+body+'</table><p>'+str(len(shown))+' / '+str(len(rows))+' rows</p>'

def bundle(kind, payload):
    if len(payload.encode('utf-8'))>MAX_BYTES:
        raise ValueError('Display exceeds 8 MiB limit')
    if kind=='json':
        value=json.loads(payload)
        return {'text/plain':json.dumps(value,ensure_ascii=False,indent=2), 'text/html':table(value)}
    if kind in ('html','markdown'):
        return {'text/plain':payload, 'text/'+kind:payload}
    path=Path(payload).expanduser().resolve()
    if path.stat().st_size>MAX_BYTES:
        raise ValueError('Display file exceeds 8 MiB limit')
    data=path.read_bytes()
    encoded=base64.b64encode(data).decode('ascii')
    if kind=='image':
        if data.startswith(b'\x89PNG\r\n\x1a\n'): mime='image/png'
        elif data.startswith(b'\xff\xd8\xff'): mime='image/jpeg'
        elif path.suffix.lower()=='.svg': return {'text/plain':str(path),'image/svg+xml':data.decode('utf-8-sig')}
        else: raise ValueError('Images must be PNG, JPEG or SVG')
        return {'text/plain':str(path),mime:encoded}
    if kind=='audio':
        if not (data[:4]==b'RIFF' and data[8:12]==b'WAVE'):
            raise ValueError('Audio must be a WAV file')
        return {'text/plain':str(path),'text/html':'<audio controls src="data:audio/wav;base64,'+encoded+'"></audio>'}
    raise ValueError('Unknown display kind: '+kind)

def collect(raw):
    result=[]
    messages=json.loads(raw)
    if len(messages)>100:
        raise ValueError('At most 100 display calls per cell')
    for message in messages:
        kind,payload=json.loads(message)
        result.append({'data':bundle(kind,payload),'metadata':{}})
    if sum(len(json.dumps(item)) for item in result)>MAX_BYTES*2:
        raise ValueError('Combined display output exceeds 16 MiB')
    return result
