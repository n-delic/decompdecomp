"""Discarded unsigned byte increments with a bounded CFG and conservative read grammar.

A cached byte may cross ordinary reads and branches, never an intervening write/call.
Unknown declarations, expressions, macros or control flow give no advice. No ABI or
header changes are proposed; the ordinary compiler/link judge still decides matches.
"""
import re

from . import integer_abi, local_flow
from .csource import _blank
from .integer_repair_support import IDENT, NUMBER, edit, locals_for, view
from .pattern_repairs import close

FAMILY = 'unsigned_byte_increment'
FAMILIES = (FAMILY,)
LIMIT = 2
MAX_NODES = 256
TYPE = rf'{IDENT}(?:\s+{IDENT})*'


def number(text):
    text = re.sub(r'[uUlL]+$', '', text)
    try:
        return int(text, 16 if text.lower().startswith('0x') else 8 if len(text) > 1 and text.startswith('0') else 10)
    except ValueError:
        return None


def strip(text):
    text = text.strip()
    while text.startswith('(') and close(text, 0) == len(text):
        text = text[1:-1].strip()
    return text


def cast(text):
    text = strip(text)
    if not text.startswith('('):
        return None
    end = close(text, 0)
    if end and end < len(text) and re.fullmatch(rf'{TYPE}\s*\*?', text[1:end-1].strip()):
        return text[1:end-1].strip(), strip(text[end:])
    return None


def binary(text, operators):
    """Split only a top-level binary operator; parentheses and brackets are indivisible."""
    pos = 0
    while pos < len(text):
        if text[pos] in '([':
            stop = close(text, pos, text[pos], ')' if text[pos] == '(' else ']')
            if not stop:
                return None
            pos = stop
            continue
        for op in operators:
            if text.startswith(op, pos) and text[:pos].strip() and text[pos+len(op):].strip():
                return text[:pos], op, text[pos+len(op):]
        pos += 1
    return None


class Reads:
    """Only explicit ordinary integer loads through uniquely declared stable pointer roots."""
    def __init__(self, part, context, arch, declarations):
        self.part, self.context, self.arch = part, context, arch
        self.scalars = {d['name']: d['type'] for d in declarations}
        self.roots = {}
        signature = re.search(r'\b'+IDENT+r'\s*\(([^{};]*)\)\s*$', part.clean[:part.opening])
        self.params = signature[1] if signature else ''
        for param in self.params.split(','):
            match = re.fullmatch(rf'\s*({TYPE})\s+({IDENT})\s*', param)
            if (match and integer_abi.scalar(match[1], context, arch)
                    and not re.search(rf'(?<!&)&(?!&)\s*(?:\(\s*)*{re.escape(match[2])}\b', part.body)
                    and not re.search(rf'\b{IDENT}(?:\s+\**\s*|\s*\*+\s*){re.escape(match[2])}\s*(?=[;=,\[])', part.body)):
                self.scalars[match[2]] = match[1]

    def scalar(self, spelling):
        return integer_abi.scalar(spelling, self.context, self.arch)

    def root(self, name):
        if name in self.roots:
            return self.roots[name]
        # Local pointer aliases, shadows and escaped pointer objects are deliberately unsupported.
        if re.search(rf'\b{IDENT}(?:\s+\**\s*|\s*\*+\s*){re.escape(name)}\b', self.part.body) or re.search(
                rf'(?<!&)&(?!&)\s*(?:\(\s*)*{re.escape(name)}\b', self.part.body):
            return None
        # Look at every declaration spelling, including conflicting/qualified redeclarations.
        declarations = re.findall(rf'\b((?:{IDENT}\s+)*{IDENT}\s*\*+\s*(?:volatile\s+|const\s+|_Atomic\s+)*){re.escape(name)}\s*(?=[;,)=])', self.context)
        declarations += re.findall(rf'\b((?:{IDENT}\s+)*{IDENT}\s*\*+\s*){re.escape(name)}\s*$', self.params)
        types = []
        for decl in declarations:
            typ = re.sub(r'^extern\s+', '', decl.strip())
            match = re.fullmatch(rf'({TYPE})\s*\*', typ)
            if not match or re.search(r'\b(?:volatile|const|_Atomic)\b', typ):
                return None
            pointee = match[1].strip()
            if pointee not in {'void', 'char'} and not self.scalar(pointee) and not self.record(pointee):
                return None
            types.append(pointee)
        # Catch non-pointer declarations of the same name (including typedef shadows).
        if re.search(rf'\b{IDENT}\s+{re.escape(name)}\s*(?=[;=,\[)])', self.context):
            return None
        if not types or len(set(types)) != 1:
            return None
        self.roots[name] = types[0]
        return types[0]

    def record(self, typ):
        if re.search(r'^\s*#\s*define\s+'+re.escape(typ)+r'\b', self.context, re.M):
            return None
        bodies = re.findall(r'\btypedef\s+struct(?:\s+'+IDENT+r')?\s*{([^{}]*)}\s*'+re.escape(typ)+r'\s*;', self.context, re.S)
        return bodies[0] if len(bodies) == 1 and not re.search(r'\b(?:volatile|_Atomic)\b', bodies[0]) else None

    def address(self, text):
        text = strip(text)
        pair = binary(text, ('+',))
        if pair:
            left, _, right = pair
            base = self.address(left)
            if not base or base[2] != 1 or not re.fullmatch(NUMBER, strip(right)):
                return None
            offset = number(strip(right))
            if offset is None:
                return None
            return base[0], base[1]+offset, 1
        converted = cast(text)
        if converted:
            typ, value = converted
            if not typ.endswith('*'):
                return None
            pointee = typ[:-1].strip()
            width = self.scalar(pointee)
            if pointee != 'char' and width not in {(8, True), (8, False)}:
                return None
            base = self.address(value)
            return (base[0], base[1], 1) if base else None
        if re.fullmatch(IDENT, text):
            typ = self.root(text)
            if typ:
                width = self.scalar(typ)
                return text, 0, 1 if typ == 'char' or width in {(8, True), (8, False)} else None
        return None

    def load(self, text):
        text = strip(text)
        if text.startswith('*'):
            converted = cast(text[1:])
            if converted and converted[0].endswith('*'):
                typ, value = converted
                width = self.scalar(typ[:-1].strip())
                base = self.address(value)
                if width and base:
                    return width, base[:2]
        # Constant index into a proven byte pointer, optionally explicitly cast.
        for hit in re.finditer(r'\[', text):
            start = hit.start()
            if close(text, start, '[', ']') != len(text):
                continue
            base = self.address(text[:start])
            index = strip(text[start+1:-1])
            if not base or base[2] != 1 or not re.fullmatch(NUMBER, index):
                continue
            converted = cast(text[:start])
            typ = converted[0][:-1].strip() if converted else self.root(strip(text[:start]))
            width = self.scalar(typ) if typ else None
            if width in {(8, True), (8, False)}:
                offset = number(index)
                if offset is not None:
                    return width, (base[0], base[1]+offset)
        return None

    def expression(self, text, depth=0):
        if depth > 24:
            return False
        text = strip(text)
        if re.fullmatch(NUMBER, text):
            return True
        if re.fullmatch(IDENT, text):
            return text in self.scalars and self.scalar(self.scalars[text]) is not None
        if self.load(text):
            return True
        member = re.fullmatch(rf'({IDENT})\s*->\s*({IDENT})', text)
        if member:
            typ = self.root(member[1])
            body = self.record(typ) if typ else None
            fields = re.findall(rf'(?:^|;)\s*({TYPE})\s+{re.escape(member[2])}\s*;', body or '')
            return len(fields) == 1 and self.scalar(fields[0]) is not None
        converted = cast(text)
        if converted and self.scalar(converted[0]):
            return self.expression(converted[1], depth+1)
        for operators in (('||',), ('&&',), ('>=', '<=', '==', '!=', '>', '<'), ('+', '-')):
            pair = binary(text, operators)
            if pair:
                return self.expression(pair[0], depth+1) and self.expression(pair[2], depth+1)
        return False


def reachable(flow, start, blocked=None):
    seen, pending = set(), [start]
    while pending:
        index = pending.pop()
        if index is None or index == blocked or index in seen:
            continue
        seen.add(index)
        pending.extend(flow.nodes[index].successors)
    return seen


def variants(source, name, header='', arch=None):
    part = view(source, name)
    context = _blank(header+'\n'+source)
    if re.search(r'^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif|include)\b', context, re.M):
        return []
    if not part or arch not in integer_abi.ARCHES or re.search(
            r'\b(?:volatile|_Atomic|asm|__asm__|setjmp|longjmp|for|while|do)\b', part.body):
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', context, re.M))
    if macros & (set(re.findall(IDENT, part.body)) | {'unsigned', 'char'}):
        return []
    declarations = locals_for(part)
    # Any opaque or qualified local shadow makes name/type resolution uncertain.
    if any(len(re.findall(rf'\b(?!(?:return|goto|case)\b){IDENT}(?:\s+\**\s*|\s*\*+\s*){re.escape(d["name"])}\s*(?=[;=,\[])', part.body)) != 1 for d in declarations):
        return []
    try:
        flow = local_flow.Flow(part, declarations)
    except (ValueError, RecursionError):
        return []
    if len(flow.nodes) > MAX_NODES:
        return []
    active = reachable(flow, flow.entry)
    # Reject all cycles, including goto back edges; no textual skip-over approximation.
    def cycle(index, visiting, done):
        if index in visiting:
            return True
        if index in done:
            return False
        visiting.add(index)
        if any(cycle(n, visiting, done) for n in flow.nodes[index].successors):
            return True
        visiting.remove(index)
        done.add(index)
        return False
    try:
        if flow.entry is not None and cycle(flow.entry, set(), set()):
            return []
    except RecursionError:
        return []
    reads = Reads(part, context, arch, flow.locals)
    for store_index in sorted(active, key=lambda i: flow.nodes[i].start):
        store = flow.nodes[store_index]
        statement = re.fullmatch(r'\s*(.+?)\s*=(?!=)\s*(.+);\s*', part.clean[store.start:store.end], re.S)
        if not statement:
            continue
        dest = reads.load(statement[1])
        if not dest or dest[0] not in {(8, True), (8, False)}:
            continue
        rhs = strip(statement[2])
        converted = cast(rhs)
        if converted:
            if reads.scalar(converted[0]) not in {(8, True), (8, False)}:
                continue
            rhs = converted[1]
        addition = binary(strip(rhs), ('+',))
        if not addition or strip(addition[2]) != '1':
            continue
        value = strip(addition[0])
        direct = reads.load(value)
        if direct:
            if direct[1] != dest[1] or direct[0] not in {(8, True), (8, False)}:
                continue
        else:
            if value not in reads.scalars or reads.scalar(reads.scalars[value]) not in {(8, True), (8, False)}:
                continue
            # A unique plain reaching assignment, with its address never escaped.
            definitions = [i for i in active if value in flow.nodes[i].defines and not any(
                d['name'] == value and flow.nodes[i].start <= d.start() < flow.nodes[i].end for d in declarations)]
            if len(definitions) != 1:
                continue
            load_index = definitions[0]
            load = flow.nodes[load_index]
            assignment = re.fullmatch(re.escape(value)+r'\s*=\s*(.+);', part.clean[load.start:load.end], re.S)
            loaded = reads.load(assignment[1]) if assignment else None
            if (not loaded or loaded[1] != dest[1] or loaded[0] != reads.scalar(reads.scalars[value])
                    or store_index in reachable(flow, flow.entry, blocked=load_index)):
                continue
            # Exactly the nodes lying on a load-to-store path require the no-effect proof.
            after = reachable(flow, load_index)
            corridor = {i for i in after if store_index in reachable(flow, i)} - {load_index, store_index}
            safe = True
            for index in corridor:
                text = part.clean[flow.nodes[index].start:flow.nodes[index].end].strip()
                if re.fullmatch(rf'goto\s+{IDENT}\s*;|{IDENT}\s*:', text):
                    continue
                local = re.fullmatch(rf'({IDENT})\s*=\s*(.+);', text, re.S)
                if local:
                    if local[1] == value or local[1] not in reads.scalars:
                        safe = False
                        break
                    text = local[2]
                if not reads.expression(text):
                    safe = False
                    break
            if not safe:
                continue
        root, offset = dest[1]
        unsigned = f'((unsigned char *){root})[{offset}]'
        return [(FAMILY, edit(source, [(store.start, store.end, f'{unsigned}{op};' if op == '++' else f'++{unsigned};')]))
                for op in ('prefix', '++')]
    return []
