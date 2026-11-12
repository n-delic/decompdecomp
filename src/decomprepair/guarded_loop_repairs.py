"""Small joint loop/profile hypotheses for old MIPS GCC; every candidate needs the byte judge."""
import re

from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

FAMILY = 'loop_bounds_profile'


def sources(source, name, header=''):
    """Keep a corrected bounds exit even when its isolated score is worse.

    Only recognize a pure named comparison and a terminal return of its bound. Gotos
    to the first outer-body label are drafting hypotheses, not equivalent rewrites.
    Opaque macros, qualified storage and more complicated conditions stay untouched.
    """
    part = view(source, name)
    if not part or len(source)>128000 or re.search(r'\b(?:volatile|asm|__asm__)\b|(?m:^\s*#)', source):
        return []
    part.end=len(part.clean[:part.end].rstrip())
    condition = rf'\(\s*{{\s*(?P<type>int|s32)\s+(?P<temp>{IDENT})\s*=\s*(?P<left>{IDENT})\s*<\s*(?P<bound>{IDENT})\s*;\s*(?P=temp)\s*;\s*}}\s*\)'
    matches = list(re.finditer(r'\bwhile\s*\(\s*'+condition+r'\s*\)\s*{',part.body))
    if len(matches)!=1:return []
    hit=matches[0];start=part.opening+1+hit.start();opening=part.opening+hit.end()
    end=close(part.clean,opening,'{','}')
    if not end or not re.fullmatch(r'\s*return\s+'+re.escape(hit['bound'])+r'\s*;\s*',part.clean[end:part.end-1]):return []
    left,bound,temp=hit['left'],hit['bound'],hit['temp']
    if len({left,bound,temp})!=3:return []
    definition=re.search(r'\b'+re.escape(name)+r'\s*\(([^{};]*)\)\s*$',part.clean[:part.opening])
    if not definition:return []
    signature=definition[0]
    if not all(re.search(r'\bchar\s*\*\s*'+re.escape(n)+r'\s*[,)]',signature) for n in (left,bound)):return []
    body=part.clean[opening+1:end-1]
    # The bound is stable, and neither operand is hidden behind a source/header macro.
    if re.search(rf'\b{bound}\s*(?:=(?!=)|[+\-*/&|^]=|\+\+|--)|&\s*{bound}\b',body):return []
    if re.search(rf'(?m)^\s*#\s*define\s+(?:{left}|{bound}|{temp}|{hit['type']}|while|if|return|break|goto)\b',source+'\n'+header):return []
    if re.search(r'typedef[^;]*\bvolatile\b[^;]*\b'+re.escape(hit['type'])+r'\s*;',header):return []
    label=re.match(r'\s*('+IDENT+r')\s*:',body)
    corrected=source
    if label:
        if any(m[1]!=label[1] for m in re.finditer(r'\bgoto\s+('+IDENT+r')\s*;',body)):return []
        jumps=list(re.finditer(r'\bgoto\s+'+re.escape(label[1])+r'\s*;',body))
        if jumps:
            if len(jumps)!=1:return []
            jump=opening+1+jumps[0].start()
            # A break must leave an inner while, never a switch/for/do or the outer loop.
            owners=[]
            for control in re.finditer(r'\b(while|for|switch)\s*\(',part.clean[opening+1:jump]):
                at=opening+1+control.end()-1;stop=close(part.clean,at)
                brace=re.match(r'\s*{',part.clean[stop:]) if stop else None
                if brace:
                    begin=stop+brace.end()-1;finish=close(part.clean,begin,'{','}')
                    if finish and begin<jump<finish:owners.append((begin,control[1]))
            if not owners or max(owners)[1]!='while' or re.search(r'\b(?:do|for|switch)\b',part.clean[max(owners)[0]:jump]):return []
            prefix=part.clean[opening+1:jump]
            if not re.search(r'if\s*\(\s*!\s*\(\s*'+re.escape(left)+r'\s*<\s*'+re.escape(bound)+r'\s*\)\s*\)\s*$',prefix):return []
            corrected=edit(source,[(jump,jump+len(jumps[0][0]),'break;')])
    # Keep original newlines: source layout can affect old compiler scheduling.
    guard=f"while (({{ {hit['type']} {temp}={left}<{bound}; if (!{temp}) return {bound}; {temp}; }})) "
    guarded=corrected[:start]+guard+corrected[opening:]
    return list(dict.fromkeys(s for s in (guarded,corrected) if s!=source))


def probes(proj, fn, own, header=''):
    """Three paired probes maximum, inside the caller's ordinary budget and durable cache."""
    if dict(proj).get('arch')!='mips' or not own.get('toolchain','').startswith('gcc-2.'):
        return []
    count=dict(fn).get('n_insns') or 0
    if not count or not 0<(fn['best_score'] or 0)<=count*10:return []
    bodies=sources(fn['best_source'],fn['name'],header)
    if not bodies:return []
    flags=own['flags'].split() if isinstance(own['flags'],str) else list(own['flags'])
    disabled=[f for f in flags if f not in ('-fthread-jumps','-fno-thread-jumps')]+['-fno-thread-jumps']
    return list(dict.fromkeys((tuple(f),s) for f,s in
                [(disabled,s) for s in bodies]+[(flags,bodies[0])]))[:3]


def hints(source, name, rows=None):
    result=[]
    candidates=sources(source,name)
    if candidates and re.search(r'\bgoto\b',source):
        result.append('A bounds-failure goto targets the outer loop body and can bypass its condition. '
                      'Check the target backedge before trusting the lower diff score; keep a corrected exit as a separate hypothesis.')
    for index,row in enumerate(rows or []):
        if len(row)!=3:continue
        a,b=row[1],row[2]
        branch=re.compile(r'\b(beqz|bnez|beq|bne)\s+(.+?),\s*(0x[0-9a-fA-F]+)\s*$')
        left,right=branch.search(a),branch.search(b)
        following=(rows or [])[index+1:index+2]
        if (left and right and left.groups()[:2]==right.groups()[:2] and left[3]!=right[3]
                and following and len(following[0])==3
                and 'sltu' in following[0][1] and re.search(r'\bnop\b',following[0][2])):
            result.append('Loop backedge destination and delay-slot comparison differ: check whether the branch returns to the bounds test or skips into its body. Retain a corrected inner-loop exit even if its isolated diff score is worse.')
        ordinary=lambda s:bool(re.search(r'\bbeqz?\b',s))
        likely=lambda s:bool(re.search(r'\bbeq(?:z)?l\b',s))
        if ordinary(a) and likely(b) or likely(a) and ordinary(b):
            result.append('Ordinary versus branch-likely mismatch: the delay slot is annulled on one path. '
                          'Inspect its liveness and the loop condition/body destinations; try an explicit outer return guard with a function-only jump-threading profile. Opcode similarity is not a verdict.')
            break
    return result
