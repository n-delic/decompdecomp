"""Bounded, judge-backed experiments with real matched callees before local source permutation.

A low score can hide wrong arguments. Keep a few recent near misses too, and never accept a caller unless the
included helper also matches under the caller's compiler and header. Complex C++ declarations are left to agents.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .. import __version__, scoring, seeds
from ..project import compiler_for

MAX_BASES = 3
MAX_HELPERS = 3
MAX_PROBES = 6
CACHE_VERSION = 1
CACHE_ROWS = 96  # per caller, bounded even when its header and helpers keep changing
MAX_SOURCE = 32000
_SIGNATURE = r"\s*(?P<ret>(?:(?:static|extern|inline)\s+)*[\w\s*]+?)\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^(){};]*)\)\s*"


def combine(source: str, helper: str, name: str, header: str) -> str | None:
    """One simple C helper, made file-local. Refuse ambiguous declarations instead of guessing at C++ syntax.

    A caller's void* parameter declaration may stand for a typed pointer in the verified body. Preserve the
    caller's interface with an explicit typed local in the helper, then let the judge verify both bodies.
    """
    if max(len(source), len(helper)) > MAX_SOURCE or not re.fullmatch(r"[A-Za-z_]\w*", name):
        return None
    parts = seeds.declarations(source)
    definitions = seeds.declarations(helper)
    if len(definitions) != 1:
        return None
    text = definitions[0].text
    signature = re.match(_SIGNATURE + r"\{", text)
    if not signature or signature['name'] != name or '::' in text[:signature.end()]:
        return None
    # A definition or declaration in the shared header cannot be privately replaced in a probe.
    if any(name in d.names for d in seeds.declarations(header)):
        return None
    declarations = [d for d in parts if name in d.names]
    if len(declarations) != 1:
        return None
    declaration = declarations[0]
    proto = re.fullmatch(_SIGNATURE + ';', declaration.text)
    if not proto or proto['name'] != name:
        return None  # already defined, overloaded, a macro, or a multi-declarator statement
    args = [] if signature['args'].strip() in ('', 'void') else signature['args'].split(',')
    old_args = [] if proto['args'].strip() in ('', 'void') else proto['args'].split(',')
    if len(args) != len(old_args) or any('...' in a for a in args):
        return None
    locals_ = []
    for i, (arg, old) in enumerate(zip(args, old_args)):
        parameter = re.fullmatch(r'\s*([\w\s*]+?\*)\s*([A-Za-z_]\w*)\s*', arg)
        if parameter and re.fullmatch(r'\s*void\s*\*\s*(?:[A-Za-z_]\w*)?\s*', old):
            typ, ident = parameter.groups()
            raw = f'decomp_callee_arg_{i}'
            if re.search(r'\b' + raw + r'\b', text):
                return None
            args[i] = f'void *{raw}'
            locals_.append(f'{typ} {ident} = ({typ}){raw};')
    ret = re.sub(r'\b(?:static|extern|inline)\s+', '', signature['ret']).strip()
    if any(word in ret.split() for word in ('typedef', 'class', 'template', 'virtual')):
        return None
    body = 'static ' + ret + ' ' + name + '(' + ', '.join(args) + ') {\n' + '\n'.join(locals_)
    body += '\n' + text[signature.end():]
    # Only replace the declaration; keep the rest of the source (including its comments and macros) verbatim.
    if source.count(declaration.text) != 1:
        return None
    return source.replace(declaration.text, body, 1)


def helpers_match(net, proj, fn, source, obj):
    """Revalidate defined matched callees after every mutation, including mutations to their bodies."""
    names = {m['name'] for d in seeds.declarations(source)
             if (m := re.match(_SIGNATURE + r"\{", d.text)) and m['name'] != fn['name']}
    for name in names:
        helper = net.store.one("SELECT * FROM functions WHERE project_id=? AND name=? AND status='matched'",
                               proj['id'], name)
        if helper is not None:
            cand, exact, problems = net._evaluate(helper, proj, obj)
            if cand is None or exact is not True or problems:
                return False
    return True


def identity(fn):
    return [fn['id'], fn['name'], fn['vram'], bytes(fn['target'] or b'').hex(), fn['relocs']]


def cache_key(net, proj, fn, source, helpers, header, symbol_key):
    cfg = net.cfg(proj)
    facts = [CACHE_VERSION, __version__, symbol_key, identity(fn), source, header, compiler_for(cfg, fn['vram']),
             {k: cfg.get(k) for k in ('platform', 'gp', 'big_endian')},
             [[identity(h), h['matched_source'], net._context(proj['id'], h['unit'])['header']] for h in helpers]]
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def judge(net, proj, fn, source, helpers, header):
    """Return diagnostics, not a verdict inferred from a score. The normal linker judges every defined helper."""
    result = net._compile(proj, header, source, fn)
    if not result.ok:
        return None  # compiler/runtime failures may be transient; don't persist them as exhausted paths
    cand, verdict, problems = net._evaluate(fn, proj, result.obj)
    if cand is None:
        return None
    d = scoring.diff(net._target_obj(fn, proj), cand)
    checked = True
    messages = list(problems)
    for helper in helpers:
        obj, exact, errors = net._evaluate(helper, proj, result.obj)
        checked &= obj is not None and exact is True
        if obj is None or exact is not True:
            messages.append(f"{helper['name']}: included helper does not match")
            messages.extend(errors)
    exact = verdict is True and checked and not messages
    return {'score': 0 if exact else max(1, d.score), 'exact': exact, 'helpers_match': checked,
            'markers': sorted({r[0] for r in d.rows if r[0] != ' '}), 'problems': messages[:8]}


def probe(net, agent, proj, fn, stop, deadline, count=lambda: None):
    """Under the permuter's existing claim, return (matched, bounded alternative seeds, diagnostic summary).

    ROM only: project builds own their translation units; injecting helpers there would duplicate definitions.
    """
    if not net.is_rom(proj):
        return False, [], 'matched-callee probes require a ROM judge'
    called = net.xrefs(proj)['calls'].get(fn['vram'], set())
    if not called:
        return False, [], ''
    addresses = sorted(called)[:64]  # malformed call graphs cannot make an unbounded database query
    helpers = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched' "
                            "AND matched_source IS NOT NULL AND id!=? AND vram IN (" +
                            ','.join('?' for _ in addresses) + ") ORDER BY size, id LIMIT ?",
                            proj['id'], fn['id'], *addresses, MAX_HELPERS)
    if not helpers:
        return False, [], ''
    bases = [fn['best_source']]
    for row in net.store.all('SELECT source FROM attempts WHERE function_id=? AND compiled=1 AND score>0 '
                             'AND score<=? ORDER BY id DESC LIMIT 12', fn['id'], fn['n_insns'] * 100 * .06):
        if row['source'] and row['source'] not in bases:
            bases.append(row['source'])
        if len(bases) == MAX_BASES:
            break
    # Renaming/moving a referenced symbol changes the link even if the C and compiler stay the same.
    symbols = [tuple(r) for r in net.store.all(
        'SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    functions = [tuple(r) for r in net.store.all(
        'SELECT name,vram,size FROM functions WHERE project_id=? ORDER BY id', proj['id'])]
    symbol_key = hashlib.sha256(json.dumps([symbols, functions]).encode()).hexdigest()
    ctx = net._context(proj['id'], fn['unit'])
    header = ctx['header']
    best = fn['best_score']
    frontier, tested, cached, used, reasons = [], 0, 0, 0, []

    def evaluate(source, included):
        nonlocal tested, cached
        key = cache_key(net, proj, fn, source, included, header, symbol_key)
        found = net.store.one('SELECT result FROM callee_probes WHERE function_id=? AND cache_key=?', fn['id'], key)
        if found:
            cached += 1
            return json.loads(found['result'])
        if stop.is_set() or time.monotonic() >= deadline:
            return None
        count()
        tested += 1
        result = judge(net, proj, fn, source, included, header)
        if result and not result['exact']:
            result['callees'] = [h['name'] for h in included]
            with net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO callee_probes VALUES(?,?,?,?)',
                           (fn['id'], key, json.dumps(result), time.time()))
                db.execute('DELETE FROM callee_probes WHERE function_id=? AND cache_key NOT IN '
                           '(SELECT cache_key FROM callee_probes WHERE function_id=? ORDER BY created_at DESC LIMIT ?)',
                           (fn['id'], fn['id'], CACHE_ROWS))
        return result

    for source in bases:
        if stop.is_set() or time.monotonic() >= deadline:
            break
        candidates = [(h, combine(source, h['matched_source'], h['name'], header)) for h in helpers]
        candidates = [(h, text) for h, text in candidates if text is not None]
        if not candidates:
            continue
        baseline = evaluate(source, [])
        if not baseline:
            continue
        if baseline['problems']:
            reasons.append('linker validation failed; inspect helpers/relocations before source rewrites')
            continue
        if baseline['exact']:
            net._record(fn, agent, 0, source, ctx['version'], matched=True, via='permuter')
            return True, [], 'recent candidate matches under current build settings'
        if not baseline['markers'] or not set(baseline['markers']) <= {'r', 's', 'o', 'i'}:
            continue
        frontier.append((baseline['score'], source))
        for helper, combined in candidates:
            if used >= MAX_PROBES or stop.is_set() or time.monotonic() >= deadline:
                break
            used += 1
            result = evaluate(combined, [helper])
            if not result:
                continue
            if result['exact']:
                net._record(fn, agent, 0, combined, ctx['version'], matched=True, via='permuter')
                return True, [], f"matched with verified callee {helper['name']}"
            if result['helpers_match'] and not result['problems']:
                # Preserve the context for a later random rewrite, even when its scalar score got worse.
                frontier.append((result['score'], combined))
                if result['score'] < best:
                    best = result['score']
                    net._record(fn, agent, best, combined, ctx['version'], matched=False, via='permuter')
            elif result['problems']:
                reasons.append(f"{helper['name']}: helper/linker validation failed")
    # Keep different source contexts, not just the three numerically smallest scores (a wrong argument can score less).
    alternatives = [src for _, src in frontier if src != fn['best_source']]
    alternatives = list(dict.fromkeys(alternatives))[:MAX_BASES]
    summary = f'matched-callee probes: {tested} compiles, {cached} cached results'
    if reasons:
        summary += '; ' + '; '.join(dict.fromkeys(reasons))
    return False, alternatives, summary


def history(net, function_id):
    """Short historical evidence for the next agent, explicitly not a verdict on today's inputs."""
    rows = net.store.all('SELECT result FROM callee_probes WHERE function_id=? ORDER BY created_at DESC LIMIT 12',
                         function_id)
    lines = []
    for row in rows:
        result = json.loads(row['result'])
        if not result.get('callees'):
            continue
        why = '; '.join(result['problems'])[:240] if result['problems'] else f"caller score {result['score']}; helpers matched"
        line = ', '.join(result['callees']) + ': ' + why
        if line not in lines:
            lines.append(line)
        if len(lines) == 3:
            break
    return ('\n\nPrevious matched-callee probes (historical; inputs may have changed):\n' +
            '\n'.join('- ' + line for line in lines)) if lines else ''
