"""Conservative return-only bundles: direct discarded calls, never inferred indirect contracts."""
import json
import re

from .. import arch as architectures
from ..forks.csource import _blank
from ..network import NetworkError
from . import integer_abi
from .pattern_repairs import close
from .source_next_steps import signatures


def declarations(text, name, before, after):
    """Rewrite only simple ANSI prototypes; reject address-taking and non-statement calls."""
    clean = _blank(text)
    word = re.compile(r'\b'+re.escape(name)+r'\b')
    proto = re.compile(r'(?m)(?:^|(?<=[;{}\n]))[ \t]*(?:extern[ \t]+)?void[ \t]+'
                       +re.escape(name)+r'\s*\([^(){};]*\)\s*;')
    edits = []
    covered = set()
    for hit in proto.finditer(clean):
        signature = signatures(hit[0], {name}).get(name)
        if signature != before:
            raise NetworkError('A caller has a conflicting declaration; the return repair stays diagnostic.')
        at = hit.start()+re.search(r'\bvoid\b',hit[0]).start()
        edits.append((at, at+4, after.split(' (',1)[0]))
        covered.update(range(hit.start(),hit.end()))
    for hit in word.finditer(clean):
        if hit.start() in covered:continue
        tail = re.match(r'\s*\(', clean[hit.end():])
        if not tail or clean[:hit.start()].rstrip()[-1:] not in (';', '{', '}'):
            raise NetworkError('A source reference may use the return value or take its address; saved C is unchanged.')
        opening = hit.end()+tail.end()-1
        end = close(clean,opening,'(',')')
        if not end or not re.match(r'\s*;',clean[end:]):
            raise NetworkError('A source caller does not discard the result as a standalone statement.')
    for start,end,value in reversed(edits):text=text[:start]+value+text[end:]
    return text


def plan(net, proj, fn, source, functions, contexts, image):
    reader = architectures.get(proj['arch']).source_return_paths
    old = signatures(fn['best_source'] or '',{fn['name']}).get(fn['name'],'')
    new = signatures(source,{fn['name']}).get(fn['name'],'')
    if not reader or not old.startswith('void (') or not new or old.split(' (')[1:] != new.split(' (')[1:]:
        raise NetworkError('Automatic return repair requires unchanged ANSI parameters and a void return.')
    header = contexts.get(fn['unit'] or '',contexts[''])['header']
    if integer_abi.scalar(new.split(' (')[0],header+'\n'+source,proj['arch']) != (32,True):
        raise NetworkError('Only a target-supported signed word return can be applied automatically.')
    proof = reader(net._asm_text(fn,proj))
    if not proof or proof['truncated'] or not proof['guarded_exits']:
        raise NetworkError('The target no longer supports this return hypothesis.')
    # A raw pointer table is not represented by function relocation call edges. Decline it,
    # including unaligned embedded pointers, rather than pretending xrefs close indirect calls.
    pointer = int(fn['vram']).to_bytes(4,'big' if proj['big_endian'] else 'little')
    if pointer in image:
        raise NetworkError('The image contains a possible function pointer; indirect callers need review.')
    updates = []
    callers = 0
    for row in functions:
        refs = [(off,addr) for off,_,addr in json.loads(row['relocs'] or '[]')
                if fn['vram'] <= addr < fn['vram']+fn['size']]
        body = source if row['id']==fn['id'] else row['matched_source'] or row['best_source'] or ''
        ctx = contexts.get(row['unit'] or '',contexts[''])
        hdr = declarations(ctx['header'],fn['name'],old,new)
        if row['id']==fn['id']:
            if refs:raise NetworkError('Recursive return repairs require a separate review.')
            updated = body
        else:
            updated = declarations(body,fn['name'],old,new)
            if refs:
                evidence = reader(net._asm_text(fn,proj),net._asm_text(row,proj),fn['name'])
                uses = evidence['caller_uses'] if evidence else []
                if (not uses or len(uses)>=8 or any(u['use']!='discarded' for u in uses)
                        or {(off,addr) for off,addr in refs} != {(u['call']*4,fn['vram']) for u in uses}):
                    raise NetworkError('A target reference is not a proven discarded direct call; saved C is unchanged.')
                if row['status']!='matched' or not row['matched_source']:
                    raise NetworkError('Every affected caller needs verified source before automatic return repair.')
                callers += len(uses)
        if row['id']==fn['id'] or updated!=body or hdr!=ctx['header'] or refs:
            if row['id']!=fn['id'] and row['status']!='matched':
                raise NetworkError('An affected source is not matched yet; its declarations need review.')
            updates.append(dict(function=row,source=updated,header=hdr,context=ctx))
    if not callers:raise NetworkError('No discarded direct caller was established; the candidate stays diagnostic.')
    return updates
