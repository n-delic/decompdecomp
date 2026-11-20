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


def load(net, key, *, project_id=None):
    store = net.project_store(project_id) if project_id is not None else net.store
    row = store.one('SELECT value FROM settings WHERE key=?', key)
    return json.loads(row['value']) if row else {}


def observe(net, fn, agent, notes, progress, snapshot=None):
    store = net.project_store(fn['project_id'])
    current = store.one('SELECT * FROM functions WHERE id=?', fn['id'])
    proj = store.one('SELECT * FROM projects WHERE id=?', fn['project_id'])
    key = PREFIX+str(fn['id'])
    if current['status'] != 'open' or not CONCERN.search(notes) and not load(net,key,project_id=fn['project_id']):
        return
    evidence = snapshot if snapshot is not None else facts(net, proj, current)
    concern = bool(CONCERN.search(notes))
    diagnostic=getattr(net,'_function_diagnostic_compiles',{}).get((agent.id,fn['id']))
    compiled = bool(progress and progress['compiles'] or diagnostic and progress and diagnostic>=progress.get('claimed_at',0))
    with store.tx() as db:
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
    store = net.project_store(slug)
    proj = net.project_row(slug)
    rows = store.all('SELECT f.*,s.value AS review FROM functions f LEFT JOIN settings s ON s.key=? || f.id '
                         "WHERE f.project_id=? AND f.status='open' AND (s.value IS NOT NULL OR EXISTS("
                         "SELECT 1 FROM notes n WHERE n.function_id=f.id AND (n.text LIKE '%no%compile%' OR n.text LIKE '%not compiled%' OR n.text LIKE '%didn%compile%'))) "
                         'ORDER BY f.id LIMIT 200', PREFIX, proj['id'])
    items = []
    for fn in rows:
        if fn['review']:
            item = entry(net,proj,fn,json.loads(fn['review']))
        else:
            notes=store.all('SELECT n.text,n.created_at,a.name AS agent FROM notes n LEFT JOIN agents a ON a.id=n.agent_id '
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
    store = net.project_store(slug)
    proj = net.project_row(slug)
    fn = store.one('SELECT * FROM functions WHERE project_id=? AND name=?',proj['id'],name)
    if fn is None:
        raise NetworkError('That function is no longer in this project. Refresh the review.')
    state = load(net,PREFIX+str(fn['id']),project_id=fn['project_id'])
    if state and state.get('facts') != facts(net,proj,fn):
        raise NetworkError('Inputs changed; refresh the review before acknowledging it.')
    if not state:
        state={'facts':facts(net,proj,fn),'releases':[]}
    state['reviewed'] = True
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+str(fn['id']),json.dumps(state)))
    return {'reviewed':True}


def brief(net, proj, fn):
    state = load(net,PREFIX+str(fn['id']),project_id=fn['project_id'])
    if not state:
        return ''
    item = entry(net,proj,fn,state)
    text = 'Since the previous release: '+', '.join(item['changed'])+' changed. Reassess the recorded blocker before repeating work.' if item['changed'] else ''
    if item['needs_review']:
        text += '\nReview requested: repeated unchanged blocker reports without compilation. This is not a no-C verdict. Read prior evidence; try a specific new experiment or release with the unresolved blocker.'
    return text


class StaleHandoff(NetworkError):
    """The optional experiment no longer describes the candidate the next helper inherits."""


def prepare_experiment(net, agent, slug, fn, proposal, progress):
    """Capture the agent's evidence before the release or its background repairs change inputs."""
    from .agent_tokens import candidate

    fields = {'revision', 'old', 'new', 'title', 'tried', 'changed'}
    if not isinstance(proposal, dict) or set(proposal) - fields:
        raise NetworkError('Use revision, tried, changed and title for a handoff, with optional old and new patch text.')
    limits = {'revision': 64, 'old': 8000, 'new': 8000, 'title': 240, 'tried': 320, 'changed': 320}
    if any(not isinstance(value, str) or len(value) > limits[key] for key, value in proposal.items()):
        raise NetworkError('Keep the handoff fields short and use text for each field.')
    source, revision = candidate(net, agent, slug, fn['name'])
    if not source or proposal.get('revision') != revision:
        raise StaleHandoff('The handoff needs the current candidate revision. Read get_candidate before retrying.')
    if source != (fn['best_source'] or fn['seed']):
        raise StaleHandoff('The next agent inherits the saved candidate. Use get_candidate(base="saved_best") before proposing its next step, or release with notes.')

    patch = 'old' in proposal or 'new' in proposal
    old, new = proposal.get('old', ''), proposal.get('new', '')
    if patch and ('new' not in proposal or not old or source.count(old) != 1 or old == new):
        raise NetworkError('The handoff patch is ambiguous or makes no edit. Supply one exact old anchor and its replacement.')
    if not patch or 'tried' in proposal or 'changed' in proposal:
        if not all(proposal.get(field, '').strip() for field in ('tried', 'changed', 'title')):
            raise NetworkError('Describe what you tried, what changed, and one untested next step in title.')
    store = net.project_store(fn['project_id'])
    at = progress['claimed_at']
    state = dict(proposal, title=proposal.get('title', '').strip() or 'Test the proposed patch',
                 source_sha256=digest(source), facts=facts(net, net.project_row(slug), fn),
                 extent=digest((fn['unit'], fn['size'], fn['n_insns'], fn['asm'])),
                 agent=agent.name, claimed_at=at)
    if patch and patch_tried(net, fn, state, source):
        raise NetworkError('That patch has already been tried. Name a different untested next step.')
    # Check now and on read. The release transaction owns publication of this snapshot.
    if not store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=? AND expires_at>?',
                     fn['id'], agent.id, time.time()):
        raise NetworkError('Hold a current claim before handing off an experiment.')
    return state


def automatic_handoff(net, agent, slug, fn, measured, reason):
    """Keep measured release evidence with the source the next worker will inherit."""
    fn = net._function(fn['name'], slug)
    source = fn['best_source'] or fn['seed'] or ''
    progress = net.claim_progress(agent, fn)
    if not source or not progress or fn['status'] != 'open':
        return None
    from .agent_evidence import build_key
    from .efficiency_feedback import mismatch

    store = net.project_store(fn['project_id'])
    proj = net.project_row(slug)
    existing = load(net, EXPERIMENT + str(fn['id']), project_id=fn['project_id'])
    inherited = existing if existing and applicable(net, fn, slug, source, existing) and 'old' in existing else {}
    recent = store.all(
        'SELECT h.summary FROM attempt_hints h JOIN main.attempts a ON a.id=h.attempt_id '
        'WHERE a.function_id=? AND a.agent_id=? AND a.created_at>=? ORDER BY a.id DESC LIMIT 2',
        fn['id'], agent.id, progress['claimed_at'])
    tried = ' / '.join(row['summary'] for row in recent if row['summary'])
    tried = tried or f'{progress["compiles"]} recorded attempts on the released claim.'
    focus = ''
    if measured.get('source') == source and measured.get('input_scope') == build_key(net, fn)[0]:
        focus = mismatch(measured.get('rows') or (), window=False).strip()
    return dict(inherited, source_sha256=digest(source), facts=facts(net, proj, fn),
                extent=digest((fn['unit'], fn['size'], fn['n_insns'], fn['asm'])), agent=agent.name,
                claimed_at=inherited.get('claimed_at', progress['claimed_at']), tried=tried[:320],
                changed=(reason + (' ' + focus if focus else ''))[:320], automatic=True,
                title=inherited.get('title', 'Read the current search brief and choose a different evidence-backed experiment.'))


def patch_tried(net, fn, state, source):
    from .agent_tokens import compiled_before

    edited = source.replace(state['old'], state['new'], 1)
    if compiled_before(net, fn, edited):
        return True
    if 'claimed_at' not in state:
        return False
    return net.project_store(fn['project_id']).one(
        'SELECT 1 FROM main.attempts WHERE function_id=? AND created_at>=? '
        'AND COALESCE(source_digest,source_hash)=? LIMIT 1',
        fn['id'], state['claimed_at'], hashlib.sha256(edited.encode()).hexdigest()) is not None


def applicable(net, fn, slug, source, state):
    if fn['status'] != 'open' or not source or state.get('source_sha256') != digest(source):
        return False
    if 'extent' in state and state['extent'] != digest((fn['unit'], fn['size'], fn['n_insns'], fn['asm'])):
        return False
    if state['facts'] != facts(net, net.project_row(slug), fn):
        return False
    if 'old' in state:
        return source.count(state['old']) == 1 and not patch_tried(net, fn, state, source)
    # An advisory step has no exact candidate identity. Fresh work supersedes it.
    return not net.project_store(fn['project_id']).one(
        'SELECT 1 FROM main.attempts WHERE function_id=? AND created_at>? LIMIT 1', fn['id'], state['released_at'])


def summary(state):
    label = 'Next check: ' if state.get('automatic') and 'old' not in state else 'Untested next step: '
    lines = ['Previous agent handoff (current candidate/build):', label + state['title']]
    if state.get('tried'):
        lines.append('Tried: ' + state['tried'].strip())
    if state.get('changed'):
        lines.append('Observed: ' + state['changed'].strip())
    return '\n'.join(lines)


def handoff_brief(net, slug, fn, source):
    state = load(net, EXPERIMENT + str(fn['id']), project_id=fn['project_id'])
    if not state or not applicable(net, fn, slug, source, state):
        return ''
    text = summary(state)
    if 'old' in state:
        text += '\nRead get_function(section="experiment") for the validated patch and judge it with compile_patch.'
    return text


def experiment(net, agent, slug, name):
    from .agent_tokens import candidate

    fn = net._function(name, slug)
    store = net.project_store(fn['project_id'])
    state = load(net, EXPERIMENT + str(fn['id']), project_id=fn['project_id'])
    if not state:
        return 'No structured next experiment is saved.'
    if not store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=? AND expires_at>?',
                     fn['id'], agent.id, time.time()):
        return 'Hold the function to check whether a structured next experiment applies.'
    source, revision = candidate(net, agent, slug, name)
    if not applicable(net, fn, slug, source, state):
        return 'Saved next experiment is outdated for this candidate/build or has since been tried. Prepare a fresh experiment.'
    text = summary(state)
    if 'old' in state:
        text += '\nUntested historical proposal; exact anchor validated. Use compile_patch through the ordinary judge:\n' + json.dumps(
            {'function': name, 'revision': revision, 'old': state['old'], 'new': state['new'], 'repair_note': state['title']})
    return text
