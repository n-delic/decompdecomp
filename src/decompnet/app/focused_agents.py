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
    if not s.options.get('focus_id'):return manager._run_work(s)
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
