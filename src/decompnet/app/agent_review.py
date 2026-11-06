"""Revision-scoped release evidence and explicit, validated next experiments."""
import hashlib
import json
import re
import time
from contextlib import nullcontext

from ..network import NetworkError

PREFIX = 'agent-review:'
EXPERIMENT = 'agent-experiment:'
CONCERN = re.compile(r'block|ceiling|cannot|can.t|no (?:pure )?C|\$ra|break 9|asm', re.I)
NO_COMPILE = re.compile(r'no compiles?|not compiled|no new compiles?|didn.t compile', re.I)


def digest(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()


def view(net):
    return net.progress.view() if hasattr(net,'progress') else nullcontext()


def facts(net, proj, fn):
    with view(net):
        return _facts(net,proj,fn)


def _facts(net, proj, fn):
    from .. import candidate_scope, claim_stalls
    state = candidate_scope.state(net, proj, fn)
    return {'compiler':digest(state[0]), 'project/build inputs':digest(state[1:4]), 'header':digest(state[4]), 'symbols':digest(state[5]),
            'target':digest(state[6:9]), 'callees':digest(state[9:]),
            'source':digest((fn['best_source'], fn['seed'])),
            'repair rules':digest(claim_stalls.repair_rules()),
            'instructions':digest(json.loads(proj['config']).get('instructions'))}


def load(net, key):
    row = net.store.one('SELECT value FROM settings WHERE key=?', key)
    return json.loads(row['value']) if row else {}


def observe(net, fn, agent, notes, progress, snapshot=None):
    current = net.store.one('SELECT * FROM functions WHERE id=?', fn['id'])
    proj = net.store.one('SELECT * FROM projects WHERE id=?', fn['project_id'])
    key = PREFIX+str(fn['id'])
    if current['status'] != 'open' or not CONCERN.search(notes) and not load(net,key):
        return
    evidence = snapshot if snapshot is not None else facts(net, proj, current)
    concern = bool(CONCERN.search(notes))
    diagnostic=getattr(net,'_function_diagnostic_compiles',{}).get((agent.id,fn['id']))
    compiled = bool(progress and progress['compiles'] or diagnostic and progress and diagnostic>=progress.get('claimed_at',0))
    with net.store.tx() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        prior = json.loads(row['value']) if row else {}
        releases = prior.get('releases', []) if prior.get('facts') == evidence else []
        releases.append({'agent':agent.name, 'at':time.time(), 'notes':notes[:1000], 'compiled':compiled, 'concern':concern})
        state = {'facts':evidence, 'releases':releases[-12:]}
        if prior.get('facts') == evidence:
            state['reviewed'] = prior.get('reviewed', False)
        if prior.get('facts') and prior['facts'] != evidence:
            state['changed'] = [k for k in evidence if evidence[k] != prior['facts'].get(k)]
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key,json.dumps(state)))


def entry(net, proj, fn, state):
    current = facts(net, proj, fn)
    changes = [k for k in current if current[k] != state.get('facts', {}).get(k)]
    releases = state.get('releases', [])
    blockers = [r for r in releases if r.get('concern') and not r.get('compiled')]
    return {'function':fn['name'], 'score':fn['best_score'], 'releases':releases,
            'no_compile_releases':len(blockers), 'needs_review':len(blockers)>=3 and not changes and not state.get('reviewed'),
            'changed':changes or state.get('changed', []), 'current':not changes,
            'note':'Agent reports are hypotheses, not proof of assembly or incorrect C. Status and matches are unchanged.'}


def listing(net, slug):
    with view(net):
        return _listing(net,slug)


def _listing(net, slug):
    proj = net.project_row(slug)
    rows = net.store.all('SELECT f.*,s.value AS review FROM functions f LEFT JOIN settings s ON s.key=? || f.id '
                         "WHERE f.project_id=? AND f.status='open' AND (s.value IS NOT NULL OR EXISTS("
                         "SELECT 1 FROM notes n WHERE n.function_id=f.id AND (n.text LIKE '%no%compile%' OR n.text LIKE '%not compiled%' OR n.text LIKE '%didn%compile%'))) "
                         'ORDER BY f.id LIMIT 200', PREFIX, proj['id'])
    items = []
    for fn in rows:
        if fn['review']:
            item = entry(net,proj,fn,json.loads(fn['review']))
        else:
            notes=net.store.all('SELECT n.text,n.created_at,a.name AS agent FROM notes n LEFT JOIN agents a ON a.id=n.agent_id '
                                'WHERE n.function_id=? ORDER BY n.id DESC LIMIT 12',fn['id'])
            evidence=[{'agent':r['agent'] or 'Earlier agent','notes':r['text'][:1000], 'at':r['created_at'],
                       'compiled':False,'concern':True} for r in notes if NO_COMPILE.search(r['text']) and CONCERN.search(r['text'])]
            if len(evidence)<3:
                continue
            item={'function':fn['name'],'score':fn['best_score'],'releases':evidence,
                  'no_compile_releases':len(evidence),'needs_review':True,'current':False,'historical':True,
                  'changed':['Historical build inputs unrecorded; recheck the concern before retrying'],
                  'note':'Historical agent notes, not verified no-C evidence or proof that current inputs are unchanged.'}
        if item['needs_review'] or item['changed']:
            items.append(item)
    return {'items':items, 'limit':200}


def acknowledge(net, slug, name):
    with view(net):
        return _acknowledge(net,slug,name)


def _acknowledge(net, slug, name):
    # Review metadata uses the saved row; resolving an engine function can start
    # whole-project verification, which is unnecessary for acknowledgment.
    proj = net.project_row(slug)
    fn = net.store.one('SELECT * FROM functions WHERE project_id=? AND name=?',proj['id'],name)
    if fn is None:
        raise NetworkError('That function is no longer in this project. Refresh the review.')
    state = load(net,PREFIX+str(fn['id']))
    if state and state.get('facts') != facts(net,proj,fn):
        raise NetworkError('Inputs changed; refresh the review before acknowledging it.')
    if not state:
        state={'facts':facts(net,proj,fn),'releases':[]}
    state['reviewed'] = True
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+str(fn['id']),json.dumps(state)))
    return {'reviewed':True}


def brief(net, proj, fn):
    state = load(net,PREFIX+str(fn['id']))
    if not state:
        return ''
    item = entry(net,proj,fn,state)
    text = 'Since the previous release: '+', '.join(item['changed'])+' changed. Reassess the recorded blocker before repeating work.' if item['changed'] else ''
    if item['needs_review']:
        text += '\nReview requested: repeated unchanged blocker reports without compilation. This is not a no-C verdict. Read prior evidence; try a specific new experiment or release with the unresolved blocker.'
    return text


def save_experiment(net, agent, slug, name, proposal):
    from .agent_tokens import candidate
    if not isinstance(proposal, dict) or set(proposal) - {'revision','old','new','title'}:
        raise NetworkError('Next experiment needs revision, old, new and optional title only.')
    if not all(isinstance(proposal.get(k),str) and len(proposal[k])<=8000 for k in ('revision','old','new')):
        raise NetworkError('Next experiment needs bounded text revision, old and new fields.')
    source, revision = candidate(net,agent,slug,name)
    if proposal['revision'] != revision or not proposal['old'] or source.count(proposal['old']) != 1 or proposal['old'] == proposal['new']:
        raise NetworkError('Next experiment is stale, ambiguous or makes no edit. Use the current revision and one exact old anchor.')
    fn = net._function(name,slug)
    if not net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id):
        raise NetworkError('Hold the function before saving a next experiment.')
    state = dict(proposal, title=str(proposal.get('title') or 'Next experiment')[:160], source_sha256=digest(source),
                 facts=facts(net,net.project_row(slug),fn), agent=agent.name)
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(EXPERIMENT+str(fn['id']),json.dumps(state)))
    return 'Next experiment saved as an untested patch; it will be revalidated for the next candidate.'


def experiment(net, agent, slug, name):
    from .agent_tokens import candidate
    fn = net._function(name,slug)
    state = load(net,EXPERIMENT+str(fn['id']))
    if not state:
        return 'No structured next experiment is saved.'
    source, revision = candidate(net,agent,slug,name)
    if state['facts'] != facts(net,net.project_row(slug),fn) or state['source_sha256'] != digest(source) or source.count(state['old']) != 1:
        return 'Saved next experiment is outdated for this candidate/build; do not apply it. Read the notes and prepare a fresh experiment.'
    return 'Untested historical proposal; exact anchor validated. Use compile_patch through the ordinary judge:\n'+json.dumps(
        {'function':name,'revision':revision,'old':state['old'],'new':state['new'],'repair_note':state['title']})
