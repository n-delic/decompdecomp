"""Retain agent-reported concerns and compiled alternatives without changing the judge or best C."""
import hashlib
import json
import re
import time

CONCERN=re.compile(r'\bwrong (?:C|logic|code)\b|\b(?:missing|omits?|never resets?) (?:the )?(?:counter|reset|store|initialization)\b|uninitialized|undefined behavio[u]?r|semantically (?:incorrect|invalid)|invalid historical|historical[^.\n]{0,100}(?:invalid|incorrect)',re.I)
PREFIX='candidate-quality:'


def records(net,fn):
    store = net.project_store(fn['project_id'])
    row=store.one('SELECT value FROM settings WHERE key=?',PREFIX+str(fn['id']))
    return json.loads(row['value']) if row else []


def capture(net,fn,agent,notes,progress):
    if not CONCERN.search(notes) or fn['status']=='matched':
        return
    store = net.project_store(fn['project_id'])
    current=store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    alternatives=store.all('SELECT id,source,score FROM attempts WHERE function_id=? AND agent_id=? '
                               'AND created_at>=? AND compiled=1 AND score>0 AND (source_hash IS NOT NULL OR source IS NOT NULL) '
                               'ORDER BY score,id DESC LIMIT 30',fn['id'],agent.id,
                               progress['claimed_at'] if progress else time.time())
    seen={current['best_source']};ids=[]
    for row in alternatives:
        if row['source'] in seen:
            continue
        seen.add(row['source']);ids.append(row['id'])
        if len(ids)==3:
            break
    report={'source_hash':hashlib.sha256((current['best_source'] or '').encode()).hexdigest(),
            'warning':notes[:600], 'agent':agent.name, 'alternatives':ids}
    reference=re.search(r'(?:start from|corrected (?:revision|version)|use revision)[^\n]{0,50}?\b([a-f0-9]{20}|[a-f0-9]{64})\b',notes,re.I)
    if reference:
        revision=reference[1].lower()
        recent=store.all('SELECT id,source FROM attempts WHERE function_id=? AND compiled=1 AND score IS NOT NULL '
                             'AND (source_hash IS NOT NULL OR source IS NOT NULL) ORDER BY id DESC LIMIT 200',fn['id'])
        linked=next((r for r in recent if hashlib.sha256(r['source'].encode()).hexdigest().startswith(revision)
                     and r['source']!=current['best_source']),None)
        if linked:
            report.update(structured=True,issue='other',corrected_attempt=linked['id'])
            report['alternatives']=list(dict.fromkeys([linked['id']]+ids))[:3]
    saved=records(net,fn)
    for previous in saved:
        if previous['source_hash']==report['source_hash']:
            if previous.get('structured'):
                report.update(issue=previous['issue'], structured=True, warning=previous['warning'],
                              corrected_attempt=previous.get('corrected_attempt'))
            report['review']=previous.get('review','pending')
            report['alternatives']=list(dict.fromkeys(report['alternatives']+previous['alternatives']))[:3]
    saved=[r for r in saved if r['source_hash']!=report['source_hash']]
    saved.append(report)
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+str(fn['id']),json.dumps(saved[-4:])))


def source_hint(net, fn, source):
    """Surface only this candidate's pending report, without hydrating alternatives or context."""
    if fn['status'] != 'open' or not source:
        return ''
    digest = hashlib.sha256(source.encode()).hexdigest()
    report = next((r for r in reversed(records(net, fn)[-4:])
                   if r.get('source_hash') == digest and r.get('review', 'pending') == 'pending'), None)
    if not report:
        return ''
    warning = ' '.join(report.get('warning', '').split())[:200]
    hint = (f'Candidate quality (agent report, not proven; revision {digest[:20]}): {warning} '
            'Inspect get_function(section="quality") before optimizing this revision; '
            'a lower score alone does not establish valid C. Work remains available.')
    if report.get('structured') and type(report.get('corrected_attempt')) is int and report['corrected_attempt'] > 0:
        hint += (' A compiled alternative was explicitly linked to this report: '
                 'get_candidate(base="reported_corrected"); inspect and rejudge it before use.')
    return hint


def brief(net,fn):
    store = net.project_store(fn['project_id'])
    if fn['status']=='matched':
        return []
    from .app.source_match_context import aggregate_hints
    source = fn['best_source'] or fn['seed'] or ''
    abi = aggregate_hints(source, fn['name'], source)
    prefix = ['### Aggregate ABI checks (advisory)', *abi] if abi else []
    prefix += net.knowledge.for_function(fn)
    from .app.matched_context import brief as matched_context
    prefix += matched_context(net, fn, source)
    corrected = corrected_alternative(net, fn)
    if corrected:
        revision = hashlib.sha256(corrected['source'].encode()).hexdigest()[:20]
        prefix += ['### Reported corrected alternative (not verified)',
                   f'Revision {revision}, recorded score {corrected["score"]}. '
                   'Select with get_candidate(base="reported_corrected"); rejudge in the current context. '
                   'This alternative is retained separately from the lowest score; inspect its evidence before using.']
    current=hashlib.sha256((fn['best_source'] or '').encode()).hexdigest()
    saved=[r for r in records(net,fn) if r['source_hash']==current]
    if saved:
        return prefix + ['### Candidate quality (agent report, not proven)',saved[-1]['warning'][:300],
                ('Work continues while this concern awaits review. ' if saved[-1].get('review','pending') == 'pending' else 'User reviewed this candidate; the historical warning is retained. ')+
                'Compiled alternatives and evidence: get_function(section="quality"). Rejudge before using; '
                'a lower score alone does not establish valid C.']
    # Older notes remain discoverable; do not falsely anchor them to today's source.
    notes=store.all('SELECT text FROM notes WHERE function_id=? ORDER BY id DESC LIMIT 20',fn['id'])
    if any(CONCERN.search(r['text'] or '') for r in notes):
        return prefix + ['### Candidate quality (historical, unanchored report)',
                'An earlier agent reported a candidate-quality concern. Check get_function(section="quality"); '
                'the warning may concern older C.']
    return prefix


def view(net,fn):
    store = net.project_store(fn['project_id'])
    out=['Agent concerns are hypotheses, not semantic proofs. Alternatives retain their recorded scores; '
         'compile and judge them again in the current context. Best C and exact-match status are unchanged.']
    for report in records(net,fn)[-4:]:
        out += [report['agent']+': '+report['warning']]
        if report.get('structured'):
            out += ['Reported issue: '+report['issue']+'; source revision '+report['source_hash'][:20]+'.']
        for ident in report['alternatives']:
            row=store.one('SELECT source,score FROM attempts WHERE id=? AND function_id=?',ident,fn['id'])
            if row and row['source'] is None:
                out += [f'Alternative attempt {ident}: recorded source unavailable; no review or fresh verification inferred.']
            elif row:
                out += [f'Compiled alternative attempt {ident}, historical score {row["score"]} (quality unverified):',
                        '```c\n'+row['source'][:6000]+'\n```']
            if sum(map(len,out))>18000:
                return '\n\n'.join(out)+'\nFurther alternatives omitted.'
    if len(out)==1:
        notes=[r for r in store.all('SELECT * FROM notes WHERE function_id=? ORDER BY id DESC LIMIT 20',fn['id'])
               if CONCERN.search(r['text'] or '')][:2]
        for note in notes:
            out += ['Historical unanchored report: '+note['text'][:600]]
            rows=store.all('SELECT id,source,score FROM attempts WHERE function_id=? AND agent_id=? '
                               'AND created_at<=? AND compiled=1 AND score>0 AND source!=? ORDER BY id DESC LIMIT 3',
                               fn['id'],note['agent_id'],note['created_at'],fn['best_source'] or '')
            for row in rows:
                out += [f'Historical compiled alternative {row["id"]}, score {row["score"]}; quality unverified:',
                        '```c\n'+row['source'][:3000]+'\n```']
    from .app.random_safety import view as checkpoints
    out += checkpoints(net,fn)
    return '\n\n'.join(out)


def reported_source(net, fn, source):
    """Do not reselect a still-reported historical shape after a reviewer chose its correction."""
    digest = hashlib.sha256(source.encode()).hexdigest()
    return any(r['source_hash'] == digest and r.get('review', 'pending') == 'pending' for r in records(net, fn))


def pending(net, fn):
    """List unresolved concerns about the selected source; these do not block work."""
    if fn['status'] != 'open':
        return []
    digest = hashlib.sha256((fn['best_source'] or '').encode()).hexdigest()
    return [r for r in records(net, fn) if r['source_hash'] == digest and r.get('review', 'pending') == 'pending']


def blocked(net, proj):
    """Quality reports are advisory; keep existing callers from treating them as scheduling blocks."""
    return []


def listing(net, slug=''):
    if not slug:
        return [report for project in net.store.all('SELECT slug FROM projects ORDER BY id')
                for report in listing(net, project['slug'])]
    rows = net.project_store(slug).all("SELECT f.*,p.slug FROM functions f JOIN projects p ON p.id=f.project_id "
                         "JOIN settings s ON s.key=? || f.id WHERE (?='' OR p.slug=?) AND f.status='open'",
                         PREFIX, slug, slug)
    return [{'function': fn['name'], 'slug': fn['slug'], **report}
            for fn in rows for report in pending(net, fn)]


def review(net, slug, function, source_hash):
    store = net.project_store(slug)
    from .network import NetworkError
    fn = net._function(function, slug)
    if not any(r['source_hash'] == source_hash for r in pending(net, fn)):
        raise NetworkError('This candidate changed or was already reviewed. Refresh the quality reports.')
    saved = records(net, fn)
    for report in saved:
        if report['source_hash'] == source_hash:
            report['review'] = 'resume'
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (PREFIX+str(fn['id']), json.dumps(saved)))
    from . import claim_stalls
    claim_stalls.resume(net,fn)
    return {'resumed': fn['name']}


ISSUES = ('uninitialized', 'loop_mutation', 'wrong_store', 'wrong_value', 'other')


def report(net, agent, slug, function, revision, issue, reason, alternative_revision=''):
    """Record explicit source-scoped evidence, never an assertion that the judge was wrong."""
    store = net.project_store(slug)
    from .network import NetworkError
    fn = net._function(function,slug)
    if fn['status'] != 'open' or not store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id):
        raise NetworkError('Report a quality concern for an unmatched function you currently hold.')
    if issue not in ISSUES or not isinstance(reason,str) or not 10 <= len(reason.strip()) <= 600:
        raise NetworkError('Choose a supported issue and explain the concern in 10–600 characters.')
    for value in (revision, alternative_revision):
        if value and not re.fullmatch(r'(?:[0-9a-f]{20}|[0-9a-f]{64})',value):
            raise NetworkError('Use a 20-character candidate revision or full source hash.')
    rows = store.all('SELECT id,source,compiled,score FROM attempts WHERE function_id=? AND (source_hash IS NOT NULL OR source IS NOT NULL) ORDER BY id DESC LIMIT 1000',fn['id'])
    def find(value, *, compiled=False):
        return next((r for r in rows if hashlib.sha256(r['source'].encode()).hexdigest().startswith(value)
                     and (not compiled or r['compiled'] and r['score'] is not None)),None)
    bad = find(revision) if revision else None
    best = hashlib.sha256((fn['best_source'] or '').encode()).hexdigest()
    if not revision or not bad and not (fn['best_source'] and best.startswith(revision)):
        raise NetworkError('The reported revision is not saved for this function. Read get_candidate or quality history first.')
    digest = hashlib.sha256(bad['source'].encode()).hexdigest() if bad else best
    alternative = find(alternative_revision,compiled=True) if alternative_revision else None
    if alternative_revision and (not alternative or hashlib.sha256(alternative['source'].encode()).hexdigest()==digest):
        raise NetworkError('The alternative must be a different, compiled and scored revision of this function.')
    saved = records(net,fn)
    previous = next((r for r in saved if r['source_hash']==digest),{})
    item = {'source_hash':digest,'issue':issue,'warning':reason.strip(),'agent':agent.name,
            'alternatives':list(dict.fromkeys(([alternative['id']] if alternative else [])+previous.get('alternatives',[])))[:3],
            'review':previous.get('review','pending'),'structured':True,
            'corrected_attempt': alternative['id'] if alternative else previous.get('corrected_attempt')}
    saved = [r for r in saved if r['source_hash']!=digest]+[item]
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+str(fn['id']),json.dumps(saved[-4:])))
    return 'Candidate issue recorded as a hypothesis, tied to revision '+digest[:20]+'. Best C, score and exact-match status unchanged. Release with notes when finished; agents and automatic repairs can continue while this concern is reviewed.'


def corrected_alternative(net, fn):
    """An explicitly linked compiled alternative, never an automatic semantic verdict."""
    store = net.project_store(fn['project_id'])
    for item in reversed(records(net, fn)):
        if not item.get('structured') or not item.get('corrected_attempt'):
            continue
        row = store.one('SELECT source,score FROM attempts WHERE id=? AND function_id=? AND compiled=1 AND score IS NOT NULL',
                            item['corrected_attempt'], fn['id'])
        if row and row['source']:
            return row
    return None
