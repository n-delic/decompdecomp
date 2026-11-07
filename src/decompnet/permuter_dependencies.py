"""Fail-closed dependency checks for native scalar substitution/reordering.

This small symbolic check supports straight-line ordinary 32-bit scalar C only.
It compares ordered returns and final original locals, including assignment
conversions. Unsupported control, pointers, aliases or effects decline a rewrite;
the normal machine judge remains mandatory, and other passes stay exploratory.
"""
from functools import wraps

GUARDED = frozenset({'perm_split_assignment', 'perm_chain_assignment',
                     'perm_long_chain_assignment', 'perm_duplicate_assignment',
                     'perm_reorder_stmts', 'perm_temp_for_expr'})


class Unknown(ValueError):
    pass


class RejectedRewrite(Exception):
    """Escape the native retry loop: a rejected AST must be discarded completely."""


def signature(fn, typedefs=()):
    types = {}
    values = {}
    declared = set()
    arithmetic = []
    aliases={}
    for decl in typedefs:
        if decl.name in aliases:raise Unknown('Ambiguous alias')
        aliases[decl.name]=decl.type

    def scalar(decl):
        def resolve(node, seen=()):
            if (type(node).__name__ != 'TypeDecl' or node.quals
                    or type(node.type).__name__ != 'IdentifierType'):
                raise Unknown('Qualified, escaped or non-scalar storage')
            words = set(node.type.names)
            if words <= {'signed', 'int'} and words:return 'int'
            if words <= {'unsigned', 'int'} and 'unsigned' in words:return 'unsigned'
            if len(words)==1:
                name=next(iter(words))
                if name in aliases and name not in seen:return resolve(aliases[name],(*seen,name))
            raise Unknown('Unproven scalar width')
        if getattr(decl,'storage',()):raise Unknown('Nonautomatic storage')
        return resolve(decl.type)

    def convert(kind, value):
        return value if value[:2] == ('convert', kind) else ('convert', kind, value)

    def define(decl, parameter=False):
        if not decl.name or decl.name in declared:raise Unknown('Shadowed storage')
        declared.add(decl.name);types[decl.name] = scalar(decl)
        if parameter:values[decl.name] = convert(types[decl.name], ('input', decl.name))
        elif decl.init is not None:assign(decl.name, expression(decl.init))

    def assign(name, value):
        if name not in types:raise Unknown('Nonlocal write')
        value = convert(types[name], value)
        values[name] = value
        return value

    def expression(node):
        kind = type(node).__name__
        if kind == 'ID':
            if node.name not in values:raise Unknown('Uninitialized or nonlocal read')
            return values[node.name]
        if kind == 'Constant' and node.type == 'int':return ('constant', node.value)
        if kind == 'BinaryOp' and node.op in {'+', '-', '*', '&', '|', '^', '==', '!=', '<', '>', '<=', '>='}:
            def pure(part):
                return type(part).__name__ != 'Assignment' and all(pure(c) for _, c in part.children())
            if not pure(node):raise Unknown('Unsequenced operand effects')
            value=('binary', node.op, expression(node.left), expression(node.right))
            if node.op in {'+','-','*'}:arithmetic.append(value)
            return value
        if kind == 'UnaryOp' and node.op in {'+', '-', '~', '!'}:
            value=('unary', node.op, expression(node.expr))
            if node.op=='-':arithmetic.append(value)
            return value
        if kind == 'Assignment' and node.op == '=' and type(node.lvalue).__name__ == 'ID':
            def targets(part):
                out=[part.lvalue.name] if type(part).__name__ == 'Assignment' and type(part.lvalue).__name__ == 'ID' else []
                return out+[n for _,c in part.children() for n in targets(c)]
            writes=targets(node)
            if len(writes)!=len(set(writes)):raise Unknown('Repeated writes in one expression')
            return assign(node.lvalue.name, expression(node.rvalue))
        raise Unknown('Unsupported evaluation or memory effect')

    if type(fn.decl.type).__name__ != 'FuncDecl':raise Unknown('Not a function')
    result_type = scalar(fn.decl.type)  # FuncDecl.type is its return TypeDecl.
    if fn.decl.type.args:
        for decl in fn.decl.type.args.params:define(decl, True)
    returned = []
    finished = False
    for node in fn.body.block_items or []:
        if finished:raise Unknown('Unreachable statements')
        kind = type(node).__name__
        if kind == 'Decl':define(node)
        elif kind == 'Assignment':expression(node)
        elif kind == 'EmptyStatement':continue
        elif kind == 'Return':
            returned.append(convert(result_type, expression(node.expr)));finished = True
        else:raise Unknown('Control or unknown effects')
    # Final state is deliberately conservative: even dead original values must
    # agree. This rejects more than a full liveness proof, never less.
    pending=[*values.values(),*returned,*arithmetic]
    visits=0
    while pending:
        node=pending.pop();visits+=1
        if visits>4096:raise Unknown('Expanded dependency budget')
        pending.extend(v for v in node if isinstance(v,tuple))
    return types, values, returned, set(arithmetic)


def equivalent(before, after, typedefs=()):
    try:
        old_types, old_values, old_returns, old_arithmetic = signature(before,typedefs)
        new_types, new_values, new_returns, new_arithmetic = signature(after,typedefs)
        return (old_returns == new_returns and all(new_types.get(n) == t for n, t in old_types.items())
                and new_arithmetic <= old_arithmetic
                and all(new_values.get(n) == value for n, value in old_values.items())
                and all(n not in new_values for n in old_types if n not in old_values))
    except (Unknown, AttributeError, TypeError, RecursionError):
        return False


def protect(original, failure, blocked=()):
    import copy
    @wraps(original)
    def guarded(fn, ast, indices, region, random):
        def hidden(node):
            return (type(node).__name__ == 'ID' and node.name in blocked
                    or any(hidden(c) for _, c in node.children()))
        if hidden(fn):raise failure()
        before = copy.deepcopy(fn)
        try:result = original(fn, ast, indices, region, random)
        except Exception as error:
            # Native RandomizationFailure is swallowed by its retry loop, even
            # if the pass already changed nodes. Escape to our fresh-parent path.
            raise failure() from error
        if not equivalent(before, fn,[n for n in ast.ext if type(n).__name__=='Typedef'] if ast is not None else ()):
            raise failure()
        return result
    return guarded
