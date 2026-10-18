"""Filter SDK LCOV output to notebook code, excluding generated helpers."""
from pathlib import Path

def notebook_coverage(text,path,mapping):
    lines={};branches=[];records=[]
    for record in text.split('end_of_record'):
        rows=record.strip().splitlines()
        filename=next((r[3:] for r in rows if r.startswith('SF:')),None)
        if not filename or Path(filename).resolve()!=path.resolve():continue
        kept=[]
        for row in rows:
            if row.startswith('DA:'):
                line,hits=map(int,row[3:].split(',')[:2]);origin=mapping.get(line)
                if not origin:continue
                key=(origin[0],origin[1],origin[2]);lines[key]=max(lines.get(key,0),hits)
                kept.append(row)
            elif row.startswith('BRDA:'):
                fields=row[5:].split(',');line=int(fields[0]);origin=mapping.get(line)
                if not origin:continue
                hits=0 if fields[3]=='-' else int(fields[3]);branches.append((origin,hits));kept.append(row)
        records=kept
    total=len(lines);hit=sum(v>0 for v in lines.values());bhit=sum(v>0 for _,v in branches)
    rows=[{'cell':key[0],'line':key[1],'hits':value,'source':key[2]} for key,value in sorted(lines.items())]
    summary=f'Notebook lines: {hit}/{total} covered'+(f' ({100*hit/total:.1f}%)' if total else '')+f'\nBranches: {bhit}/{len(branches)} covered'
    for row in rows:
        summary+=f"\n{row['cell']}:{row['line']}  {row['hits']:5d}  {row['source']}"
    return summary,rows,records
