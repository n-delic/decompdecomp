"""User-selected single-function workers with an app-owned claim and attempt budget."""
import logging
import threading
import time

from ..network import NetworkError

DEFAULT_TRIES = 300
MAX_TRIES = 10000
BLOCKED_TOOLS = frozenset({'claim_function', 'release_function', 'release_and_continue',
                           'compile_data', 'edit_file', 'split_file', 'split_function', 'join_functions',
                           'tidy_file', 'tidy_done'})
CANDIDATE_TOOLS = frozenset({'compile_and_diff', 'compile_patch', 'compile_variants', 'compile_family',
                             'try_local_repairs'})
TARGET_TOOLS = CANDIDATE_TOOLS | {'try_compiler', 'compiler_experiments', 'search_flags', 'suggest', 'redraft'}
# A helper started with "focus on stuck near-matches" (the `focus_near_matches` option, off by default) turns into a
# focused run when it claims a function this stuck: at least STUCK_ATTEMPTS attempts and a best score above 0 and at
# most STUCK_SCORE. Functions a person matched in under 22 compiles had sat with helpers for 300-440 attempts at
# scores 10-15, passed round by normal rotation; a focused run stays on one function with a large try budget.
# When the focused run ends (match, tries or time) the helper goes back to normal rotation.
STUCK_ATTEMPTS = 50
STUCK_SCORE = 50
CONVERTED_MINUTES = 60
CONVERTED_TRIES = DEFAULT_TRIES


def stuck(fn) -> bool:
    best = fn['best_score']
    return (fn['status'] == 'open' and (fn['attempts'] or 0) >= STUCK_ATTEMPTS
            and best is not None and 0 < best <= STUCK_SCORE)


def bind_conversion(manager, s, agent):
    """Remember an opted-in matching helper's session, so its claims can convert it (convert)."""
    sessions = manager.net.__dict__.setdefault('_focus_conversion', {})
    if s.options.get('focus_near_matches') is True and not s.options.get('task'):
        sessions[agent.id] = (manager, s)
    else:
        sessions.pop(agent.id, None)


def convert(net, agent, slug, function_name):
    """After a claim: an opted-in helper that now holds a stuck near-match becomes a focused run on it. Returns the
    note for the claim's answer, or ''."""
    entry = getattr(net, '_focus_conversion', {}).get(getattr(agent, 'id', None))
    if entry is None or not function_name or scope(net, agent):
        return ''
    manager, s = entry
    if s.slug != slug or s.options.get('focus_id'):
        return ''
    fn = net._function(function_name, slug)
    if not stuck(fn):
        return ''
    now = time.time()
    s.options.update({'focus_id': fn['id'], 'focus_function': fn['name'], 'focus_minutes': CONVERTED_MINUTES,
                      'focus_max_tries': CONVERTED_TRIES, 'focus_since': now,
                      'focus_until': now + CONVERTED_MINUTES * 60, 'focus_tries_used': 0,
                      'focus_converted': {'selection': s.options.get('selection') or {},
                                          'per_session': s.options.get('per_session')}})
    s.options['selection'] = {**(s.options.get('selection') or {}), 'unchanged_compiles': 0, 'stall_compiles': 1000}
    net.set_selection(slug, agent.name, s.options['selection'])
    bind(net, s, agent)
    why = (f"Focused on {fn['name']}: a stuck near-match ({fn['attempts']} attempts, best score {fn['best_score']}). "
           f"Up to {CONVERTED_TRIES} tries or {CONVERTED_MINUTES} minutes, then back to normal rotation.")
    s.push('info', why)
    manager._save(s)
    return ('\n\n🎯 ' + why + ' Stay on this function; read guide("near-match") and keep a numbered edit list. '
            'Do not release it to move on.')


def revert(manager, s, why):
    """A converted focused run ended: the helper returns to normal rotation with its own settings."""
    saved = s.options.pop('focus_converted', None) or {}
    for key in ('focus_id', 'focus_function', 'focus_minutes', 'focus_max_tries', 'focus_since', 'focus_until',
                'focus_tries_used'):
        s.options.pop(key, None)
    s.options['selection'] = saved.get('selection') or {}
    agent = manager.net.store.one('SELECT id, name FROM agents WHERE name=?', s.agent_name)
    if agent is not None:
        manager.net.set_selection(s.slug, agent['name'], s.options['selection'])
        getattr(manager.net, '_agent_focus', {}).pop(agent['id'], None)
    s.push('info', f'{why} Back to normal rotation.')
    manager._save(s)


def configure(net,slug,options,count):
    if not options.get('focus_function'):return options
    if count!=1 or options.get('task'):
        raise ValueError('A focused run starts one matching agent on one function.')
    minutes=options.get('focus_minutes',30)
    if minutes is not None and (type(minutes) is not int or not 1<=minutes<=240):
        raise ValueError('Choose a whole number from 1 to 240 minutes, or leave the time limit blank.')
    tries=options.get('focus_max_tries',DEFAULT_TRIES)
    if type(tries) is not int or not 1<=tries<=MAX_TRIES:
        raise ValueError(f'Choose a whole number from 1 to {MAX_TRIES} tries.')
    if not isinstance(options['focus_function'],str) or len(options['focus_function'])>512:
        raise ValueError('Choose a function name.')
    fn=net._function(options['focus_function'],slug)
    if fn['status']!='open':raise ValueError('Choose an unmatched function that is not set aside.')
    settings = {k:v for k,v in options.items() if k not in ('focus_since', 'focus_until', 'focus_tries_used')}
    return {**settings,'focus_id':fn['id'],'focus_function':fn['name'],'focus_minutes':minutes,
            'focus_max_tries':tries,'per_session':1,'selection':{'unchanged_compiles':0,'stall_compiles':1000}}


def begin(session):
    options = session.options
    minutes = options.get('focus_minutes')
    if minutes is not None:
        deadline = options.setdefault('focus_until', session.started_at + minutes * 60)
        # Older persisted runs only have their original deadline.
        options.setdefault('focus_since', deadline - minutes * 60)
    else:
        options.setdefault('focus_since', session.started_at)
    options.setdefault('focus_max_tries', DEFAULT_TRIES)
    options.setdefault('focus_tries_used', 0)


def bind(net,s,agent):
    scopes = net.__dict__.setdefault('_agent_focus', {})
    if s.options.get('focus_id'):
        scopes[agent.id] = {'id': s.options['focus_id'], 'slug': s.slug, 'until': s.options.get('focus_until'),
                            'stop': s.stop, 'since': s.options.get('focus_since', 0),
                            'tries': s.options.get('focus_max_tries', DEFAULT_TRIES)}
    else:
        scopes.pop(agent.id, None)


def scope(net, agent):
    return getattr(net, '_agent_focus', {}).get(getattr(agent, 'id', None))


def target(net,agent,project,function=None):
    focus = scope(net, agent)
    if not focus:return function
    if focus['stop'].is_set() or (focus['until'] is not None and time.time()>=focus['until']):
        raise NetworkError('This focused run has ended. Saved improvements are retained.')
    if project and project!=focus['slug']:raise NetworkError('This agent is focused on another project.')
    fn=net.project_store(focus['slug']).one('SELECT * FROM functions WHERE id=?',focus['id'])
    if not fn:raise NetworkError('The focused function no longer exists.')
    if function and net._function(function,focus['slug'])['id']!=fn['id']:
        raise NetworkError(f"Stay on {fn['name']}; this focused agent cannot claim other functions.")
    return fn['name']


def used_tries(store, function_id, agent_id, since):
    return store.one('SELECT COUNT(*) AS n FROM attempts WHERE function_id=? AND agent_id=? AND created_at>=?',
                     function_id, agent_id, since)['n']


def limits(net, agent, fn, store):
    focus = scope(net, agent)
    if focus and focus['id'] == fn['id']:
        return focus['tries'], used_tries(store, fn['id'], agent.id, focus['since'])
    return None


def check_target(net, agent, fn):
    focus = scope(net, agent)
    if not focus:
        return
    row = net.project_store(focus['slug']).one('SELECT id,status,project_id FROM functions WHERE id=?', focus['id'])
    if row is None or row['status'] != 'open':
        raise NetworkError('This focused run is complete. Saved improvements are retained.')
    if (row['id'], row['project_id']) != (fn['id'], fn['project_id']):
        raise NetworkError('Only the assigned function can be compiled in this focused run.')


def completion(manager,s,now=None):
    options = s.options
    store = manager.net.project_store(s.slug)
    fn = store.one('SELECT status FROM functions WHERE id=?', options['focus_id'])
    limit = options.get('focus_max_tries')
    if limit is not None:
        agent = manager.net.store.one('SELECT id FROM agents WHERE name=?', s.agent_name)
        used = used_tries(store, options['focus_id'], agent['id'], options['focus_since']) if agent else 0
        options['focus_tries_used'] = used
    if not fn:return 'Focused function was removed.'
    if fn['status'] == 'matched':return 'Verified match. This focused run is complete.'
    if fn['status'] != 'open':return 'Focused function was set aside. This run is complete.'
    if limit is not None and used >= limit:
        return f'Focused try limit reached ({used}/{limit}). Saved improvements are retained.'
    deadline = options.get('focus_until')
    if deadline is not None and (time.time() if now is None else now) >= deadline:
        return 'Focused time limit reached; saved improvements are retained.'
    return None


def run(manager,s):
    if not s.options.get('focus_id') or s.options.get('focus_converted'):return manager._run_work(s)
    finished=threading.Event()
    def stop_if_finished():
        why=completion(manager,s)
        if why and not s.stop.is_set():
            manager.stop(s.id,by='itself')
            manager._finish(s,why)
            return True
        return False
    if stop_if_finished():return
    def watch():
        while not finished.wait(.5) and not s.stop.is_set():
            try:
                if stop_if_finished():return
            except Exception:
                logging.getLogger('decompdecomp').exception('Could not refresh focused worker status')
    watcher=threading.Thread(target=watch,daemon=True,name='focused-agent-completion')
    watcher.start()
    try:return manager._run_work(s)
    finally:finished.set()


def ready(manager,s,agent):
    """Wait for an existing owner without spending a model call or stealing its claim."""
    if not s.options.get('focus_id'):return True
    if s.options.get('focus_converted') and (why := completion(manager, s)):
        revert(manager, s, why)
        return True
    if completion(manager, s):
        s.stop.wait(.5)  # The watcher records the reason and releases the claim.
        return False
    held=manager.net.project_store(s.slug).one('SELECT agent_id FROM claims WHERE function_id=? AND expires_at>?',
                               s.options['focus_id'],time.time())
    if held and held['agent_id']!=agent.id:
        manager._set(s,'waiting','Focused function is claimed by another worker or the C editor. Waiting without AI calls.')
        s.stop.wait(1)
        return False
    return True



def prompt(s):
    tries = s.options.get('focus_max_tries', DEFAULT_TRIES)
    used = s.options.get('focus_tries_used', 0)
    return (f"Work continuously on only the assigned function, {s.options['focus_function']}. "
            f"The app stops this run on a verified match or when all {tries} tries are used ({used} used so far). "
            "The app manages the claim for you. If you need its details again, use get_claims. "
            "Keep testing new hypotheses and saving verified improvements. You can read callees and record "
            "findings with add_note. Use the provider's automatic context compaction when available. "
            "Retain the current best candidate, compiler and flags, scores, failed experiments and next hypotheses. "
            "After compaction recover exact C with get_candidate and consult saved experiments. "
            "Follow the app's focused-run status if generic tool guidance refers to another assignment.")
