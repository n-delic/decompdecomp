"""Cheap claim receipts: no diagnostic builds, drafts or imported-tree inspection on the claim path."""
import functools
import json
import threading
import time
from contextlib import nullcontext
from contextvars import ContextVar

from ..release_receipts import current

_fast = ContextVar('fast_agent_claim', default=False)


def fast():
    return _fast.get()


def quick(function):
    @functools.wraps(function)
    def run(net, agent, slug, *args, **kwargs):
        with net.store.lock:
            locks=net.__dict__.setdefault('_claim_request_locks',{})
            lock=locks.setdefault((agent.id,slug),threading.RLock())
        # Concurrent retries share the ordinary held claims; no parallel hydration/builds.
        progress = getattr(net, "progress", None)
        with lock, progress.view() if progress is not None else nullcontext():
            token=_fast.set(True)
            try:
                return function(net,agent,slug,*args,**kwargs)
            finally:
                _fast.reset(token)
    return run


def brief(net, fn, agent, header):
    from .agent_tokens import candidate
    from .draft_reports import read, summary
    proj=net.store.one('SELECT slug,name FROM projects WHERE id=?',fn['project_id'])
    available=fn['best_source'] or fn['matched_source'] or fn['seed'] or net.store.one('SELECT 1 FROM attempts WHERE function_id=? AND agent_id=? LIMIT 1',fn['id'],agent.id)
    source,revision=candidate(net,agent,proj['slug'],fn['name']) if available else ('','unavailable until source is loaded')
    lines=[f"## {header}: {proj['slug']}/{fn['name']}",
           f"unit {fn['unit'] or '(main image)'}; {fn['size']} bytes; status={fn['status']}",
           f"claim_token: {current(net,fn,agent)}",f'candidate revision {revision}',
           'Preparation: deferred. No diagnostic build or new draft was run while reserving this claim.',
           'Saved scores are historical. get_candidate verifies the baseline; get_assembly reads target instructions; '
           'get_function(section="brief") loads full supporting evidence. compile_patch/compile_and_diff use the ordinary judge.']
    from ..candidate_quality import source_hint as quality_hint
    if note := quality_hint(net, fn, source):lines.append(note)
    diagnostic = summary(read(net,fn['id']))
    if diagnostic:lines.append(diagnostic)
    from .wrapper_contracts import source_hint
    if note:=source_hint(source,fn['name']):lines.append(note)
    if source:
        lines += ['Candidate C (complete):','```c',source,'```']
    else:
        lines += ['No saved C. Read get_function(section="brief") for a draft and supporting evidence.']
    return '\n'.join(lines)


def get_claims(net, agent, slug):
    rows=net.store.all('SELECT f.*,c.claimed_at,c.expires_at FROM claims c JOIN functions f ON f.id=c.function_id '
                        'JOIN projects p ON p.id=f.project_id WHERE c.agent_id=? AND p.slug=? AND c.expires_at>? '
                        'ORDER BY f.id LIMIT 21',agent.id,slug,time.time())
    claims=[]
    for fn in rows[:20]:
        claims.append({'function':fn['name'],'unit':fn['unit'],'status':fn['status'],
                       'expires_at':fn['expires_at'],'claim_token':current(net,fn,agent),
                       'next':'get_candidate(function="'+fn['name']+'") to recover current C/revision; get_assembly for target code'})
    return json.dumps({'claims':claims,'more':len(rows)>20,
                       'scope':'Only your live claims in this project; no claims, sources or scores changed.'})


def ownership_hint(net, agent, project_id):
    rows=net.store.all('SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id '
                       'WHERE c.agent_id=? AND f.project_id=? AND c.expires_at>? ORDER BY f.id LIMIT 6',
                       agent.id,project_id,time.time())
    names=', '.join(r['name'] for r in rows) or '(another project)'
    return ('you hold another claim: '+names+'. Use get_claims to recover live function names and claim tokens, '
            'then get_candidate for the intended function; release it before working on a different function')
