"""Bounded caller-backed return hints for ROM drafts; never edit the shared header."""
import re

from ..forks.csource import _blank
from . import return_contract_repairs


def caller(net,proj,fn):
    if not hasattr(net,'xrefs'):return ''
    asm=net._asm_text(fn,proj)
    if not return_contract_repairs.witness(asm,proj['arch']):return ''
    addresses=sorted(net.xrefs(proj).get('called_by',{}).get(fn['vram'],()))[:8]
    for address in addresses:
        row=net.store.one('SELECT * FROM functions WHERE project_id=? AND vram=?',proj['id'],address)
        if row:
            text=net._asm_text(row,proj)
            proof=return_contract_repairs.witness(asm,proj['arch'],text,fn['name'])
            if proof and proof['caller_consumes']:return text
    return ''


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
