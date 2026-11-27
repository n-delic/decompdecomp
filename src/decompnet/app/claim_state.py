"""Cheap claim receipts; verify existing handoffs without running diagnostic builds or generating drafts."""
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
        guard=net.__dict__.setdefault("_claim_request_guard",threading.Lock())
        with guard:
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


def history(net, fn):
    from .agent_tokens import tried_summary

    lines = []
    if summary := tried_summary(net, fn, compact=True):
        lines.append(summary.strip())
    if notes := net.notes_for_brief(fn, show=2, when=False):
        return ['Handoff notes:', *(note[:450] for note in notes), *lines]
    return lines


def brief(net, fn, agent, header):
    store = net.project_store(fn['project_id'])
    from .agent_tokens import candidate
    from .draft_reports import read, summary
    proj=store.one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    available=fn['best_source'] or fn['matched_source'] or fn['seed'] or store.one('SELECT 1 FROM attempts WHERE function_id=? AND agent_id=? LIMIT 1',fn['id'],agent.id)
    source,revision=candidate(net,agent,proj['slug'],fn['name']) if available else ('','unavailable until source is loaded')
    lines=[f"## {header}: {proj['slug']}/{fn['name']}",
           f"unit {fn['unit'] or '(main image)'}; {fn['size']} bytes; status={fn['status']}",
           f"claim_token: {current(net,fn,agent)}",f'candidate revision {revision}',
           'Preparation: deferred. No diagnostic build or new draft was run while reserving this claim.',
           'Saved scores are historical. get_candidate verifies the baseline; get_assembly reads target instructions; '
           'get_function(section="brief") loads full supporting evidence and any retargeted twin. '
           'Try a matched twin first, then compiler_experiments for a supported profile hypothesis, '
           'then try_local_repairs for small register or scheduling differences. '
           'compile_patch/compile_and_diff use the ordinary judge.']
    from .agent_review import handoff_brief
    if handoff := handoff_brief(net, proj['slug'], fn, source):
        lines.append(handoff)
    from ..claim_routing import current_focus
    from .guided_matching import GOALS

    focus_record = store.one('SELECT focus,inputs,context_version FROM matching_focus WHERE function_id=?', fn['id'])
    focus = current_focus(store.one, proj, fn, focus_record)
    if focus:
        lines.append('Claim objective. ' + GOALS.get(focus, 'Resolve one ' + focus.lower() + ' mismatch.'))
    from .start_boundaries import brief as start_brief
    if note := start_brief(net, proj, fn):
        lines.append(note)
    lines.append('For a compact working packet with the current C, use get_function(section="work").')
    lines.extend(history(net, fn))
    from .profile_attempts import brief_lines as other_profile
    lines += other_profile(net, proj, fn)
    if source:
        from .deep_handoff import brief as deep_brief
        from .search_evidence import repeated_hint

        if handoff := deep_brief(net, proj, fn, source):
            lines.append(handoff)

        if repeat := repeated_hint(net, proj, fn, source):
            lines.append(repeat)
    from .stub_candidates import hint as stub_hint
    if note := stub_hint(net, fn, source):lines.append(note)
    from ..candidate_quality import source_hint as quality_hint
    if note := quality_hint(net, fn, source):lines.append(note)
    diagnostic = summary(read(net,fn['id'],project_id=fn['project_id']))
    if diagnostic:lines.append(diagnostic)
    from .wrapper_contracts import source_hint
    if note:=source_hint(source,fn['name']):lines.append(note)
    if source:
        lines += ['Candidate C (complete):','```c',source,'```']
    else:
        lines += ['No saved C. Read get_function(section="brief") for a draft and supporting evidence.']
    return '\n'.join(lines)


def get_claims(net, agent, slug):
    store = net.project_store(slug)
    rows=store.all('SELECT f.*,c.claimed_at,c.expires_at FROM claims c JOIN functions f ON f.id=c.function_id '
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
    store = net.project_store(project_id)
    rows=store.all('SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id '
                       'WHERE c.agent_id=? AND f.project_id=? AND c.expires_at>? ORDER BY f.id LIMIT 6',
                       agent.id,project_id,time.time())
    names=', '.join(r['name'] for r in rows) or '(another project)'
    return ('you hold another claim: '+names+'. Use get_claims to recover live function names and claim tokens, '
            'then get_candidate for the intended function; release it before working on a different function')


def claimed_name(net, agent, slug, named=None):
    """The function `agent` claimed most recently in `slug` (what claim_function just gave it), or ''."""
    row = net.project_store(slug).one(
        'SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=? '
        'ORDER BY c.claimed_at DESC LIMIT 1', agent.id)
    return row['name'] if row else ''
