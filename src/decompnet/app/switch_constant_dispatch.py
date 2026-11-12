"""One bounded switch graph with real constant operands staged at loop entry.

Preserves case effects and the default edge. This is a compiler scheduling
hypothesis, not a match: shared ROM and PROJECT judges still verify every byte.
"""
import re

from decomprepair.integer_repair_support import edit, view
from decomprepair.pattern_repairs import close

from ..forks.csource import _blank

FAMILY = 'switch_constant_dispatch'
PREFIX = '__repair_switch_'
MAX_NODES = 4096


def variants(source, name, header='', *, language=None, int_bits=None):
    if language != 'c' or int_bits != 32 or len(source)+len(header) > 256000:
        return []
    part = view(source, name)
    if not part or PREFIX in source+'\n'+header or re.search(r'\(\s*{', part.body):
        return []
    context = _blank(header+'\n'+source)
    if (re.search(r'\\(?:\r\n?|\n)|\?\?|%:', source+'\n'+header)
            or re.search(r'\b(?:asm|__asm|__asm__|setjmp|longjmp|__builtin_\w+|__attribute__|__declspec|typeof|__typeof__|__auto_type|_Generic|_Pragma|__LINE__|__COUNTER__|__FILE__)\b', context)
            or any(d != 'define' for d in re.findall(r'^\s*#\s*(\w+)', context, re.M))):
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+([A-Za-z_]\w*)', context, re.M))
    if macros & (set(re.findall(r'[A-Za-z_]\w*', part.clean)) | {'unsigned','int','if','goto'}):
        return []
    # pycparser is an optional existing dependency; unsupported contexts give no advice.
    try:
        from pycparser import CParser, c_ast, c_parser
    except ImportError:
        return []
    # Mask comments/directives without changing coordinates or string literals.
    raw = header+'\n'+source
    masked = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: re.sub(r'[^\n]', ' ', m[0]) if m[0].startswith(('/*','//')) else m[0], raw)
    masked = re.sub(r'(?m)^[ \t]*#[^\n]*', lambda m: ' '*len(m[0]), masked)
    try:
        root = CParser().parse(masked)
    except (c_parser.ParseError, ValueError, RecursionError):
        return []
    nodes = []
    def walk(node, ancestors=()):
        nodes.append((node, ancestors))
        if len(nodes) > MAX_NODES:
            raise ValueError('too many syntax nodes')
        for _, child in node.children():
            walk(child, ancestors+(node,))
    try:
        walk(root)
    except (ValueError, RecursionError):
        return []
    functions = [n for n, _ in nodes if isinstance(n, c_ast.FuncDef) and n.decl.name == name]
    if len(functions) != 1:
        return []
    fn = functions[0]
    body_nodes = [(n, parents) for n, parents in nodes if fn in parents]
    # Existing jump entry, scope declarations and unusual transfers are opaque.
    if any(isinstance(n, (c_ast.Goto, c_ast.Label, c_ast.Typedef)) for n, _ in body_nodes):
        return []
    aliases = {n.name:n.type for n in root.ext if isinstance(n, c_ast.Typedef)}
    primitives = {'signed','unsigned','char','short','int','long','_Bool'}
    def integer(typ, seen=()):
        if not isinstance(typ, c_ast.TypeDecl) or typ.quals or not isinstance(typ.type, c_ast.IdentifierType):
            return False
        words = typ.type.names
        if words and set(words) <= primitives:
            return True
        return (len(words) == 1 and words[0] in aliases and words[0] not in seen
                and integer(aliases[words[0]], seen+(words[0],)))
    starts = [0]
    for hit in re.finditer('\n', raw):
        starts.append(hit.end())
    offset = len(header)+1
    def position(node):
        return starts[node.coord.line-1]+node.coord.column-1-offset
    switches = [(n, parents) for n, parents in body_nodes if isinstance(n, c_ast.Switch)]
    for switch, parents in switches:
        if not isinstance(switch.cond, c_ast.ID) or not isinstance(switch.stmt, c_ast.Compound):
            continue
        selector = switch.cond.name
        declarations = [(n, scope) for n, scope in body_nodes if isinstance(n, c_ast.Decl) and n.name == selector]
        if len(declarations) != 1:
            continue
        declaration, scope = declarations[0]
        if not integer(declaration.type) or declaration.storage:
            continue
        parameters = fn.decl.type.args.params if fn.decl.type.args else []
        if declaration not in parameters:
            compounds = [n for n in scope if isinstance(n, c_ast.Compound)]
            # A name in an unrelated inner block does not establish the type of
            # an identically named global used by this switch. For-init scopes
            # are intentionally unsupported rather than treated as the outer block.
            if (not compounds or compounds[-1] not in parents
                    or position(declaration) >= position(switch)
                    or any(isinstance(n, c_ast.DeclList) for n in scope)):
                continue
        if any(isinstance(n, c_ast.UnaryOp) and n.op == '&' and isinstance(n.expr, c_ast.ID)
               and n.expr.name == selector for n, _ in body_nodes):
            continue
        # The switch must be a direct statement in a braced loop body. Staged
        # constants execute only on that loop's entered paths, before existing setup.
        if len(parents) < 2 or not isinstance(parents[-1], c_ast.Compound):
            continue
        loop = parents[-2]
        if not isinstance(loop, (c_ast.For, c_ast.While, c_ast.DoWhile)) or loop.stmt is not parents[-1]:
            continue
        cases = switch.stmt.block_items or []
        if len(cases) != 4 or any(not isinstance(c, c_ast.Case) for c in cases):
            continue
        if any(not isinstance(c.expr, c_ast.Constant) or c.expr.type != 'int' or c.expr.value != str(i)
               for i, c in enumerate(cases)):
            continue
        safe = True
        for case in cases:
            statements = case.stmts or []
            if not statements or not isinstance(statements[-1], c_ast.Break):
                safe = False
                break
            descendants = []
            def collect(n, items=descendants):
                items.append(n)
                for _, child in n.children():
                    collect(child, items)
            for statement in statements[:-1]:
                collect(statement)
            if any(isinstance(n, (c_ast.Decl, c_ast.Typedef, c_ast.Break, c_ast.Continue,
                                  c_ast.Goto, c_ast.Label, c_ast.Return, c_ast.Switch,
                                  c_ast.For, c_ast.While, c_ast.DoWhile, c_ast.Case, c_ast.Default))
                   for n in descendants):
                safe = False
                break
        if not safe:
            continue
        start = position(switch)
        opening = part.clean.find('{', start)
        end = close(part.clean, opening, '{', '}')
        loop_opening = position(loop.stmt)
        loop_opening += len(part.clean[loop_opening:])-len(part.clean[loop_opening:].lstrip())
        if not end or part.clean[loop_opening:loop_opening+1] != '{':
            continue
        # Use the original selector type's value range only for equality; 1..3
        # have the same comparison meaning after C's usual integer promotions.
        one, two, three = (PREFIX+x for x in ('one','two','three'))
        labels = [PREFIX+'case'+str(i) for i in range(4)]
        done = PREFIX+'done'
        indent_match = re.search(r'(?:^|\n)([ \t]*)[^\n]*$', source[:start])
        indent = indent_match[1] if indent_match else ''
        lines = [f'if ({selector} == {one}) goto {labels[1]};',
                 f'if ({selector} == 0) goto {labels[0]};',
                 f'if ({selector} == {two}) goto {labels[2]};',
                 f'if ({selector} == {three}) goto {labels[3]};', f'goto {done};']
        dispatch = ('\n'+indent).join(lines)+'\n'
        for i, case in enumerate(cases):
            colon = part.clean.find(':', position(case))
            last = position(case.stmts[-1])
            dispatch += labels[i]+':'+source[colon+1:last]
            if i != 3:
                dispatch += 'goto '+done+';\n'
        # A C label must precede a statement even if switch is last in its block.
        dispatch += done+':;'
        entry = ('\n'+indent+'unsigned int '+one+', '+two+', '+three+';\n'+indent+
                 one+' = 1;\n'+indent+two+' = 2;\n'+indent+three+' = 3;')
        result = edit(source, [(start,end,dispatch), (loop_opening+1,loop_opening+1,entry)])
        return [(FAMILY,result)]
    return []
