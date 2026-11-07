"""Keep adjacent assignments on their proven original shared physical line.

Old compilers can use line boundaries during scheduling. This preserves an input
formatting choice, not C equivalence or absolute line numbers. Unsupported or
ambiguous groups stay as the native printer emitted them.
"""
import re
from collections import Counter

_COMMENTS = re.compile(r'//[^\n]*|/\*.*?\*/|("(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')', re.S)


def _layout_c(source):
    # Joining a continued directive/string requires preprocessing; leave it alone.
    if '\\\n' in source or '\\\r\n' in source:
        raise ValueError('continued source')
    blank = lambda text: re.sub(r'[^\r\n]', ' ', text)
    source = _COMMENTS.sub(lambda hit: hit[1] or blank(hit[0]), source)
    return re.sub(r'(?m)^[ \t]*#[^\n]*', lambda hit: blank(hit[0]), source)


def _tree(node):
    return (type(node).__name__, tuple((key, repr(getattr(node, key))) for key in node.attr_names),
            tuple((key, _tree(child)) for key, child in node.children()))


def _walk(node):
    yield node
    for _, child in node.children():
        yield from _walk(child)


def _pairs(function):
    for node in _walk(function):
        if type(node).__name__ == 'Compound':
            items = node.block_items or []
            for left, right in zip(items, items[1:]):
                if type(left).__name__ == type(right).__name__ == 'Assignment':
                    yield left, right


def _origin(node):
    coord = node.coord
    return (coord.file, coord.line, coord.column) if coord else None


def _unique_origins(function):
    return Counter(_origin(node) for node in _walk(function)
                   if type(node).__name__ == 'Assignment' and node.coord)


class LineGroups:
    def __init__(self, original, header, name, parse_unit):
        self.parse_unit, self.name, self.groups = parse_unit, name, ()
        self.origins = set()
        try:
            context = original+'\n'+header
            if re.search(r'\b__(?:LINE|COUNTER)__\b|##|(?m:^\s*#\s*include\b)', context):
                raise ValueError('preprocessor-dependent source values')
            self.prefix = _layout_c(header)+'\n'
            self.offset = self.prefix.count('\n')
            pairs = list(_pairs(self._parse(original)))
            counts = Counter((_tree(left), _tree(right)) for left, right in pairs)
            self.groups = tuple(key for left, right in pairs
                                if left.coord and right.coord and left.coord.line == right.coord.line
                                and counts[key := (_tree(left), _tree(right))] == 1)
        except Exception:
            # The native parser's extensions and macros are not all readable here.
            self.groups = ()

    def _parse(self, source):
        ast = self.parse_unit(self.prefix+_layout_c(source))
        functions = [node for node in ast.ext if type(node).__name__ == 'FuncDef'
                     and node.decl.name == self.name]
        if len(functions) != 1:
            raise ValueError('ambiguous function')
        return functions[0]

    def bind_origins(self, function):
        """Bind only proven input pairs before the native AST is randomized.

        Native copies preserve parent coordinates when changing an expression.
        Coordinates identify those parents, never a newly inserted assignment.
        """
        counts = _unique_origins(function)
        pairs = list(_pairs(function))
        keys = Counter((_tree(left), _tree(right)) for left, right in pairs)
        self.origins = {(_origin(left), _origin(right)) for left, right in pairs
                        if (_tree(left), _tree(right)) in self.groups
                        and keys[(_tree(left), _tree(right))] == 1
                        and _origin(left) is not None and _origin(right) is not None
                        and counts[_origin(left)] == counts[_origin(right)] == 1}

    def apply(self, source, function=None):
        if not self.groups:
            return source
        original = source
        try:
            keys = set(self.groups)
            if function is not None and self.origins:
                counts = _unique_origins(function)
                for left, right in _pairs(function):
                    if ((_origin(left), _origin(right)) in self.origins
                            and counts[_origin(left)] == counts[_origin(right)] == 1):
                        keys.add((_tree(left), _tree(right)))
            function = self._parse(source)
            before = _tree(function)
            pairs = {}
            for left, right in _pairs(function):
                pairs.setdefault((_tree(left), _tree(right)), []).append((left, right))
            offsets = [0]
            for line in source.splitlines(keepends=True):
                offsets.append(offsets[-1]+len(line))
            edits = []
            for key in keys:
                hits = pairs.get(key, [])
                if len(hits) != 1:
                    continue
                left, right = hits[0]
                if not left.coord or not right.coord:
                    continue
                start = offsets[left.coord.line-self.offset-1]+left.coord.column-1
                end = offsets[right.coord.line-self.offset-1]+right.coord.column-1
                segment = source[start:end]
                semi = segment.rfind(';')
                # Never erase comments, directives, another statement or any token.
                if semi < 0 or segment[semi+1:].strip():
                    continue
                edits.append((start+semi+1, end))
            for start, end in sorted(edits, reverse=True):
                source = source[:start]+' '+source[end:]
            if _tree(self._parse(source)) == before:
                return source
        except Exception:
            pass
        return original
