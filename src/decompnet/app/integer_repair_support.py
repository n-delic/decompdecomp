"""Small, conservative C spans shared by integer repair hypotheses, not a replacement C parser."""
import re
from dataclasses import dataclass

from ..forks.csource import _blank, find
from .pattern_repairs import close

IDENT = r'[A-Za-z_]\w*'
INTEGER = r'(?:unsigned\s+(?:char|short|int)|signed\s+(?:char|short|int)|[su](?:8|16|32)|char|short|int)'
DECL = re.compile(rf'\b(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*;')
NUMBER = r'(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*'


@dataclass
class View:
    source: str
    clean: str
    opening: int
    end: int

    @property
    def body(self):
        return self.clean[self.opening+1:self.end-1]


def view(source, name):
    span = find(source, name)
    if not span:
        return None
    clean = _blank(source)
    definition = re.search(r'\b'+re.escape(name)+r'\s*\([^{};]*\)\s*{',clean[span[0]:span[1]])
    opening = span[0]+definition.end()-1 if definition else -1
    if opening < 0 or '#' in clean[opening:span[1]] or re.search(r'::|\b(?:class|template|namespace)\b', clean):
        return None
    return View(source, clean, opening, span[1])


def locals_for(part):
    """Ordinary, unescaped scalar declarations; leave shadows and qualified objects to the compiler."""
    found = []
    records = []
    for record in re.finditer(r'\b(?:struct|union)(?:\s+'+IDENT+r')?\s*{',part.body):
        start=part.opening+1+record.end()-1
        end=close(part.clean,start,'{','}')
        if end:
            records.append((start,end))
    for decl in DECL.finditer(part.clean, part.opening+1, part.end):
        if any(start < decl.start() < end for start,end in records):
            continue
        prefix = part.clean[part.opening+1:decl.start()].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue
        name = decl['name']
        # Also notice shadows declared as pointers, qualified objects or for-loop variables.
        declarations = re.findall(rf'\b(?:{INTEGER}|void|float|double)\s*\**\s*{re.escape(name)}\s*(?=[;=,\[])', part.body)
        if len(declarations) != 1 or re.search(rf'&\s*{re.escape(name)}\b', part.body):
            continue
        found.append(decl)
    return found


def refs(part, decl):
    return [hit for hit in re.finditer(rf'\b{re.escape(decl["name"])}\b', part.clean)
            if part.opening < hit.start() < part.end and not decl.start() <= hit.start() < decl.end()
            and not re.search(r'(?:\.|->|\bgoto)\s*$', part.clean[:hit.start()])
            and not re.match(r'\s*:(?!:)', part.clean[hit.end():])]


def edit(source, edits):
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start]+replacement+source[end:]
    return source


def integer(text):
    text = re.sub(r'[uUlL]+$', '', text)
    return int(text, 16 if text.lower().startswith('0x') else 10)


def split_args(text):
    start = depth = 0
    result = []
    for pos, char in enumerate(text):
        depth += (char in '([{')-(char in ')]}')
        if char == ',' and depth == 0:
            result.append((start, pos))
            start = pos+1
    if text.strip():
        result.append((start, len(text)))
    return result


def calls(part, name):
    for hit in re.finditer(rf'(?<![\w.>]){re.escape(name)}\s*\(', part.clean[part.opening+1:part.end-1]):
        start = part.opening+1+hit.end()
        end = close(part.clean, start-1)
        if end:
            yield start, end-1
