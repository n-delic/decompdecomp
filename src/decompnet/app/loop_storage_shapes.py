"""Optional loop/counter representations for matching; the original draft remains available."""
import re

from decomprepair import integer_abi, local_flow, repair_localisation
from decomprepair.allocator_call_guards import dominators
from decomprepair.integer_repair_support import edit, refs
from decomprepair.pattern_repairs import close

FAMILIES = ('explicit_loop_backedge', 'row_number_staging')
LIMIT = 4


def fresh(source, header, stem):
    while re.search(r'\b'+re.escape(stem)+r'\b',source+'\n'+header):stem += '_'
    return stem


def variants(source, name, header='', arch=None, locations=None, *, mapped_only=False):
    flow=local_flow.analyze(source,name)
    if not flow:return []
    part=flow.part
    dom=dominators(flow)
    from decomprepair.equal_value_flow import macros_safe
    if not macros_safe(source,header,part.body,{d['name']:d['type'] for d in flow.locals}):return []
    if re.search(r'\b(?:__LINE__|__COUNTER__|__FILE__)\b',source+'\n'+header):return []
    relevant=repair_localisation.lines(source,locations)
    result=[]
    def offer(family,text,start,end):
        lines=range(source.count('\n',0,start)+1,source.count('\n',0,end)+2)
        result.append((not bool(relevant.intersection(lines)),family,text))
    for hit in re.finditer(r'\bdo\s*{',part.body):
        start=part.opening+1+hit.start();opening=part.opening+hit.end()
        end=close(part.clean,opening,'{','}')
        tail=re.match(r'\s*while\s*\(',part.clean[end:]) if end else None
        if not tail:continue
        cond=end+tail.end()-1;stop=close(part.clean,cond)
        semi=re.match(r'\s*;',part.clean[stop:]) if stop else None
        body=part.clean[opening+1:end-1]
        # Preserve the original body scope; loop-directed transfers cannot simply
        # become transfers to an enclosing loop/switch after removing this do.
        if not semi or re.search(r'\b(?:break|continue|goto|case|default)\b|(?<!:):(?!:)',body):continue
        label=fresh(source,header,'__repair_rows')
        text=label+': '+source[opening:end]+'\nif ('+source[cond+1:stop-1]+') goto '+label+';'
        offer(FAMILIES[0],edit(source,[(start,stop+semi.end(),text)]),start,stop)
    for label,index in flow.labels.items():
        jumps=[flow.nodes[i] for i,key in flow.gotos if key==label]
        if len(jumps)!=1:continue
        begin=flow.nodes[index].end;jump=jumps[0]
        if jump.start<=begin:continue
        # A plain conditional latch with the sole backedge. Its counter's next
        # value is committed before evaluating this exact original condition.
        latch=None
        for hit in re.finditer(r'\bif\s*\(',part.clean[begin:jump.start]):
            start=begin+hit.start();opening=begin+hit.end()-1;end=close(part.clean,opening)
            if end and re.fullmatch(r'\s*goto\s+'+re.escape(label)+r'\s*;',part.clean[end:jump.end]):
                latch=(start,opening,end)
        if not latch:continue
        start,opening,end=latch
        for decl in flow.locals:
            counter=decl['name']
            if integer_abi.scalar(decl['type'],header+'\n'+source,arch)!=(32,False):continue
            if not re.search(r'\b'+re.escape(counter)+r'\b',part.clean[opening:end]):continue
            updates=list(re.finditer(r'\b'+re.escape(counter)+r'\s*\+\+\s*;',part.clean[begin:start]))
            if len(updates)!=1:continue
            update=updates[0];lo=begin+update.start();hi=begin+update.end()
            node=flow.at(lo)
            if not node or part.clean[node.start:node.end].strip()!=update[0].strip():continue
            defining=flow.nodes.index(node)
            affected=[i for i,n in enumerate(flow.nodes) if hi<=n.start<=start]
            if any(defining not in dom.get(i,set()) for i in affected):continue
            remainder=part.clean[hi:start]
            if re.search(r'\b(?:goto|return|break|continue|for|while|do|switch)\b',remainder):continue
            if re.search(r'\b'+re.escape(counter)+r'\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])?=(?!=))|(?:\+\+|--)\s*\b'+re.escape(counter)+r'\b',remainder):continue
            macro_lines=re.findall(r'(?m)^\s*#\s*define(?:[^\n\\]|\\[^\n]|\\\n)*',source+'\n'+header)
            if any(re.search(r'\b'+re.escape(counter)+r'\b',m) for m in macro_lines):continue
            next_value=fresh(source,header,'__repair_row_number')
            changes=[(decl.end(),decl.end(),'\n  '+decl['type']+' '+next_value+';'),
                     (lo,hi,next_value+' = '+counter+' + 1;'),
                     (start,start,counter+' = '+next_value+';\n        ')]
            changes += [(m.start(),m.end(),next_value) for m in refs(part,decl) if hi<=m.start()<start]
            offer(FAMILIES[1],edit(source,changes),lo,start)
    # Rank whole loop hypotheses, never split edits or infer a match from a line.
    # Stable sorting preserves broad order when mappings are absent or stale.
    selected=[];broad_backedges=0
    for broad,family,text in sorted(result,key=lambda item:item[0]):
        if mapped_only and broad:continue
        if broad and family==FAMILIES[0]:
            if broad_backedges>=2:continue  # keep counter proposals in the unguided shortlist too
            broad_backedges+=1
        if (family,text) not in selected:selected.append((family,text))
        if len(selected)==LIMIT:break
    return selected
