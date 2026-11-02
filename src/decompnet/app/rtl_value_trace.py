"""Bounded legacy GCC def/use evidence. Physical register reuse never proves a C variable identity."""
import hashlib
import re
from collections import Counter, defaultdict

REG = r'\(reg(?:/[^:\s()]*)?:\w+\s+(\d+)(?:\s+([A-Za-z_]\w*))?\)'


def function(text, name):
    parts = re.split(r'^;; Function (\S+)[^\n]*\n', text, flags=re.M)
    matches = [parts[i+1] for i in range(1,len(parts)-1,2) if parts[i]==name]
    return matches[0] if len(matches)==1 else ''


def instructions(text):
    line = None
    for block in re.split(r'\n(?=\((?:note|insn|jump_insn|call_insn|code_label|barrier)\b)', '\n'+text):
        note = re.match(r'\(note\s+\d+\s+\d+\s+\d+\s+\("([^"]+)"\)\s+(\d+)\)',block)
        if note:
            line = int(note[2]) if note[1]=='func.c' else None
        head = re.match(r'\((insn|jump_insn|call_insn)\s+(\d+)\b',block)
        if not head:continue
        expression = re.split(r'\(expr_list:REG_',block,maxsplit=1)[0]
        defs = [int(m[1]) for m in re.finditer(r'\(set\s+'+REG,expression)]
        clobbers = [int(m[1]) for m in re.finditer(r'\(clobber\s+'+REG,expression)]
        uses = Counter(int(m[1]) for m in re.finditer(REG,expression))
        uses.subtract(defs+clobbers)
        yield dict(uid=int(head[2]),line=line,definitions=defs,uses=sorted(k for k,v in uses.items() if v>0),
                   deaths=[int(m[1]) for m in re.finditer(r'\(expr_list:REG_DEAD\s+'+REG,block)],
                   equal_constants=[int(v) for v in re.findall(r'\(expr_list:REG_EQUAL\s+\(const_int (-?\d+)\)',block)],
                   call=head[1]=='call_insn')


def summarize(phases, name):
    """Accept complete bounded dumps only; join instruction IDs within this single compilation."""
    texts = {phase:function(text,name) for phase,text in phases.items()}
    flow = list(instructions(texts.get('flow','')))
    initial = {r['uid']:r for r in instructions(texts.get('rtl',''))}
    dispositions = dict((int(a),int(b)) for a,b in re.findall(r'\b(\d+)\s+in\s+(\d+)\b',
        texts.get('greg','').split(';; Hard regs used:',1)[0]))
    aliases = defaultdict(set)
    for match in re.finditer(REG,texts.get('greg','')):
        if match[2]:aliases[int(match[1])].add(match[2])
    values = defaultdict(lambda:dict(definitions=[],uses=[],deaths=[],source_lines=[],calls_crossed=None,equal_constants=[]))
    for record in flow[:4096]:
        origin = initial.get(record['uid'],record)
        for kind in ('definitions','uses','deaths'):
            for pseudo in record[kind]:
                if pseudo not in dispositions:continue
                item=values[pseudo]
                if len(item[kind])<32:item[kind].append(record['uid'])
                if kind=='definitions':
                    item['equal_constants']=list(dict.fromkeys(item['equal_constants']+record['equal_constants']))[:16]
                if origin['line'] and origin['line'] not in item['source_lines']:
                    item['source_lines'].append(origin['line'])
    for match in re.finditer(r'^Register (\d+) used [^\n]*crosses (\d+) calls?',texts.get('flow',''),re.M):
        if int(match[1]) in values:values[int(match[1])]['calls_crossed']=int(match[2])
    result=[]
    for pseudo,item in sorted(values.items()):
        hard=dispositions[pseudo]
        result.append(dict(pseudo=pseudo,hard_register=hard,aliases=sorted(aliases[hard]),**item))
    return {'values':result[:512], 'truncated':len(flow)>4096 or len(result)>512,
            'note':'Explicit GCC instruction IDs, source notes and dispositions. Register reuse can identify several candidate values; no assembly-address or C-name correspondence is inferred.'}


def focus(trace, source, rows, header=None):
    """Find candidate source lines through explicit GCC hard-register aliases, preserving ambiguity."""
    if not trace or trace.get('source_sha256')!=hashlib.sha256(source.encode()).hexdigest() or trace.get('truncated'):
        return []
    if header is not None and trace.get('header_sha256')!=hashlib.sha256(header.encode()).hexdigest():return []
    if re.search(r'^\s*#\s*(?:line\b|\d)',source,re.M):return []
    names=set()
    for row in rows or []:
        if not isinstance(row,(list,tuple)) or len(row)!=3 or row[0]!='r' or not isinstance(row[2],str):continue
        names.update(re.findall(r'(?<!\w)[$%]([A-Za-z_]\w*)\b',row[2]))
    lines=set()
    for item in trace.get('values',[]):
        if names.intersection(item.get('aliases',[])):
            lines.update(v for v in item.get('source_lines',[]) if isinstance(v,int) and 0<v<=len(source.splitlines()))
    return sorted(lines)[:64]


def saved(net, proj, fn, source, header):
    """Read an existing trace without starting tools; stale inputs cannot supply local candidates."""
    import json

    from ..project import compiler_for
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):return {},[]
    row=net.store.one('SELECT state FROM repair_frontiers WHERE function_id=?',fn['id'])
    if not row:return {},[]
    try:state=json.loads(row['state'])
    except (ValueError,TypeError):return {},[]
    trace=state.get('value_trace') or {}
    settings=compiler_for(json.loads(proj['config']),fn['vram'])
    profile=hashlib.sha256(json.dumps(settings,sort_keys=True,default=str).encode()).hexdigest()
    if trace.get('profile_sha256')!=profile:return {},[]
    rows=state.get('value_trace_rows') or []
    return (trace,rows) if focus(trace,source,rows,header) else ({},[])


def anchors(trace, source):
    """Hash unique unchanged statement lines, not pseudo IDs, for a cross-build comparison."""
    from .repair_tokens import tokens
    lines=source.splitlines();by_hash=defaultdict(list)
    for index,line in enumerate(lines,1):
        significant=[t for t in tokens(line) if not t.isspace() and not t.startswith(('//','/*'))]
        if len(significant)>=3:
            by_hash[hashlib.sha256(repr(significant).encode()).hexdigest()].append(index)
    trace['line_anchors']={str(indices[0]):key for key,indices in by_hash.items() if len(indices)==1}
    return trace


def compare(before, after):
    """Align only unambiguous unchanged source anchors; pseudo numbers have compilation-local meaning."""
    if (not before or not after or before.get('truncated') or after.get('truncated')
            or before.get('header_sha256')!=after.get('header_sha256')
            or before.get('profile_sha256')!=after.get('profile_sha256')):
        return {'comparable':False,'note':'Need complete traces under the same header/profile.'}
    def indexed(trace):
        result=defaultdict(list)
        for item in trace.get('values',[]):
            for anchor in {trace.get('line_anchors',{}).get(str(line)) for line in item.get('source_lines',[])}-{None}:
                result[anchor].append(item)
        return result
    left,right=indexed(before),indexed(after);changes=[];ambiguous=0
    for anchor in sorted(left.keys() & right.keys()):
        a,b=left[anchor],right[anchor]
        if len(a)!=1 or len(b)!=1:ambiguous+=1;continue
        a,b=a[0],b[0]
        fields={'register':a.get('hard_register')!=b.get('hard_register'),
                'definitions':len(a.get('definitions',[]))!=len(b.get('definitions',[])),
                'uses':len(a.get('uses',[]))!=len(b.get('uses',[])),
                'deaths':len(a.get('deaths',[]))!=len(b.get('deaths',[])),
                'calls_crossed':a.get('calls_crossed')!=b.get('calls_crossed')}
        if any(fields.values()):changes.append(dict(anchor=anchor,**fields))
    return dict(comparable=True,changes=changes[:32],ambiguous_anchors=ambiguous,
                note='Unique unchanged C-line anchors only. A line can contain several values; ambiguous joins are omitted. Counts and register changes are leads, not causal proof.')
