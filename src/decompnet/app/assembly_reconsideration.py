"""Retire obsolete tooling verdicts when a target-backed supported repair is available."""
import json
import time

from .. import toolchains
from ..network import NetworkError, assembly_blocker
from ..project import compiler_for
from . import live_return_address_repairs


def candidates(net, proj, fn):
    if proj['arch']!='mips':return []
    meta=json.loads(fn['meta'] or '{}')
    votes=meta.get('not_c',{})
    if fn['status']!='asm' or not votes or 'you' in votes or any(meta.get(k) for k in ('set_aside','asm_twin','verified_assembly','boundary_review')):
        return []
    if not all(assembly_blocker(v.get('why','')) for v in votes.values()):return []
    console=net.console(proj)
    if fn['target'] and (console.asm_reason(fn['target']) or console.boundary_reason(fn['target'])):return []
    profile=compiler_for(net.cfg(proj),fn['vram'],fn)
    tid=profile.get('toolchain')
    if not isinstance(tid,str) or not tid.strip():return []
    try:
        if not toolchains.installed(tid):return []
        tc=toolchains.get(tid)
    except toolchains.ToolchainError:
        return []
    if '__builtin_mips_live_return_address' not in toolchains.source_prelude(tc,'__builtin_mips_live_return_address()'):return []
    ctx=net._context(proj['id'],fn['unit'])
    return live_return_address_repairs.variants(fn['best_source'] or '',fn['name'],proj['arch'],fn['asm'] or '',ctx['header'])[:4]


def run(net, proj, log=print):
    """Invalidate only obsolete tooling votes, then use the ordinary bounded autopilot judge."""
    if not net.is_rom(proj) or proj['slug'] in net.links:return 0
    reopened=0
    for fn in net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='asm'",proj['id']):
        if net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?',fn['id']):continue
        proposals=candidates(net,proj,fn)
        if not proposals:continue
        with net.project_store(proj['id']).tx() as db:
            current=db.execute('SELECT * FROM functions WHERE id=?',(fn['id'],)).fetchone()
            if dict(current)!=dict(fn) or db.execute('SELECT 1 FROM claims WHERE function_id=?',(fn['id'],)).fetchone():continue
            meta=json.loads(fn['meta'] or '{}')
            meta['capability_review']={'capability':'mips_live_return_address', 't':time.time(),
                                       'not_c':meta.pop('not_c'), 'asm_code':meta.pop('asm_code',None)}
            meta['reopened']=True
            db.execute("UPDATE functions SET status='open',meta=?,fingerprint=NULL WHERE id=?",(json.dumps(meta),fn['id']))
            net._event(db,proj['id'],None,'reopen',fn['name']+': obsolete tooling verdicts reconsidered with supported hardware C')
        reopened+=1
        agent,_=net.local_agent('autopilot')
        log(f"Checking supported hardware C for {fn['name']} ({len(proposals)} candidates).")
        for _,source in proposals:
            current=net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',fn['id'])
            if current['status']!='open':break
            try:
                if net.try_quietly(agent,proj,current,source).get('matched'):break
            except NetworkError:
                break  # another worker may have taken it after reopening
    return reopened
