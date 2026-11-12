"""Bounded hypotheses from captured matches; no source spelling supplies a match verdict."""
import re

from .call_lifetime_repairs import block_path
from .csource import _blank
from .integer_repair_support import IDENT, edit, locals_for, split_args, view
from .local_flow import analyze
from .pattern_repairs import close

FAMILIES = ('shared_branch_locals', 'loop_exit_value', 'float_call_staging',
            'inline_scalar_chain', 'success_pointer_update')
TYPE = r'(?!(?:return|goto|case|break|continue|if|else|while|for|switch)\b)(?:unsigned\s+(?:char|short|int)|signed\s+(?:char|short|int)|[su](?:8|16|32)|int|char|short|float|double|f32|f64|'+IDENT+r')'
DECL = re.compile(rf'\s*({TYPE}\s*\*?\s*)({IDENT})\s*=\s*([^;{{}}]+);')


def context(source, name, header, pointer_only=False):
    part=view(source,name)
    if not part or part.end-part.opening>20000 or re.search(r'\b(?:volatile|asm|__asm__|setjmp|longjmp)\b',(part.body if pointer_only else part.clean)):
        return None
    if not pointer_only:
        words=set(re.findall(IDENT,part.body))
        for declaration in re.finditer(r'\btypedef\b[^;]*\bvolatile\b[^;]*;',_blank(header)):
            alias=re.search(r'('+IDENT+r')\s*;\s*$',declaration[0])
            if alias and alias[1] in words:return None
        for hit in re.finditer(r'\bvolatile\b([^;]*);',_blank(header)):
            names=set(re.findall(IDENT,hit[1]))-{'int','char','short','long','unsigned','signed','float','double','u8','u16','u32','s8','s16','s32'}
            if names & words:return None
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')',source+'\n'+header,re.M))
    if macros & set(re.findall(IDENT,part.body)):
        return None
    return part


def branches(source, name, header=''):
    part=context(source,name,header)
    if not part or re.search(r'\bgoto\b',part.body):return []
    result=[]
    for hit in re.finditer(r'\bif\s*\(',part.body):
        start=part.opening+1+hit.start();end=close(part.clean,part.opening+hit.end())
        arm=re.match(r'\s*{',part.clean[end:]) if end else None
        if not arm:continue
        a=end+arm.end()-1;b=close(part.clean,a,'{','}')
        sibling=re.match(r'\s*else\s*{',part.clean[b:]) if b else None
        if not sibling:continue
        c=b+sibling.end()-1;d=close(part.clean,c,'{','}')
        if not d:continue
        groups=[]
        for left,right in ((a,b),(c,d)):
            declarations=[];pos=left+1
            while len(declarations)<4:
                decl=DECL.match(part.clean,pos,right-1)
                if not decl:break
                declarations.append(decl);pos=decl.end()
            groups.append(declarations)
        by_name=[{m[2]:m for m in g} for g in groups]
        common=[v for v in by_name[0] if v in by_name[1] and by_name[0][v][1].strip()==by_name[1][v][1].strip()]
        edits=[];decls=[]
        for var in common:
            left,right=by_name[0][var],by_name[1][var]
            outside=part.clean[start:a+1]+part.clean[b:c+1]
            if re.search(rf'\b{var}\b',outside) or re.search(rf'&\s*(?:\(\s*)*{var}\b|(?:\.|->)\s*{var}\b',part.clean[a:d]):continue
            # Only the two leading declarations: unknown typedef/shadow declarations reject the name.
            if len(re.findall(rf'\b{TYPE}\s*\*?\s*{var}\s*(?=[=;,\[])',part.clean[a:d]))!=2:continue
            decls.append(left[1].strip()+' '+var+';')
            for m in (left,right):
                edits.append((m.start(),m.end(),'\n'+var+' = '+source[m.start(3):m.end(3)]+';'))
        if edits:
            # C89 builds require every remaining arm declaration before the new assignments.
            selected={m.start() for var in common for m in (by_name[0][var],by_name[1][var])
                      if any(x[0]==m.start() for x in edits)}
            edits=[]
            for group in groups:
                if not group:continue
                declarations=''.join(m[1].strip()+' '+m[2]+';\n' for m in group if m.start() not in selected)
                assignments=''.join(m[2]+' = '+source[m.start(3):m.end(3)]+';\n' for m in group)
                edits.append((group[0].start(),group[-1].end(),'\n'+declarations+assignments))
            result.append(('shared_branch_locals',edit(source,edits+[(start,start,'{\n'+'\n'.join(decls)+'\n'),(d,d,'\n}')])) )
            parent=block_path(part,start)[-1]
            prefix=part.clean[parent+1:start]
            declaration_prefix=rf'(?:\s*{TYPE}\s*\*?\s*{IDENT}\s*(?:=[^;{{}}]*)?;)*\s*'
            parent_end=close(part.clean,parent,'{','}')
            outer=part.clean[parent+1:start]+part.clean[d:parent_end]
            if re.fullmatch(declaration_prefix,prefix) and not any(re.search(rf'\b{v}\b',outer) for v in common):
                result.append(('shared_branch_locals',edit(source,edits+[(start,start,'\n'+'\n'.join(decls)+'\n')])))
                # Try the sibling's declaration/initialization order only for independent expressions.
                selected_names=[m[2] for m in groups[1] if m.start() in selected]
                independent=all(not re.search(r'\b(?:'+ '|'.join(common)+r')\b|\+\+|--|[=,?:]|\b'+IDENT+r'\s*\(',
                                               re.sub(r'\(\s*'+TYPE+r'\s*\*?\s*\)','',m[3])) for g in groups for m in g)
                if len(selected_names)==len(decls) and independent:
                    reordered=[]
                    for group in groups:
                        declarations=''.join(m[1].strip()+' '+m[2]+';\n' for m in group if m.start() not in selected)
                        pool={m[2]:m for m in group}
                        order=[pool[v] for v in selected_names]+[m for m in group if m.start() not in selected]
                        assignments=''.join(m[2]+' = '+source[m.start(3):m.end(3)]+';\n' for m in order)
                        reordered.append((group[0].start(),group[-1].end(),'\n'+declarations+assignments))
                    declarations='\n'.join(by_name[1][v][1].strip()+' '+v+';' for v in selected_names)
                    result.append(('shared_branch_locals',edit(source,reordered+[(start,start,'\n'+declarations+'\n')])))
                    grouped=[]
                    for var in selected_names:
                        typ=by_name[1][var][1].strip();base=typ.rstrip('*').strip();value=('*' if typ.endswith('*') else '')+var
                        if grouped and grouped[-1][0]==base:grouped[-1][1].append(value)
                        else:grouped.append((base,[value]))
                    text='\n'.join(base+' '+', '.join(values)+';' for base,values in grouped)
                    result.append(('shared_branch_locals',edit(source,reordered+[(start,start,text+'\n')])))
    return list(dict.fromkeys(result))[:8]


def loop_values(source,name,header=''):
    part=context(source,name,header)
    flow=analyze(source,name) if part else None
    if not flow or '__repair_exit_value' in source:return []
    out=[]
    for decl in locals_for(part):
        var=decl['name']
        if decl['type'] not in {'u8','u16','unsigned char','unsigned short'} or re.search(rf'&\s*(?:\(\s*)*{var}\b',part.body):continue
        latches=list(re.finditer(rf'\bwhile\s*\(\s*({var})\s*<\s*\d+\s*\)\s*;',part.clean[part.opening:part.end]))
        if not latches or len(latches)>8:continue
        tail=part.opening+latches[-1].end()
        uses=[m for m in re.finditer(rf'\b{var}\b',part.clean[tail:part.end-1])
              if not re.search(r'(?:\.|->)\s*$',part.clean[tail:tail+m.start()])]
        if not uses or re.search(rf'\b{var}\s*(?:=(?!=)|\+\+|--|(?:[+*/&|^-]|<<|>>)=)|&\s*(?:\(\s*)*{var}\b',part.clean[tail:part.end]):continue
        sites={part.opening+m.start(1) for m in latches}
        # Ignore intermediate loop tests whose value is overwritten before the common tail.
        # This keeps the widened pseudo out of unrelated phases of the same branch.
        relevant=set()
        for position in sites:
            node_index=next((i for i,n in enumerate(flow.nodes) if n.start<=position<n.end),None)
            if node_index is None:continue
            todo=list(flow.nodes[node_index].successors);seen=set()
            while todo:
                i=todo.pop()
                if i in seen:continue
                seen.add(i);node=flow.nodes[i];text=part.clean[node.start:node.end]
                if node.start>=tail and var in node.uses:relevant.add(position);break
                if re.search(rf'\b{var}\s*(?:=(?!=)|\+\+|--|(?:[+*/&|^-]|<<|>>)=)|(?:\+\+|--)\s*{var}\b',text):continue
                todo.extend(node.successors)
        sites=relevant
        if not sites:continue
        generators={i for i,n in enumerate(flow.nodes) if any(n.start<=p<n.end for p in sites)}
        # On every reachable path, the last index write must be followed by a selected latch.
        seen=set();todo=[(flow.entry,False)];valid=True
        while todo:
            i,equal=todo.pop()
            if i is None or (i,equal) in seen:continue
            seen.add((i,equal));node=flow.nodes[i];text=part.clean[node.start:node.end]
            if node.start>=tail and var in node.uses and not equal:valid=False;break
            if re.search(rf'\b{var}\s*(?:=(?!=)|\+\+|--|(?:[+*/&|^-]|<<|>>)=)|(?:\+\+|--)\s*{var}\b',text):equal=False
            if i in generators:equal=True
            todo.extend((n,equal) for n in node.successors)
        if not valid:continue
        edits=[(part.opening+1,part.opening+1,'\nunsigned int __repair_exit_value;\n')]
        edits += [(p,p+len(var),'(__repair_exit_value = '+var+')') for p in sites]
        edits += [(tail+m.start(),tail+m.end(),'__repair_exit_value') for m in uses]
        candidate=edit(source,edits)
        # A goto to the immediately following switch tail is equivalent to this switch's break.
        for switch in re.finditer(r'\bswitch\s*\(',part.body):
            condition=close(part.clean,part.opening+switch.end())
            arm=re.match(r'\s*{',part.clean[condition:]) if condition else None
            if not arm:continue
            opening=condition+arm.end()-1;end=close(part.clean,opening,'{','}')
            label=re.match(r'\s*('+IDENT+r')\s*:(?!:)',part.clean[end:]) if end else None
            if not label:continue
            jumps=list(re.finditer(r'\bgoto\s+'+re.escape(label[1])+r'\s*;',part.clean))
            if len(jumps)!=1 or not opening<jumps[0].start()<end:continue
            jump=jumps[0]
            if block_path(part,jump.start())[-1]!=opening or not re.match(r'\s*(?:case\b|default\b|})',part.clean[jump.end():]):continue
            out.append(('loop_exit_value',edit(source,edits+[(jump.start(),jump.end(),'break;'),(end+label.start(),end+label.end(),'')])) )
        out.append(('loop_exit_value',candidate))
    return out[:4]


def float_calls(source,name,header=''):
    part=context(source,name,header)
    if not part or '__repair_float_arg' in source:return []
    # Only known local/parameter float values and float literals; never guess a load/call type.
    signature=re.search(r'\b'+re.escape(name)+r'\s*\(([^;{}]*)\)\s*$',part.clean[:part.opening])
    known={var:part.opening for var in re.findall(r'\b(?:f32|float)\s+('+IDENT+r')\s*(?=[,)])',(signature[1]+',') if signature else '')}
    for declaration in re.finditer(r'\b(?:f32|float)\s+('+IDENT+r')\s*(?=[;=])',part.body):
        pos=part.opening+1+declaration.start();prefix=part.clean[part.opening+1:pos].rstrip()
        if not prefix or prefix[-1] in ';{}':known[declaration[1]]=pos
    for var in list(known):
        declarations=re.findall(r'\b'+TYPE+rf'(?:\s+|\s*\*\s*){var}\s*(?=[=;,)\[])',
                                (signature[1] if signature else '')+';'+part.body)
        if len(declarations)!=1:known.pop(var)
    result=[]
    for hit in re.finditer(rf'(?:^|(?<=[;{{}}]))\s*({IDENT})\s*\(',part.body):
        start=part.opening+1+hit.start();opening=part.opening+hit.end();end=close(part.clean,opening)
        if not end or not re.match(r'\s*;',part.clean[end:]):continue
        args=split_args(part.clean[opening+1:end-1]);edits=[];decls=[];assigns=[]
        for index,(a,b) in enumerate(args):
            a+=opening+1;b+=opening+1
            match=re.fullmatch(r'\s*\(\s*(?:s32|int|u32|unsigned int)\s*\)\s*\((.*)\)\s*',part.clean[a:b],re.S)
            if not match:continue
            expr=match[1];words=set(re.findall(IDENT,re.sub(r'\b\d+(?:\.\d*)?(?:[eE][+-]?\d+)?[fF]\b','',expr)))
            if not words or not words<=set(known) or re.search(r'[^\w\s.+*()/-]|\+\+|--|\b'+IDENT+r'\s*\(',expr):continue
            if any(known[var]>start or (known[var]!=part.opening and block_path(part,start)[:len(block_path(part,known[var]))]!=block_path(part,known[var])) for var in words):continue
            # All numeric literals must have float type; otherwise usual conversions need a real type reader.
            if any(not m[0].lower().endswith('f') for m in re.finditer(r'(?<!\w)\d+(?:\.\d*)?(?:[eE][+-]?\d+)?[fFlL]?',expr)):continue
            fresh='__repair_float_arg'+str(index)
            decls.append('float '+fresh+';');assigns.append(fresh+' = '+source[a+match.start(1):a+match.end(1)]+';')
            edits.append((a+match.start(1),a+match.end(1),fresh))
        if len(edits)>=2:
            # Other arguments must be plain values: staging may not reorder side effects or loads.
            rest=edit(source[opening+1:end-1],[(a-(opening+1),b-(opening+1),v) for a,b,v in edits])
            if re.search(r'\+\+|--|[=\[\].]|->|\b'+IDENT+r'\s*\(',rest):continue
            edits.extend([(start,start,'{\n'+'\n'.join(decls+assigns)+'\n'),(end+re.match(r'\s*;',part.clean[end:]).end(),end+re.match(r'\s*;',part.clean[end:]).end(),'\n}')])
            result.append(('float_call_staging',edit(source,edits)))
            # Fold immediately preceding, dead float copies into the staged expressions.
            # Never remove the declaration, a load, a conversion or a value used elsewhere.
            prefix=part.clean[part.opening+1:start]
            aliases=[];cursor=len(prefix)
            for _ in range(3):
                matches=list(re.finditer(r'\b('+IDENT+r')\s*=\s*(-?\s*'+IDENT+r')\s*;\s*$',prefix[:cursor]))
                if not matches:break
                m=matches[-1];var=m[1];value=m[2].strip();origin=value.lstrip('-').strip()
                if var not in known or origin not in known or var==origin:break
                call_uses=len(re.findall(rf'\b{var}\b',part.clean[opening+1:end-1]))
                declarations=list(re.finditer(rf'\b(?:f32|float)\s+{var}\s*;',part.body))
                if (not call_uses or len(declarations)!=1 or re.search(rf'&\s*(?:\(\s*)*{var}\b',part.body)
                        or re.search(rf'\b{var}\b',part.clean[end:part.end])):break
                declaration=part.opening+1+declarations[0].start()
                path=block_path(part,declaration)
                if declaration>=start or block_path(part,start)[:len(path)]!=path:break
                aliases.append((var,value,part.opening+1+m.start(),part.opening+1+m.end()))
                cursor=m.start()
            if aliases:
                # Rebuild from original offsets rather than editing shifted source positions.
                folded=[]
                for a,b,value in edits:
                    for var,expression,_,_ in aliases:
                        value=re.sub(rf'\b{var}\b',lambda m,expression=expression:'('+expression+')',value)
                    folded.append((a,b,value))
                folded += [(a,b,'') for _,_,a,b in aliases]
                result.insert(0,('float_call_staging',edit(source,folded)))
    return result[:4]


def scalar_chains(source,name,header=''):
    part=context(source,name,header)
    if not part:return []
    result=[]
    for decl in locals_for(part):
        var=decl['name'];typ=decl['type']
        hit=re.search(rf'\b{var}\s*=(?!=)([^;{{}}]+);\s*{var}\s*=\s*('+IDENT+rf')\s*\+\s*{var}\s*;\s*([^;{{}}]+);',part.body)
        if not hit or hit[2]==var:continue
        load,statement=hit[1],hit[3]
        if re.search(rf'\b{var}\b',load):continue
        # Limit duplication to reads in one pure RHS. No stores, calls, assignments or sequencing.
        assignment=re.fullmatch(r'\s*('+IDENT+r')\s*=(?!=)(.*)',statement,re.S)
        if not assignment or assignment[1] in {var,hit[2]}:continue
        rhs=assignment[2]
        if len(re.findall(rf'\b{var}\b',rhs))!=2:continue
        if re.search(r'\+\+|--|[=,?:]|\b'+IDENT+r'\s*\(',load+' '+rhs):continue
        # Only scalar array/explicit dereference reads; reject hidden mutation and narrow temporary casts.
        if typ not in {'int','s32','u32','unsigned int'} or not ('*' in load or '[' in load):continue
        start=part.opening+1+hit.start();finish=part.opening+1+hit.end()
        if re.search(rf'\b{var}\b',part.clean[decl.end():start]+part.clean[finish:part.end]):continue
        expression=f'(({typ})({hit[2]} + ({typ})({load})))'
        replacement=assignment[1]+' = '+re.sub(rf'\b{var}\b',lambda m,expression=expression:expression,rhs)+';'
        result.append(('inline_scalar_chain',edit(source,[(start,finish,replacement)])))
    return result[:2]


def success_pointers(source,name,header=''):
    part=context(source,name,header,pointer_only=True)
    if not part:return []
    pointers=set(re.findall(r'\b'+TYPE+r'\s*\*\s*('+IDENT+r')\s*;',part.body))
    result=[];edits=[]
    pattern=rf'\b({IDENT})\s*=\s*({IDENT})\s*\([^;{{}}]*\);\s*({IDENT})\s*=\s*({IDENT})\s*;\s*if\s*\(\s*\1\s*!=\s*0\s*\)\s*(?:{{\s*)?return\s+\1\s*;'
    for hit in re.finditer(pattern,part.body):
        ptr,other=hit[3],hit[4]
        origins=list(re.finditer(r'\b'+TYPE+rf'(?:\s*\*\s*{other}\s*;|\s+{other}\s*\[)',part.clean))
        if len(origins)!=1:continue
        origin=origins[0].start();position=part.opening+1+hit.start()
        path=block_path(part,origin)
        if origin<=part.opening or origin>=position or block_path(part,position)[:len(path)]!=path:continue
        if ptr not in pointers or ptr==other or re.search(rf'&\s*(?:\(\s*)*{ptr}\b',part.body):continue
        declarations=list(re.finditer(r'\b'+TYPE+rf'\s*\*\s*{ptr}\s*;',part.clean))
        if len(declarations)!=1:continue
        position=part.opening+1+hit.start()
        decl=declarations[0];path=block_path(part,decl.start())
        if decl.end()>position or block_path(part,position)[:len(path)]!=path:continue
        # Plain unbraced error returns only: preserve all control-flow boundaries.
        text=source[part.opening+1+hit.start():part.opening+1+hit.end()]
        if '{' in text:continue
        assignment=re.search(rf'\b{ptr}\s*=\s*{other}\s*;',text)
        replacement=text[:assignment.start()]+text[assignment.end():]+' '+assignment[0]
        edits.append((part.opening+1+hit.start(),part.opening+1+hit.end(),replacement))
        result.append(('success_pointer_update',edit(source,[edits[-1]])))
    if len(edits)>1:result.insert(0,('success_pointer_update',edit(source,edits)))
    return result[:4]


def variants(source,name,header=''):
    return branches(source,name,header)+loop_values(source,name,header)+float_calls(source,name,header)+scalar_chains(source,name,header)+success_pointers(source,name,header)
