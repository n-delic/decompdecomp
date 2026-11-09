"""Evidence-directed private layout/constant trials; no target or shared context is edited."""
import re

from .. import arch as arches
from ..forks.csource import _blank
from .pattern_repairs import close

ID=r'[A-Za-z_]\w*'
NUM=r'-?(?:0x[0-9a-fA-F]+|\d+)'


def locations(obj, name, rows, source):
    from ..source_lines import mismatches
    directives=list(re.finditer(r'(?m)^\s*#line\s+(\d+)\s+"([^"]+)"',source))
    if len(directives)>1:return {}
    filename,first='func.c',1
    if directives:
        m=directives[0];filename=m[2];first=int(m[1])-source[:m.end()].count('\n')-1
        # first_line offsets debug line numbers to the physical candidate, including #line.
    try:return mismatches(obj,name,rows,source,filename=filename,first_line=first)
    except (ValueError,KeyError,IndexError):return {}


def record_spans(header):
    clean=_blank(header)
    for m in re.finditer(r'\btypedef\s+struct(?:\s+'+ID+r')?\s*\{',clean):
        a=m.end()-1;b=close(clean,a,'{','}')
        if b and '{' not in clean[a+1:b-1] and ':' not in clean[a+1:b-1]:
            yield a+1,b-1


def variants(source, header, arch, rows, mapping):
    reader=arches.get(arch).source_operand_changes
    if not reader:return []
    changes=reader(rows);lines={}
    for item in mapping.get('lines',[]):
        if 1<=item['line']<=len(source.splitlines()):
            for ins in item['instructions']:lines[ins['row']]=source.splitlines()[item['line']-1]
    result=[];fields=[x for x in changes if x['kind']=='field']
    # At least two independently mapped field operands must agree on the same small gap.
    deltas={x['new']-x['old'] for x in fields}
    names=set()
    for x in fields:
        line=_blank(lines.get(x['row'],''))
        assignment=re.search(r'(?<![=!<>])(?:[+*/&|^-])?=(?!=)',line)
        if assignment:line=line[:assignment.start()] if x.get('store') else line[assignment.end():]
        chains=re.findall(r'(?:(?:->|\.)\s*'+ID+r'\s*)+',line)
        if len(chains)==1:
            members=re.findall(ID,chains[0])
            if members:names.add(members[-1])
    if len(fields)>=2 and len(names)>=2 and len(deltas)==1:
        delta=next(iter(deltas))
        if delta in {1,2,4,8,12,16,24,32}:
            clean=_blank(header)
            for a,b in record_spans(header):
                positions=[]
                for name in names:
                    hits=list(re.finditer(r'\b'+re.escape(name)+r'\b',clean[a:b]))
                    if len(hits)!=1:break
                    pos=a+hits[0].start();start=clean.rfind(';',a,pos)+1
                    positions.append(max(a,start))
                else:
                    start=min(positions)
                    # A comma declaration cannot be split into an invented type.
                    stop=clean.find(';',start,b)
                    if stop<0 or ',' in clean[start:stop] or 'cpu_layout_gap' in clean[a:b]:continue
                    h=header[:start]+f' unsigned char cpu_layout_gap[{delta}]; '+header[start:]
                    result.append(('cpu-record-gap',source,h))
    from .retail_source_repairs import field_order
    result += field_order(source,header,fields,lines,list(record_spans(header)))
    constants=[]
    for x in changes:
        if x['kind']!='constant' or x['old']==x['new'] or not 0<=x['new']<=4096:continue
        line=_blank(lines.get(x['row'],''))
        if not re.search(r'\b(?:if|for|while)\s*\(',line):continue
        # Only comparison operands or an explicit sizeof(array) bound, never arbitrary arithmetic.
        names=re.findall(r'(?:==|!=|<=|>=|<|>)\s*('+ID+r')\b',line)
        for name in names:
            pattern=r'(?m)^(\s*#\s*define\s+'+re.escape(name)+r'\s+)('+NUM+r')([uUlL]*)(?=\s|$)'
            for text,is_header in ((header,True),(source,False)):
                hits=list(re.finditer(pattern,_blank(text)))
                if len(hits)==1 and int(hits[0][2],0)==x['old']:
                    m=hits[0];new=text[:m.start(2)]+str(x['new'])+text[m.end(2):]
                    constants.append((source,new) if is_header else (new,header))
        if re.search(r'(?:==|!=|<=|>=|<|>)\s*'+re.escape(str(x['old']))+r'\b',line):
            pattern=r'((?:==|!=|<=|>=|<|>)\s*)'+re.escape(str(x['old']))+r'\b'
            clean=_blank(source)
            edits=list(re.finditer(pattern,clean));new=source
            if 0<len(edits)<=8:
                for m in reversed(edits):new=new[:m.start()]+m[1]+str(x['new'])+new[m.end():]
                constants.append((new,header))
        for name in re.findall(r'\bsizeof\s*\(\s*('+ID+r')\s*\)',line):
            pattern=r'\b'+re.escape(name)+r'\s*\[\s*('+NUM+r')\s*\]'
            hits=list(re.finditer(pattern,_blank(header)))
            if len(hits)==1 and int(hits[0][1],0)==x['old'] and x['new']>0:
                m=hits[0];constants.append((source,header[:m.start(1)]+str(x['new'])+header[m.end(1):]))
    unique=list(dict.fromkeys(constants))
    if len(unique)>=2:
        from difflib import SequenceMatcher
        combined=[]
        valid=True
        for index,base in enumerate((source,header)):
            edits=[]
            for pair in unique[:2]:
                for tag,a,b,c,d in SequenceMatcher(None,base,pair[index],autojunk=False).get_opcodes():
                    if tag!='equal':edits.append((a,b,pair[index][c:d]))
            edits=sorted(set(edits))
            if any(a[1]>b[0] for a,b in zip(edits,edits[1:])):valid=False;break
            new=base
            for a,b,text in reversed(edits):new=new[:a]+text+new[b:]
            combined.append(new)
        if valid:result.append(('cpu-retail-constant-bundle',*combined))
    result += [('cpu-retail-constant',s,h) for s,h in dict.fromkeys(constants)][:3]
    return result[:4]
