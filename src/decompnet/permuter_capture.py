"""Conservative operand stability for the pinned native expression expander.

Run inside its isolated Python, without importing the app or modifying the tool.
Only straight-line reads of pure, unescaped local captures are eligible. This is
not an equivalence proof for the permuter's other transformations.
"""
import bisect
from functools import wraps
from types import FunctionType


def _walk(node, parents=()):
    yield node, parents
    for _, child in node.children():
        yield from _walk(child, (*parents, node))


def stable_reads(fn, indices, writes, reads, blocked=(), typedefs=()):
    """Filter read locations before the expander selects or removes a capture."""
    nodes = list(_walk(fn))
    by_index = {indices.starts[n]: n for n, _ in nodes if n in indices.starts}
    contexts = {id(n): tuple(id(p) for p in parents if type(p).__name__ in {
        'Compound', 'If', 'For', 'While', 'DoWhile', 'Switch', 'Case', 'Default', 'TernaryOp'
    }) for n, parents in nodes}
    ancestry = {id(n): parents for n, parents in nodes}
    declarations = {}
    for node, _ in nodes:
        if type(node).__name__ == 'Decl' and node.name:
            declarations.setdefault(node.name, []).append(node)
    escaped = {n.expr.name for n, _ in nodes if type(n).__name__ == 'UnaryOp'
               and n.op == '&' and type(n.expr).__name__ == 'ID'}
    aliases = {}
    # A shadowed alias may differ between blocks. Conservatively retain every
    # definition rather than treating a later nonvolatile alias as proof.
    for node in [*typedefs, *(n for n, _ in nodes if type(n).__name__ == 'Typedef')]:
        aliases.setdefault(node.name, []).append(node.type)

    def qualified(node, seen=frozenset()):
        if set(getattr(node, 'quals', ())) & {'volatile', '_Atomic'}:
            return True
        if type(node).__name__ == 'IdentifierType':
            return any(name not in seen and qualified(alias, seen | {name})
                       for name in node.names for alias in aliases.get(name, ()))
        return any(qualified(child, seen) for _, child in node.children())

    locals_ = {name for name, decls in declarations.items() if len(decls) == 1
               and name not in escaped and name not in blocked and not qualified(decls[0])
               and not set(decls[0].storage) & {'static', 'extern', '_Thread_local'}}
    labels = [indices.starts[n] for n, _ in nodes if type(n).__name__ == 'Label']
    result = {}
    for variable, locations in reads.items():
        if variable not in locals_:
            continue
        candidates = []
        for location in locations:
            prior = writes.get(variable, ())
            before = bisect.bisect_left(prior, location)
            if not before:
                continue
            capture = prior[before-1]
            assignment = by_index.get(capture)
            kind = type(assignment).__name__
            expression = (assignment.init if kind == 'Decl' else assignment.rvalue
                          if kind == 'Assignment' and assignment.op == '=' else None)
            if expression is None:
                continue
            parts = [n for n, _ in _walk(expression)]
            # Do not move a load, call, side effect, or an unknown/global operand.
            if qualified(expression) or any(type(n).__name__ in {'ArrayRef', 'StructRef', 'FuncCall', 'Assignment', 'InitList'}
                   or (type(n).__name__ == 'Constant' and n.type == 'string')
                   or (type(n).__name__ == 'UnaryOp' and n.op in {'*', '&', 'p++', 'p--', '++', '--', 'sizeof'})
                   for n in parts):
                continue
            dependencies = {n.name for n in parts if type(n).__name__ == 'ID'}
            if variable in dependencies or not dependencies <= locals_:
                continue
            if contexts[id(assignment)] != contexts.get(id(by_index.get(location))):
                continue
            parents = ancestry[id(by_index[location])]
            controls = [i for i, parent in enumerate(parents) if type(parent).__name__ in {
                'Compound', 'If', 'For', 'While', 'DoWhile', 'Switch', 'Case', 'Default', 'TernaryOp'}]
            start = controls[-1]+1 if controls else 0
            statement = parents[start] if start < len(parents) else by_index[location]
            horizon = indices.ends[statement]
            if any(capture < label < location for label in labels):
                continue
            if any(any(capture < write < horizon for write in writes.get(dep, ())) for dep in dependencies):
                continue
            candidates.append(location)
        if candidates:
            result[variable] = candidates
    return result


def protect_expand(original, blocked=(), typedefs=()):
    """Filter this method only; keep a capture when some of its reads are unsafe."""
    original_namespace = original.__globals__
    finder = original_namespace['compute_read_locations']
    writer = original_namespace['compute_write_locations']

    @wraps(original)
    def guarded(fn, ast, indices, region, random):
        reads = finder(fn, indices)
        allowed = stable_reads(fn, indices, writer(fn, indices), reads, blocked,
                               typedefs() if callable(typedefs) else typedefs)
        full = {name: locations for name, locations in allowed.items() if locations == reads[name]}
        partial = {name: locations for name, locations in allowed.items() if locations != reads[name]}
        counts = [sum(region.contains_pre_index(i) for locations in group.values() for i in locations)
                  for group in (full, partial)]
        keep = bool(counts[1] and random.randrange(sum(counts)) >= counts[0])
        selected = partial if keep else full
        namespace = dict(original_namespace)
        namespace['compute_read_locations'] = lambda fn, indices: selected
        if keep:
            # The upstream method otherwise thinks omitted unsafe reads do not exist
            # and may delete the initializer still needed by those reads.
            namespace['PROB_KEEP_REPLACED_VAR'] = 1.0
        method = FunctionType(original.__code__, namespace, original.__name__,
                              original.__defaults__, original.__closure__)
        return method(fn, ast, indices, region, random)

    return guarded
