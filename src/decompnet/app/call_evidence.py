"""Who calls a function, and what its callers say about the type of its result.

The callees the app stores (a function's relocations; `RomNetwork.xrefs`) inverted give the callers. For each call
site the processor's reader (`Arch.call_sites`; MIPS: arch/mips_callsites.py) says whether the caller reads the
result register after the call or writes over it. Beyond the assembly there are three more witnesses: a matched
caller's own C (`x = g();` reads the value, a bare `g();` drops it), the project's shared header (a declaration
`void g(...)`), and a matched twin's C (the same function in this game or another release).

Verdict ('value', 'void' or 'unknown'), strongest evidence first:
- a caller reads the result register, or its matched C uses the call's value: 'value'. Compiled code does not read
  the result of a function that returns nothing, so this is strong.
- the shared header declares it `void`: 'void' (the header says so).
- several callers, all dropping the result: 'void', weak (non-void functions are often ignored too); a single
  dropping caller is not enough. docs/learnings/finding/return-type-from-callers.md measured each rule.
The verdict feeds the draft stage's result type (`app/draft_return_type.py`) and the function's brief; `get_callers`
lists the sites themselves.
"""
import re

from ..forks.csource import _blank, find

MAX_CALLERS = 12
VOID_CALLERS = 2  # callers that must all drop the result before the verdict is 'void'
TYPE_OF = {'int': 's32', 'long long': 's64', 'float': 'f32', 'double': 'f64'}
_STATEMENT_HEAD = ('if', 'while', 'for', 'switch')


def _open_paren(text, close):
    """The index of the '(' matching the ')' at `close`, or None."""
    depth = 0
    for i in range(close, -1, -1):
        if text[i] == ')':
            depth += 1
        elif text[i] == '(':
            depth -= 1
            if not depth:
                return i
    return None


def c_uses(source, name):
    """How C source uses each call of `name`: 'value' (assigned, returned, tested, an operand or an argument),
    'statement' (the result is dropped). Declarations of `name` are skipped."""
    text = re.sub(r'^[ \t]*#.*$', lambda m: ' ' * len(m[0]), _blank(source or ''), flags=re.M)  # (preprocessor lines)
    out = []
    for hit in re.finditer(r'(?<![\w.>])' + re.escape(name) + r'\s*\(', text):
        before = text[:hit.start()].rstrip()
        prev = before[-1:] if before else ''
        word = re.search(r'([A-Za-z_]\w*)$', before)
        stmt = before[max(before.rfind(';'), before.rfind('{'), before.rfind('}')) + 1:]
        if not before or prev in ';{}' or (prev == ':' and '?' not in stmt):  # (a case or label before it)
            out.append('statement')
        elif word and word[1] in ('else', 'do'):
            out.append('statement')
        elif word and word[1] == 'return':
            out.append('value')
        elif re.fullmatch(r'\s*(?:[A-Za-z_]\w*\s*\**\s*)+', stmt):
            continue  # a declaration (`s32 g(`, `extern Node *g(`)
        elif prev == ')':
            opening = _open_paren(before, len(before) - 1)
            head = re.search(r'([A-Za-z_]\w*)\s*$', before[:opening]) if opening is not None else None
            inner = before[opening + 1:-1].strip() if opening is not None else ''
            out.append('statement' if (head and head[1] in _STATEMENT_HEAD) or inner == 'void' else 'value')
        else:
            out.append('value')
    return out


def prototype(header, name):
    """The result type a header declares for `name` ('void', 'int *', ...), or None when it doesn't."""
    text = _blank(header or '')
    for hit in re.finditer(r'(?:^|[;}])\s*([^;{}()]*?)\b' + re.escape(name) + r'\s*\(', text):
        found = hit[1].strip()
        if found and not re.search(r'\b(?:return|else|typedef)\b|[=,]', found):
            return re.sub(r'\b(?:extern|static|inline|const)\b', '', found).strip() or None
    return None


def source_type(source, name):
    """The result type a function's own C declares ('void', 's32', 'f32', ...), or None."""
    span = find(source or '', name)
    if not span:
        return None
    text = _blank(source[span[0]:span[1]])
    head = re.match(r'\s*([^;{}()]*?)\b' + re.escape(name) + r'\s*\(', text)
    if not head:
        return None
    return re.sub(r'\b(?:extern|static|inline|const|register)\b', '', head[1]).strip() or None


def _is_void(type_text):
    return bool(type_text) and re.fullmatch(r'void', type_text.strip()) is not None


def _caller_rows(net, proj, fn):
    store = net.project_store(proj['id'])
    addresses = sorted(net.xrefs(proj).get('called_by', {}).get(fn['vram'], ()))
    rows = []
    for address in addresses:
        found = store.all('SELECT * FROM functions WHERE project_id=? AND vram=? AND gone_mark IS NULL LIMIT 2',
                          proj['id'], address)
        if len(found) == 1:
            rows.append(found[0])
    return rows, len(addresses)


def gather(net, proj, fn, header=None, *, limit=MAX_CALLERS, lines_before=3, lines_after=7, twins=True):
    """The evidence: {'callers': [{name, vram, status, sites: [...], c_uses: [...]}], 'total': callers in all,
    'prototype': a header's result type or None, 'twins': [(name, type)]} (the lists are cut to `limit`)."""
    from .. import arch as architectures

    reader = getattr(architectures.get(proj['arch']), 'call_sites', None)
    rows, total = _caller_rows(net, proj, fn)
    callers = []
    for row in rows[:limit]:
        sites = reader(net._asm_text(row, proj), fn['name'], lines_before, lines_after) if reader else []
        uses = c_uses(row['matched_source'], fn['name']) if row['status'] == 'matched' and row['matched_source'] else []
        callers.append({'name': row['name'], 'vram': row['vram'], 'status': row['status'], 'sites': sites, 'c_uses': uses})
    out = {'callers': callers, 'total': total, 'prototype': prototype(header, fn['name']) if header else None,
           'twins': []}
    if twins:
        out['twins'] = twin_types(net, proj, fn)
    return out


def twin_types(net, proj, fn, floor=.9):
    """[(name, result type)] of the matched look-alikes in this game (instruction similarity >= `floor`)."""
    if not fn['target'] or net.fork_slug(proj['slug']):
        return []
    out = []
    try:
        for row, score in net.similar(proj, fn, top=4):
            if row['status'] == 'matched' and score >= floor:
                kind = source_type(row['matched_source'], row['name'])
                if kind:
                    out.append((row['name'], kind))
    except Exception:  # noqa: BLE001 - twins are only a witness
        return []
    return out


def verdict(evidence):
    """{'verdict': 'value'|'void'|'unknown', 'type': C type or None, 'strength': 'strong'|'weak'|None,
    'reasons': [sentences]} from `gather`'s evidence."""
    reasons, kinds, dropped, values = [], [], [], []
    for caller in evidence['callers']:
        for site in caller['sites']:
            if site['use'] in ('value', 'float', 'double'):
                values.append(caller['name'])
                kinds.append(site['result'] or 'int')
            elif site['use'] == 'discarded':
                dropped.append(caller['name'])
        if 'value' in caller['c_uses']:
            values.append(caller['name'])
        if caller['c_uses'] and all(u == 'statement' for u in caller['c_uses']):
            dropped.append(caller['name'])
    values, dropped = list(dict.fromkeys(values)), list(dict.fromkeys(dropped))
    kind = next((k for k in ('double', 'float', 'long long') if k in kinds), 'int')
    proto = evidence.get('prototype')
    if values:
        reasons.append(f"{len(values)} of {evidence['total']} callers read the result ({', '.join(values[:4])}"
                       f"{', ...' if len(values) > 4 else ''}): a {kind} result")
        return {'verdict': 'value', 'type': TYPE_OF[kind], 'strength': 'strong', 'reasons': reasons}
    if proto and _is_void(proto):
        return {'verdict': 'void', 'type': 'void', 'strength': 'strong',
                'reasons': ['the shared header declares it void']}
    if proto:
        return {'verdict': 'value', 'type': proto, 'strength': 'strong',
                'reasons': [f'the shared header declares it {proto}']}
    twins = evidence.get('twins') or []
    if twins and all(_is_void(t) for _, t in twins):
        return {'verdict': 'void', 'type': 'void', 'strength': 'weak',
                'reasons': [f'its matched look-alike {twins[0][0]} returns void']}
    if twins and not any(_is_void(t) for _, t in twins):
        return {'verdict': 'value', 'type': twins[0][1], 'strength': 'weak',
                'reasons': [f'its matched look-alike {twins[0][0]} returns {twins[0][1]}']}
    if len(dropped) >= VOID_CALLERS and not values:
        return {'verdict': 'void', 'type': 'void', 'strength': 'weak',
                'reasons': [f'{len(dropped)} callers drop the result ({", ".join(dropped[:4])}) and none reads it']}
    return {'verdict': 'unknown', 'type': None, 'strength': None,
            'reasons': [f"{evidence['total']} callers, none shows what the result is" if evidence['total']
                        else 'no direct callers (called through a pointer or a table)']}


def render(fn, evidence, result=None, *, with_code=True):
    """get_callers' text: the verdict, then each call site with the instructions around it."""
    result = result or verdict(evidence)
    shown = len(evidence['callers'])
    lines = [f"{fn['name']} is called from {evidence['total']} place{'s' if evidence['total'] != 1 else ''}"
             f"{f' (the first {shown} shown)' if shown < evidence['total'] else ''}.",
             f"Result: {result['verdict']}" + (f" ({result['type']}, {result['strength']})" if result['type'] else '')
             + ' - ' + '; '.join(result['reasons'])]
    if evidence.get('prototype'):
        lines.append(f"Declared in the shared header with result type: {evidence['prototype']}")
    for name, kind in evidence.get('twins') or []:
        lines.append(f'Matched look-alike {name} returns: {kind}')
    for caller in evidence['callers']:
        c = f" matched C: {', '.join(caller['c_uses'])}" if caller['c_uses'] else ''
        lines += ['', f"## {caller['name']} ({caller['status']}){c}"]
        for site in caller['sites']:
            what = {'value': 'reads the result', 'float': 'reads the float result', 'double': 'reads the double result',
                    'discarded': 'drops the result', 'passthrough': 'returns with the result untouched',
                    'unknown': 'result use not shown (a branch comes first)'}[site['use']]
            lines.append(f"- {'tail call' if site['kind'] == 'tail' else 'call'}: {what}")
            if with_code:
                lines += [('  > ' if i == site['mark'] else '    ') + row for i, row in enumerate(site['excerpt'])]
    if not evidence['callers']:
        lines.append('(no direct callers are known: it is called through a pointer or a table, or from outside the game)')
    return '\n'.join(lines)


def brief_line(net, proj, fn, header=None):
    """One line for the function's brief, or '' when callers say nothing."""
    try:
        found = gather(net, proj, fn, header, limit=6, lines_before=0, lines_after=0)
    except Exception:  # noqa: BLE001 - a brief line is never worth failing the brief
        return ''
    result = verdict(found)
    if result['verdict'] == 'unknown':
        return ''
    return (f"Result type from callers: {result['verdict']}" + (f" ({result['type']})" if result['verdict'] == 'value' else '')
            + f", {result['strength']}: " + '; '.join(result['reasons']) + '. get_callers has the call sites.')
