"""Bounded, coordinated narrow snapshot/producer/branch hypotheses.

A pure inline halfword getter, a word snapshot used exclusively through narrow
casts, and two literal equality guards qualify. Emit the complete recipe directly:
its isolated intermediate edits can lose, so they must not depend on a greedy
score improvement. No effects, reads, predicates or compiler settings are added.
"""
import re

from decomprepair import integer_abi
from decomprepair.integer_repair_support import IDENT, NUMBER, edit, integer, locals_for, refs, view
from decomprepair.pattern_repairs import close

from ..forks.csource import _blank

FAMILY = 'narrow_snapshot_branches'
FAMILIES = (FAMILY,)
LIMIT = 2


def missing_masks(rows):
    """Structured target-only halfword masks are advisory routing, never proof."""
    from .permuter_focus import normalized
    rows = normalized(rows)
    return sum(1 for row in rows if isinstance(row, (tuple,list)) and len(row)==3
               and row[0]=='<' and isinstance(row[1],str)
               and re.fullmatch(r'andi\s+[^,]+,\s*[^,]+,\s*(?:0xffff|65535)', row[1].strip(), re.I))


def diagnosis(rows):
    count = missing_masks(rows)
    return ({'missing_halfword_masks':count, 'preferred_families':[FAMILY,'local_width','inline_scalar_shape'],
             'message':'Try narrow snapshots, direct pure getters and explicit fallback branches together; missing masks alone do not prove the cause.'}
            if count else {})


def _snapshot(part, var, header, arch):
    context = _blank(header+'\n'+part.source)
    decls = [d for d in locals_for(part) if d['name']==var]
    if len(decls)!=1 or integer_abi.scalar(decls[0]['type'],context,arch) not in {(32,False),(16,False)}:return None
    decl = decls[0]
    assignment = re.compile(r'\b'+re.escape(var)+r'\s*=\s*('+IDENT+r')\s*\(\s*('+IDENT+r')\s*\)\s*;')
    assignments = list(assignment.finditer(part.clean, part.opening+1, part.end))
    if len(assignments)!=1:return None
    assign = assignments[0]
    getter, actual = assign[1],assign[2]
    pattern = (r'\b((?:(?:static|inline|__inline__)\s+)+)(u16|unsigned\s+short)\s+'
               +re.escape(getter)+r'\s*\(\s*((?:struct\s+)?'+IDENT+r')\s*\*\s*('+IDENT+r')\s*\)\s*{\s*return\s+\4\s*->\s*('+IDENT+r')\s*;\s*}')
    definitions = list(re.finditer(pattern, part.clean[:part.opening]))
    if len(definitions)!=1:return None
    definition = definitions[0]
    if 'static' not in definition[1].split() or not {'inline','__inline__'}.intersection(definition[1].split()):return None
    if integer_abi.scalar(definition[2],context,arch)!=(16,False):return None
    # No conversion to an unrelated pointer type or shadowed formal/callee.
    pointer_decl = r'\b'+re.escape(definition[3])+r'\s*\*\s*'+re.escape(actual)+r'\s*(?=[;=,])'
    if len(re.findall(pointer_decl, part.body))!=1:return None
    if re.search(r'\b'+IDENT+r'\s+\**\s*'+re.escape(getter)+r'\b',part.body):return None
    for ref in refs(part,decl):
        if ref.start()==assign.start():continue
        if ref.start()<assign.end():return None
        # Every other use must explicitly consume no more than the low halfword.
        if not re.search(r'\(\s*(?:u8|s8|unsigned\s+char|signed\s+char|char|u16|s16|unsigned\s+short|short)\s*\)\s*$',part.clean[:ref.start()]):return None
    return [(decl.start('type'),decl.end('type'),'unsigned short'),
            (assign.start(),assign.end(),var+' = (unsigned short)('+actual+'->'+definition[5]+');')]


def variants(source, name, header='', arch=None):
    part = view(source,name)
    context = _blank(header+'\n'+source)
    if (not part or len(source)>128000 or re.search(r'\b(?:volatile|restrict|asm|__asm__|setjmp|longjmp|__LINE__|__FILE__|__COUNTER__)\b',source)):
        return []
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')',context))
    if macros.intersection(re.findall(IDENT,part.body)):return []
    # Match only an entire two-equality conjunction, with parentheses allowed
    # around each cast expression. No short-circuit side effects can be moved.
    expression = r'\(*\s*\(\s*(?:u16|unsigned\s+short)\s*\)\s*('+IDENT+r')\s*\)*\s*==\s*('+NUMBER+r')\s*\)*'
    guard = re.compile(r'\bif\s*\(\s*'+expression+r'\s*&&\s*'+expression+r'\s*\)\s*{')
    result = []
    for hit in guard.finditer(part.clean,part.opening+1,part.end):
        a,ka,b,kb = hit[1],hit[2],hit[3],hit[4]
        if a==b or not all(0<=integer(k)<=65535 for k in (ka,kb)):continue
        left,right = _snapshot(part,a,header,arch),_snapshot(part,b,header,arch)
        if not left or not right:continue
        opening = hit.end()-1
        end = close(part.clean,opening,'{','}')
        if not end:continue
        otherwise = re.match(r'\s*else\s*{',part.clean[end:])
        if not otherwise:continue
        other_open = end+otherwise.end()-1
        stop = close(part.clean,other_open,'{','}')
        if not stop or re.search(r'\bgoto\b|\bcase\b|\bdefault\s*:|\b'+IDENT+r'\s*:(?!:)',part.clean[opening:stop]):continue
        index = 0
        while re.search(r'\b__repair_snapshot_'+str(index)+r'_(?:fallback|done)\b',context):index+=1
        fail,done = (f'__repair_snapshot_{index}_fallback',f'__repair_snapshot_{index}_done')
        replacement = (f'if ((unsigned short){a} != {ka}) goto {fail};\n'
                       f'        if ((unsigned short){b} != {kb}) goto {fail};\n'
                       +source[opening:end]+f'\n        goto {done};\n{fail}:\n'
                       +source[other_open:stop]+f'\n{done}:;')
        result.append((FAMILY,edit(source,left+right+[(hit.start(),stop,replacement)])))
        if len(result)>=LIMIT:break
    return result
