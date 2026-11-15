"""Keep actionable compiler errors and private complete logs; never put reference logs in exports."""
import os
import re
import tempfile
from pathlib import Path


def compile_error(net, fn, output):
    output=str(output)
    lines=output.splitlines()
    first=next((i for i,line in enumerate(lines) if re.search(r'\b(?:fatal\s+)?error\b',line,re.I)),0)
    summary='\n'.join(lines[first:first+5])[:1000] or 'No compiler diagnostic was returned.'
    workspace=getattr(net,'workspace',None)
    if workspace:
        try:
            folder=Path(workspace)/'.decomp'/'logs'/'source-preparation'
            folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            target=folder/f'{int(fn["id"])}.log'
            with tempfile.NamedTemporaryFile(dir=folder,mode='w',encoding='utf-8',delete=False) as f:
                f.write(output);temp=Path(f.name)
            os.replace(temp,target)
            summary+='\nFull compiler log: '+str(target)
        except OSError:
            summary+='\nThe full compiler log could not be saved.'
    return summary


def allocations(source, rows):
    """Only a sole differing immediate near a recognized allocation call supports this advisory."""
    if not re.search(r'\bsizeof\s*\(',source):return []
    rows=rows or [];changed=[(i,r) for i,r in enumerate(rows) if len(r)!=3 or r[0]!=' ']
    if len(changed)!=1:return []
    i,row=changed[0]
    if len(row)!=3:return []
    pattern=r'addiu\s+(\$\w+),\s*\$zero,\s*(0x[0-9a-fA-F]+|[0-9]+)'
    a=re.fullmatch(pattern,row[1]);b=re.fullmatch(pattern,row[2])
    if not a or not b or a[1]!=b[1]:return []
    target,candidate=int(a[2],0),int(b[2],0)
    if not (0<target<16*1024*1024 and 0<candidate<16*1024*1024) or target==candidate:return []
    if not any(re.fullmatch(r'(?:jal|bl|call)\s+(?:alHeapDBAlloc|malloc|calloc)',r[1].strip())
               for r in rows[i+1:i+13] if len(r)==3):return []
    return [f'Only an immediate near an allocation differs: target {target}, candidate {candidate}. '
            'Check the allocation extent against sizeof in the reference context and the target argument setup. '
            'This does not establish a missing field or padding; preserve observed field offsets and rejudge any repair.']
