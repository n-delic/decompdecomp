"""Explain candidate selection separately from recorded compiler evidence; never infer an unrecorded writer."""
import hashlib
import json


def remember(net, agent, fn, source, *, attempt=None, selection=''):
    cache=net.__dict__.setdefault('_candidate_origins',{})
    cache[agent.id,fn['id']]={'revision':hashlib.sha256(source.encode()).hexdigest()[:20],
                             'attempt':attempt,'selection':selection}
    while len(cache)>128:
        del cache[next(iter(cache))]


def describe(net, agent, fn, source):
    revision=hashlib.sha256(source.encode()).hexdigest()[:20]
    origin=getattr(net,'_candidate_origins',{}).get((agent.id,fn['id']),{})
    if origin.get('revision')!=revision:
        origin={}
    row=net.project_store(fn['project_id']).one('SELECT a.id,a.source,a.score,a.score_before,a.score_improved,a.via,a.agent_id '
                      'FROM attempts a WHERE a.function_id=? AND a.id=?',
                      fn['id'],origin.get('attempt')) if origin.get('attempt') else None
    if row is None:
        # Saved-best lookups use the score index; arbitrary revisions search only recent history.
        score=0 if source==fn['matched_source'] else fn['best_score'] if source==fn['best_source'] else None
        columns='a.id,a.source,a.score,a.score_before,a.score_improved,a.via,a.agent_id '
        if score is not None:
            row=net.project_store(fn['project_id']).one('SELECT '+columns+'FROM attempts a '
                              'WHERE a.function_id=? AND a.score=? AND a.source=? ORDER BY a.id DESC LIMIT 1',
                              fn['id'],score,source)
        else:
            row=net.project_store(fn['project_id']).one('SELECT '+columns+'FROM (SELECT * FROM attempts WHERE function_id=? '
                              'ORDER BY id DESC LIMIT 128) a '
                              'WHERE a.source=? ORDER BY a.id DESC LIMIT 1',fn['id'],source)
    parts=[origin['selection']] if origin.get('selection') else []
    if row is not None and row['source']==source:
        from .project_labels import names
        writer = names(net,[row['agent_id']]).get(row['agent_id'])
        who='you' if writer==agent.name else (writer or 'an unrecorded agent')
        text=f"Recorded attempt: {who}; attempt {row['id']}"
        if row['via']:
            text+='; via '+str(row['via'])[:80]
        if row['score_improved'] and row['score_before'] is not None and row['score'] is not None:
            text+=f"; saved score {row['score_before']} → {row['score']}"
        elif row['score'] is not None:
            text+=f"; attempt score {row['score']}"
        else:
            text+='; did not compile'
        parts.append(text)
        provenance=net.project_store(fn['project_id']).one('SELECT provenance FROM repair_attempt_provenance WHERE attempt_id=?',row['id'])
        if provenance:
            p=json.loads(provenance['provenance'])
            expected=p.get('source_sha256')
            if p.get('kind')!='inherited repair context' and (not expected or expected==hashlib.sha256(source.encode()).hexdigest()):
                details=[str(p[k])[:80] for k in ('kind','family') if p.get(k)]
                if p.get('variant') is not None:details.append('variant '+str(p['variant'])[:20])
                if details:parts.append('Recorded trial: '+', '.join(details))
    elif not parts:
        parts.append('Source origin not recorded for this revision; no writer or score change inferred')
    return 'Candidate origin: '+' | '.join(' '.join(p.split()) for p in parts)+'. Historical scores are not a fresh verification.'
