"""Bounded source experiments learned from repair evidence; the exact judge decides every result."""

import re

from . import call_result_repairs, expression_locals, loop_repairs, m2cfix, temporary_repairs, value_repairs
from .csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
TYPE = r'(?:void\s*\*|[su](?:8|16|32)|unsigned\s+(?:char|short|int)|char|short|int)'


def close(text, start, left='(', right=')'):
    depth = 0
    for pos in range(start,len(text)):
        depth += (text[pos] == left) - (text[pos] == right)
        if depth == 0:
            return pos+1
    return None


def negate(condition):
    """Invert a single top-level comparison, retaining nested macro operands."""
    depth = 0
    for match in re.finditer(r'[()]|->|<<|>>|==|!=|<=|>=|<|>|&&|\|\||[=?:;]', condition):
        token = match[0]
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        elif depth == 0:
            if token == '->':
                continue
            if token in ('&&', '||', '<<', '>>', '=', '?', ':', ';'):
                return None
            lhs, rhs = condition[:match.start()].strip(), condition[match.end():].strip()
            if not lhs or not rhs or re.search(r'&&|\|\||==|!=|<=|>=|[<>]', rhs):
                return None
            inverse = {'==':'!=','!=':'==','<=':'>','>=':'<','<':'>=','>':'<='}[token]
            return f'{lhs} {inverse} {rhs}'
    return None


def redundant_jump(body, arm_start, arm_end, branch_end):
    """Only delete a sole terminal jump over closing if/else blocks."""
    jump = re.search(r'\bgoto\s+('+IDENT+r')\s*;\s*}$', body[arm_start:arm_end])
    if not jump:
        return None
    label = jump[1]
    if len(re.findall(r'\b'+re.escape(label)+r'\b', body)) != 2:
        return None
    target = re.search(r'\b'+re.escape(label)+r'\s*:', body[branch_end:])
    if not target:
        return None
    target_start = branch_end+target.start()
    if not re.fullmatch(r'[\s}]*', body[branch_end:target_start]):
        return None
    # Each crossed enclosing block must be an if/else, never a loop or switch.
    stack = []
    safe = set()
    for pos, ch in enumerate(body[:target_start]):
        if ch == '{':
            prefix = body[:pos].rstrip()
            allowed = bool(re.search(r'\belse$', prefix))
            if prefix.endswith(')'):
                depth = 1; start = len(prefix)-2
                while start >= 0 and depth:
                    depth += (prefix[start] == ')') - (prefix[start] == '(')
                    start -= 1
                allowed = bool(re.search(r'\bif\s*$', prefix[:start+1]))
            stack.append((pos, allowed))
        elif ch == '}' and stack:
            _, allowed = stack.pop()
            if allowed:
                safe.add(pos)
    if any(ch == '}' and branch_end+i not in safe for i,ch in enumerate(body[branch_end:target_start])):
        return None
    return [(arm_start+jump.start(), arm_start+jump.start()+jump[0].index(';')+1, ''),
            (target_start, branch_end+target.end(), '')]


def variants(source, name, debug_lines=False):
    span = find(source,name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{',*span)
    if opening < 0 or '#' in clean[opening:span[1]]:
        return []
    body = clean[opening:span[1]]
    result = []
    def add(family, edits):
        text = source
        for a,b,replacement in sorted(edits,reverse=True):
            text = text[:opening+a]+replacement+text[opening+b:]
        if text != source and text not in [v for _,v in result]:
            result.append((family,text))

    # Swap braced branches and negate the comparison; no dangling-else reconstruction.
    branches = []
    for m in list(re.finditer(r'\bif\s*\(',body))[:24]:
        end = close(body,m.end()-1)
        if not end:
            continue
        arm = re.match(r'\s*{',body[end:])
        if not arm:
            continue
        a = end+arm.end()-1; b = close(body,a,'{','}')
        other = re.match(r'\s*else\s*{',body[b:]) if b else None
        if not other:
            continue
        c = b+other.end()-1; d = close(body,c,'{','}')
        cond = body[m.end():end-1]
        newcond = negate(cond)
        if not d or not newcond:
            continue
        edits = [(m.end(),end-1,newcond),(a,b,source[opening+c:opening+d]),(c,d,source[opening+a:opening+b])]
        for arm_start, arm_end in ((a,b),(c,d)):
            cleanup = redundant_jump(body,arm_start,arm_end,d)
            if cleanup:
                add('branch_orientation',cleanup)
                # Remove the jump within its swapped arm; never overlap edit ranges.
                x,y,_ = cleanup[0]
                arms = {a:source[opening+a:opening+b],c:source[opening+c:opening+d]}
                text = arms[arm_start]
                arms[arm_start] = text[:x-arm_start]+text[y-arm_start:]
                add('branch_orientation',[(m.end(),end-1,newcond),(a,b,arms[c]),(c,d,arms[a]),cleanup[1]])
        branches.append((b-a+d-c,edits))
    for _,edits in sorted(branches,key=lambda item:item[0])[:4]:
        add('branch_orientation',edits)

    for candidate in temporary_repairs.variants(source,name):
        if candidate not in [v for _,v in result]:
            result.append(('typed_temporary',candidate))

    # Narrow stores: introduce a same-width local in the existing block rather than retain an inline cast.
    store = re.compile(r'(?P<lhs>\*\s*\(\s*(?P<t>[su](?:8|16)|unsigned\s+(?:char|short))\s*\*\s*\)\s*'+IDENT+r')\s*=\s*(?P<rhs>[^;{}]+);')
    for m in list(store.finditer(body))[:2]:
        rhs = source[opening+m.start('rhs'):opening+m.end('rhs')].strip()
        rhs = re.sub(r'^\(\s*'+re.escape(m['t'])+r'\s*\)\s*','',rhs)
        variable = '__repair_narrow'
        if variable in clean:
            continue
        lhs = source[opening+m.start('lhs'):opening+m.end('lhs')]
        add('typed_temporary',[(m.start(),m.end(),f"{{ {m['t']} {variable} = {rhs}; {lhs} = {variable}; }}")])

    # Hoist a dereferenced argument immediately ahead of a preceding simple store. Never cross
    # another call or control-flow statement, and retain the typed load exactly as submitted.
    call = re.compile(r'(?P<store>\b'+IDENT+r'\s*=\s*'+IDENT+r'\s*;)\s*\b'+IDENT+r'\s*\(')
    for m in list(call.finditer(body))[:2]:
        end = close(body,m.end()-1)
        if not end:
            continue
        expr = source[opening+m.end():opening+end-1].strip()
        typed = re.match(r'\*\s*\(\s*('+TYPE+r')\s*\*\s*\)',expr)
        if not typed or ',' in expr or re.search(r'\+\+|--|\bvolatile\b|\b'+IDENT+r'\s*\(',body[m.end():end-1]):
            continue
        var = '__repair_argument'
        if var in clean:
            continue
        add('typed_temporary',[(1,1,f'\n{typed[1]} {var};\n'),(m.start(),m.start(),f'{var} = {expr};\n'),(m.end(),end-1,var)])
        # Reuse an already declared compatible local when it has not been used yet and is
        # assigned again before its later uses. This also keeps old C89 compilers happy.
        type_pattern = re.escape(typed[1]).replace(r'\ ',r'\s*')
        for decl in re.finditer(type_pattern+rf'\s*({IDENT})\s*;',body[:m.start()]):
            local = decl[1]
            if len(re.findall(r'\b'+re.escape(local)+r'\b',body[:m.start()])) != 1:
                continue
            next_use = re.search(r'\b'+re.escape(local)+r'\b',body[end:])
            if next_use and re.match(r'\s*=',body[end+next_use.end():]):
                add('typed_temporary',[(m.start(),m.start(),f'{local} = {expr};\n'),(m.end(),end-1,local)])
                break

    if debug_lines:
        # Imported build flags are not known here: callers leave this phase off until per-file
        # invocation metadata is available (ROADMAP: debug-line text repairs for imported builds).
        # This text-only phase preserves/debug-label experiments the AST printer cannot represent.
        assignment = re.compile(r'(?m)^[ \t]*('+IDENT+r')\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;[ \t]*\n')
        for m in list(assignment.finditer(body))[:2]:
            suffix = body[m.end():]
            use = re.search(r'(?m)^[ \t]*[^;{}\n]+\*\s*'+re.escape(m[1])+r'\s*;',suffix)
            if use and not re.search(r'[{}]|\b'+re.escape(m[1])+r'\b',suffix[:use.start()]):
                at = m.end()+use.start()
                statement = source[opening+m.start():opening+m.end()].strip()
                add('source_lines',[(m.start(),m.end(),''),(at,at,statement+' ')])
        # Debug line boundaries can affect branch delay-slot scheduling without changing tokens.
        for m in list(re.finditer(r';[ \t]*\n[ \t]*(?=}\s*else\b)',body))[:4]:
            add('source_lines',[(m.start(),m.end(),'; ')])
        # Join/split adjacent statement lines without changing token order.
        for m in list(re.finditer(r';[ \t]*\n[ \t]*(?='+IDENT+r'\s*=)',body))[:2]:
            add('source_lines',[(m.start(),m.end(),'; ')])
    for family,candidate in expression_locals.variants(source,name):
        if candidate not in [v for _,v in result]:
            result.append((family,candidate))
    for canonical in (False,True):
        if candidate := m2cfix.stack_array(source,name,canonical):
            result.append(('stack_array',candidate))
    result += [('loop_induction',candidate) for candidate in loop_repairs.variants(source,name)]
    result += value_repairs.variants(source,name)
    result += [('call_result_inline', candidate) for candidate in call_result_repairs.variants(source,name)]
    return result[:44]
