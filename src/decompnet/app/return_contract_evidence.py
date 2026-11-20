"""Bounded caller-backed return hints for ROM drafts; never edit the shared header."""
import re

from ..forks.csource import _blank, find
from . import return_contract_repairs


def _callers(net, proj, fn):
    if not hasattr(net, 'xrefs'):
        return
    addresses = sorted(net.xrefs(proj).get('called_by', {}).get(fn['vram'], ()))[:8]
    store = net.project_store(proj['id'])
    for address in addresses:
        rows = store.all('SELECT * FROM functions WHERE project_id=? AND vram=? LIMIT 2', proj['id'], address)
        if len(rows) == 1:
            row = rows[0]
            yield row['name'], net._asm_text(row, proj)


def caller(net, proj, fn):
    asm = net._asm_text(fn, proj)
    if return_contract_repairs.witness(asm, proj['arch']):
        for _, text in _callers(net, proj, fn):
            proof = return_contract_repairs.witness(asm, proj['arch'], text, fn['name'])
            if proof and proof['caller_consumes']:
                return text
    return ''


def _consumption(asm, arch, text, callee):
    from ..arch.mips_regions import ALU, LOADS, STORES
    from ..arch.mips_returns import instructions

    raw = [(line, parsed[0]) for line in text.splitlines() if (parsed := instructions(line))]
    producer_only = {'mult','multu','dmult','dmultu','div','divu','ddiv','ddivu'}
    for i, (_,call) in enumerate(raw):
        if not re.fullmatch(r'jal\s+'+re.escape(callee), call):
            continue
        for j in range(i+2, min(i+10,len(raw))):
            line = raw[j][1]
            op = line.split(None,1)[0]
            regs = re.findall(r'\$\w+', line)
            snippet = raw[i:j+1]
            proof = return_contract_repairs.witness(asm, arch, '\n'.join(row[0] for row in snippet), callee)
            if (proof and proof['caller_consumes']) or (op in producer_only and len(regs) == 2 and '$v0' in regs):
                return [row[1] for row in snippet]
            if op in ALU | LOADS | {'mfc1'}:
                if regs and regs[0] == '$v0':
                    break
            elif op not in STORES | producer_only | {'nop','mtc1'}:
                break  # Control transfer or an unmodelled instruction.
    return []


def _stored_word_result(asm):
    """A straight-line callee leaves the same computed word it writes to memory in v0."""
    from ..arch.mips_regions import ALU, LOADS, STORES
    from ..arch.mips_returns import instructions

    rows = instructions(asm)
    if not 3 <= len(rows) <= 128 or not re.fullmatch(r'jr\s+\$ra', rows[-2]):
        return False
    computed = stored = False
    for line in [*rows[:-2], rows[-1]]:
        op = line.split(None,1)[0]
        regs = re.findall(r'\$\w+',line)
        if op in ALU | LOADS and regs and regs[0] == '$v0':
            computed, stored = op in ALU, False
        elif op == 'sw' and regs and regs[0] == '$v0':
            stored = computed
        elif op not in ALU | LOADS | STORES | {'nop'}:
            return False
    return stored


def matched_review(net, proj, fn):
    """Caller-backed signature evidence for an exact C definition."""
    source = fn['matched_source']
    if fn['status'] != 'matched' or not source:
        return None
    span = find(source, fn['name'])
    if not span or not re.match(r'\s*(?:static\s+)?void\s+'+re.escape(fn['name'])+r'\s*\(',
                                _blank(source[span[0]:span[1]])):
        return None
    asm = net._asm_text(fn, proj)
    proof = return_contract_repairs.witness(asm, proj['arch'])
    if proj['arch'] != 'mips' or not (proof or _stored_word_result(asm)):
        return None
    for name, text in _callers(net, proj, fn):
        lines = _consumption(asm, proj['arch'], text, fn['name'])
        if lines:
            return {'caller': name, 'register': 'v0', 'instructions': lines,
                    'message': 'The matched C declares void, but this caller reads the return register. '
                               'Review the result type and rejudge the affected call sites together.'}
    return None


def review_text(review):
    if not review:
        return ''
    return ('Matched function signature review. '+review['message']+' Caller '+review['caller']+'.\n'+
            '\n'.join('  '+line for line in review['instructions']))


def m2c_context(net,proj,fn,context,header):
    if (re.search(r'\b'+re.escape(fn['name'])+r'\s*\(',_blank(header))
            or not caller(net,proj,fn)):
        return context
    # This is a generated-draft hypothesis, not a discovered C type or shared prototype.
    return context+'\n/* Caller consumes the target word result; judge the draft normally. */\nint '+fn['name']+'();'


def brief(net,proj,fn,source):
    if not source or not re.search(r'\bvoid\s+'+re.escape(fn['name'])+r'\s*\(',_blank(source)):return ''
    hint=return_contract_repairs.hint(source,fn['name'],net._asm_text(fn,proj),proj['arch'],caller(net,proj,fn))
    from . import return_path_hypotheses, return_path_search
    if not hint:hint=return_path_hypotheses.brief(net,proj,fn)
    return '\n'.join(filter(None,(hint,return_path_search.history(net,proj,fn))))
