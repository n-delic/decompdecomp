"""Bounded control-flow liveness for unescaped scalar matching locals.

Unknown syntax gives no advice. Branches, switch fallthrough, loop back edges and gotos are explicit
edges: a later textual assignment alone never proves a value dead. This does not judge compiled C.
"""
import re
from dataclasses import dataclass, field

from .integer_repair_support import IDENT, locals_for, refs, view
from .pattern_repairs import close

MAX_NODES = 2048
MAX_LOCALS = 256


@dataclass
class Node:
    start: int
    end: int
    uses: set = field(default_factory=set)
    defines: set = field(default_factory=set)
    successors: set = field(default_factory=set)
    live: set = field(default_factory=set)


class Flow:
    def __init__(self, part, declarations=None):
        self.part = part
        self.locals = [decl for decl in (locals_for(part) if declarations is None else declarations)
                       if not re.search(rf'(?<!&)&(?!&)\s*(?:\(\s*)*{decl["name"]}\b', part.body)]
        if len(self.locals) > MAX_LOCALS:
            raise ValueError('too many locals')
        self.names = {d['name'] for d in self.locals}
        self.nodes, self.labels, self.gotos = [], {}, []
        self.entry = self.sequence(part.opening+1, close(part.clean, part.opening, '{', '}')-1, None, None, None, None)
        for index, label in self.gotos:
            if label not in self.labels:
                raise ValueError('unknown label')
            self.nodes[index].successors.add(self.labels[label])
        changed = True
        while changed:
            changed = False
            for node in reversed(self.nodes):
                outgoing = set().union(*(self.nodes[n].live for n in node.successors))
                live = node.uses | (outgoing - node.defines)
                if live != node.live:
                    node.live = live
                    changed = True

    def node(self, start, end, successors=()):
        """Only a plain scalar assignment kills an incoming value; unknown updates stay reads."""
        if len(self.nodes) >= MAX_NODES:
            raise ValueError('too many statements')
        text = self.part.clean[start:end]
        uses = {hit[0] for hit in re.finditer(rf'\b{IDENT}\b', text) if hit[0] in self.names
                and not re.search(r'(?:\.|->)\s*$', text[:hit.start()])}
        defines = set()
        assignment = re.fullmatch(rf'\s*({IDENT})\s*=(?!=)(.*?);?\s*', text, re.S)
        if assignment and assignment[1] in self.names:
            defines.add(assignment[1])
            if not re.search(rf'\b{assignment[1]}\b', assignment[2]):
                uses.discard(assignment[1])
        for decl in self.locals:
            if start <= decl.start() and decl.end() <= end:
                defines.add(decl['name'])
                uses.discard(decl['name'])
        index = len(self.nodes)
        self.nodes.append(Node(start, end, uses, defines, {n for n in successors if n is not None}))
        return index

    def skip(self, pos, end):
        while pos < end and self.part.clean[pos].isspace():
            pos += 1
        return pos

    def condition(self, pos):
        start = self.part.clean.find('(', pos)
        end = close(self.part.clean, start) if start >= 0 else None
        if end is None:
            raise ValueError('unclosed condition')
        return start, end

    def statement_end(self, pos, end):
        """Find complete statement boundaries without mistaking call/loop semicolons for ends."""
        text = self.part.clean
        if text[pos:pos+1] == '{':
            stop = close(text, pos, '{', '}')
            if stop is None:
                raise ValueError('unclosed block')
            return stop
        record = re.match(r'(?:struct|union)(?:\s+'+IDENT+r')?\s*{',text[pos:])
        if record:
            closing = close(text,pos+record.end()-1,'{','}')
            declaration = re.match(r'\s*'+IDENT+r'\s*;',text[closing:]) if closing else None
            if not declaration:
                raise ValueError('unsupported record declaration')
            return closing+declaration.end()
        keyword = re.match(r'(if|while|for|switch|do)\b', text[pos:])
        if keyword:
            kind = keyword[1]
            if kind == 'do':
                body_end = self.statement_end(self.skip(pos+2, end), end)
                if not re.match(r'\s*while\s*\(', text[body_end:]):
                    raise ValueError('missing do latch')
                _, stop = self.condition(body_end)
                stop = self.skip(stop, end)
                if text[stop:stop+1] != ';':
                    raise ValueError('missing do semicolon')
                return stop+1
            _, cond_end = self.condition(pos+len(kind))
            stop = self.statement_end(self.skip(cond_end, end), end)
            if text[stop-1:stop] == ':':
                raise ValueError('unbraced labeled arm')
            if kind == 'if':
                other = self.skip(stop, end)
                if re.match(r'else\b', text[other:]):
                    stop = self.statement_end(self.skip(other+4, end), end)
                    if text[stop-1:stop] == ':':
                        raise ValueError('unbraced labeled else')
            return stop
        label = re.match(rf'(?:case\s+[^:;{{}}]+|default|{IDENT})\s*:(?!:)', text[pos:])
        if label:
            return pos+label.end()
        cursor = pos
        while cursor < end:
            if text[cursor] in '([':
                stop = close(text, cursor, text[cursor], ')' if text[cursor] == '(' else ']')
                if stop is None:
                    raise ValueError('unclosed expression')
                cursor = stop
            elif text[cursor] == ';':
                return cursor+1
            elif text[cursor] in '{}':
                raise ValueError('unsupported expression block')
            else:
                cursor += 1
        raise ValueError('missing statement terminator')

    def sequence(self, start, end, following, breaking, continuing, cases):
        """Build backward so each statement starts with a known normal successor."""
        spans = []
        pos = self.skip(start, end)
        while pos < end:
            stop = self.statement_end(pos, end)
            if stop > end:
                raise ValueError('statement outside block')
            spans.append((pos, stop))
            pos = self.skip(stop, end)
        entry = following
        for start, stop in reversed(spans):
            entry = self.statement(start, stop, entry, breaking, continuing, cases)
        return entry

    def statement(self, start, end, following, breaking, continuing, cases):
        """Connect branch, loop and switch entries; resolve forward gotos after all labels exist."""
        text = self.part.clean
        if text[start] == '{':
            return self.sequence(start+1, end-1, following, breaking, continuing, cases)
        word = re.match(rf'{IDENT}', text[start:])
        kind = word[0] if word else ''
        if kind in {'if', 'while', 'for', 'switch'}:
            opening, stop = self.condition(start+len(kind))
            body = self.skip(stop, end)
            body_end = self.statement_end(body, end)
            if kind == 'if':
                other = self.skip(body_end, end)
                alternate = (self.statement(self.skip(other+4, end), end, following, breaking, continuing, cases)
                             if re.match(r'else\b', text[other:]) else following)
                branch = self.statement(body, body_end, following, breaking, continuing, cases)
                return self.node(opening+1, stop-1, (branch, alternate))
            if kind == 'switch':
                labels = []
                self.statement(body, body_end, following, following, continuing, labels)
                destinations = [index for index, _ in labels]
                if not any(default for _, default in labels):
                    destinations.append(following)
                return self.node(opening+1, stop-1, destinations)
            if kind == 'for':
                semis = [opening+1+m.start() for m in re.finditer(';', text[opening+1:stop-1])]
                if len(semis) != 2:
                    raise ValueError('unsupported for header')
                condition = self.node(semis[0]+1, semis[1])
                step = self.node(semis[1]+1, stop-1, (condition,))
                branch = self.statement(body, body_end, step, following, step, cases)
                self.nodes[condition].successors = {n for n in (branch, following) if n is not None}
                return self.node(opening+1, semis[0], (condition,))
            condition = self.node(opening+1, stop-1)
            branch = self.statement(body, body_end, condition, following, condition, cases)
            self.nodes[condition].successors = {n for n in (branch, following) if n is not None}
            return condition
        if kind == 'do':
            body = self.skip(start+2, end)
            body_end = self.statement_end(body, end)
            opening, stop = self.condition(body_end)
            condition = self.node(opening+1, stop-1)
            branch = self.statement(body, body_end, condition, following, condition, cases)
            self.nodes[condition].successors = {n for n in (branch, following) if n is not None}
            return branch
        label = re.fullmatch(rf'\s*(case\s+[^:;{{}}]+|default|{IDENT})\s*:\s*', text[start:end])
        if label:
            index = self.node(start, end, (following,))
            if label[1] == 'default' or re.match(r'case\b', label[1]):
                if cases is None:
                    raise ValueError('case outside switch')
                cases.append((index, label[1] == 'default'))
            else:
                if label[1] in self.labels:
                    raise ValueError('duplicate label')
                self.labels[label[1]] = index
            self.nodes[index].uses.clear()
            return index
        if kind == 'goto':
            match = re.fullmatch(rf'goto\s+({IDENT})\s*;', text[start:end])
            if not match:
                raise ValueError('computed goto')
            index = self.node(start, end)
            self.gotos.append((index, match[1]))
            self.nodes[index].uses.clear()
            return index
        if kind in {'break', 'continue'}:
            destination = breaking if kind == 'break' else continuing
            return self.node(start, end, (destination,))
        return self.node(start, end, () if kind == 'return' else (following,))

    def at(self, pos):
        return next((node for node in self.nodes if node.start <= pos < node.end), None)

    def dead(self, variable, pos):
        node = self.at(pos)
        return node is not None and variable not in node.live


def analyze(source, name, *, declarations=None):
    """Return conservative liveness, or no advice for extensions and escaped control flow."""
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|setjmp|_setjmp|sigsetjmp|longjmp)\b', part.clean):
        return None
    try:
        return Flow(part, declarations)
    except (ValueError, RecursionError):
        return None


def disjoint(flow, left, right):
    """Two locals may share storage only if neither is live/defined alongside the other."""
    a, b = left['name'], right['name']
    return all(not ({a, b} <= (node.live | node.defines)) for node in flow.nodes)


def hints(source, name):
    """Keep original m2c names; expose possible lifetime groupings without rewriting its draft."""
    flow = analyze(source, name)
    if not flow:
        return []
    active = [decl for decl in flow.locals if refs(flow.part, decl)]
    pairs = [(left['name'], right['name']) for i, left in enumerate(active) for right in active[i+1:]
             if left['type'] == right['type']
             and re.sub(r'_\d+$', '', left['name']) == re.sub(r'_\d+$', '', right['name'])
             and disjoint(flow, left, right)]
    result=[f'{a} / {b}: non-overlapping scalar lifetimes; try a shared local and verify.' for a, b in pairs[:3]]
    if re.search(r'\bif\s*\(',flow.part.body) and re.search(r'\(\s*(?:s16|short)\s*\)',flow.part.body):
        result.append('A branch-specific scalar is narrowed later. If register allocation or call setup is the remaining miss, try separate branch-local lifetimes, then dependency-safe embedded assignments or a typed inlined call/count helper. Keep original m2c names until the normal judge verifies a candidate; this hint does not rewrite the draft.')
    return result
