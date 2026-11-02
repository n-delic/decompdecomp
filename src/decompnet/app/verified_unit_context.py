"""Candidate-only same-unit private helper context from current verified compiler inputs."""
import hashlib
import re

from .. import match_bundle
from ..forks.csource import _blank, find
from ..project import compiler_for
from .source_next_steps import called

FAMILY = 'verified_unit_helpers'


def apply(source, name, donors):
    """Private standalone definitions only; never replace externs or invent private ABI types."""
    from .integer_repair_support import IDENT, INTEGER, NUMBER, edit
    candidates={};helpers=set();used=set(re.findall(IDENT,_blank(source)))
    for donor in donors:
        clean=_blank(donor)
        for callee in sorted(called(source)-{name})[:16]:
            span=find(donor,callee)
            if not span:continue
            code=donor[span[0]:span[1]]
            if not re.search(r'\bstatic\b',_blank(code).split('{',1)[0]):continue
            candidates.setdefault(callee,set()).add(code);helpers.add(callee)
        for hit in re.finditer(rf'\bstatic\s+const\s+{INTEGER}\s+({IDENT})\s*=\s*-?{NUMBER}\s*;',clean):
            prefix=clean[:hit.start()]
            if prefix.count('{')!=prefix.count('}') or hit[1] not in used:continue
            candidates.setdefault(hit[1],set()).add(donor[hit.start():hit.end()])
    if not candidates or any(len(v)!=1 for v in candidates.values()) or len(candidates)>4:return []
    reference='\n'.join(next(iter(v)) for v in candidates.values())
    if '#' in reference or len(reference)>16000:return []
    # A helper used as a value/address has identity-sensitive semantics; leave it alone.
    for helper in helpers:
        for text in (source,reference):
            clean=_blank(text)
            for hit in re.finditer(r'\b'+re.escape(helper)+r'\b',clean):
                if not re.match(r'\s*\(',clean[hit.end():]):return []
    names={n:'__unit_'+n for n in candidates}
    if any(re.search(r'\b'+re.escape(n)+r'\b',source+reference) for n in names.values()):return []
    def renamed(text):
        clean=_blank(text);edits=[]
        for hit in re.finditer(IDENT,clean):
            if hit[0] in names and not re.search(r'(?:->|\.)\s*$',clean[:hit.start()]):
                edits.append((hit.start(),hit.end(),names[hit[0]]))
        return edit(text,edits)
    # Forward declarations admit mutually dependent helpers without pulling in another function.
    prototypes='\n'.join(next(iter(candidates[n])).split('{',1)[0].strip()+';' for n in sorted(helpers))
    prefix=renamed(prototypes+'\n'+reference)
    candidate='#line 1 "verified-unit.c"\n'+prefix+'\n#line 1 "func.c"\n'+renamed(source)
    return [(FAMILY,candidate)]


def donors(net, proj, fn, source):
    if not net.is_rom(proj) or not fn['unit'] or net.fork_slug(proj['slug']):return []
    settings=compiler_for(net.cfg(proj),fn['vram'])
    rows=net.store.all("SELECT * FROM functions WHERE project_id=? AND unit=? AND id!=? AND status='matched' ORDER BY id DESC LIMIT 24",
                       proj['id'],fn['unit'],fn['id'])
    result=[]
    for row in rows:
        if compiler_for(net.cfg(proj),row['vram'])!=settings:continue
        proof=match_bundle.status(net,row)
        if proof and not proof['changed_inputs'] and not proof.get('regression'):
            result.append(proof['source'])
    return result


def variants(net, proj, fn, source):
    return apply(source,fn['name'],donors(net,proj,fn,source))


def signature(net, proj, fn, source):
    return hashlib.sha256(repr(donors(net,proj,fn,source)).encode()).hexdigest()
