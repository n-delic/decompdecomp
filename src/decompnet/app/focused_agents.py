"""User-selected single-function workers with a persisted wall-clock deadline."""
import logging
import threading
import time


def configure(net,slug,options,count):
    if not options.get('focus_function'):return options
    if count!=1 or options.get('task'):
        raise ValueError('A focused run starts one matching agent on one function.')
    minutes=options.get('focus_minutes',30)
    if type(minutes) is not int or not 1<=minutes<=240:
        raise ValueError('Choose a whole number from 1 to 240 minutes.')
    if not isinstance(options['focus_function'],str) or len(options['focus_function'])>512:
        raise ValueError('Choose a function name.')
    fn=net._function(options['focus_function'],slug)
    if fn['status']!='open':raise ValueError('Choose an unmatched function that is not set aside.')
    return {**options,'focus_id':fn['id'],'focus_function':fn['name'],'focus_minutes':minutes,
            'per_session':1,'selection':{'unchanged_compiles':0,'stall_compiles':1000}}


def bind(net,s,agent):
    if not s.options.get('focus_id'):return
    net.__dict__.setdefault('_agent_focus',{})[agent.id]={
        'id':s.options['focus_id'],'slug':s.slug,'until':s.options['focus_until'],'stop':s.stop}


def target(net,agent,project,function=None):
    focus=getattr(net,'_agent_focus',{}).get(agent.id)
    if not focus:return function
    from ..network import NetworkError
    if focus['stop'].is_set() or time.time()>=focus['until']:
        raise NetworkError('This focused run has ended. Saved improvements are retained.')
    if project and project!=focus['slug']:raise NetworkError('This agent is focused on another project.')
    fn=net.project_store(focus['slug']).one('SELECT * FROM functions WHERE id=?',focus['id'])
    if not fn:raise NetworkError('The focused function no longer exists.')
    if function and net._function(function,focus['slug'])['id']!=fn['id']:
        raise NetworkError(f"Stay on {fn['name']}; this timed agent cannot claim other functions.")
    return fn['name']


def completion(manager,s,now=None):
    if (time.time() if now is None else now)>=s.options['focus_until']:
        return 'Focused time limit reached; saved improvements are retained.'
    fn=manager.net.project_store(s.slug).one('SELECT status FROM functions WHERE id=?',s.options['focus_id'])
    if not fn:return 'Focused function was removed.'
    if fn['status']!='open':return 'Focused function matched or was set aside; this run is complete.'
    return None


def run(manager,s):
    if not s.options.get('focus_id'):return manager._run_work(s)
    finished=threading.Event()
    def stop_if_finished():
        why=completion(manager,s)
        if why:
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
    watcher=threading.Thread(target=watch,daemon=True,name='focused-agent-deadline')
    watcher.start()
    try:return manager._run_work(s)
    finally:finished.set()


def ready(manager,s,agent):
    """Wait for an existing owner without spending a model call or stealing its claim."""
    if not s.options.get('focus_id'):return True
    held=manager.net.project_store(s.slug).one('SELECT agent_id FROM claims WHERE function_id=? AND expires_at>?',
                               s.options['focus_id'],time.time())
    if held and held['agent_id']!=agent.id:
        manager._set(s,'waiting','Focused function is claimed by another worker or the C editor. Waiting without AI calls.')
        s.stop.wait(1)
        return False
    return True



def prompt(s):
    return (f"Work continuously on only {s.options['focus_function']} until the app stops you on a verified match "
            "or the focused deadline. This is one long conversation, not a one-function batch to finish and exit. "
            "Keep testing new hypotheses and saving verified improvements. Do not release the function merely "
            "because a trial failed, a tool-call count was reached, or you have written a status summary. "
            "Use the provider's automatic context compaction when context fills: retain the current best candidate, "
            "compiler/flags, scores, failed experiments, and next hypotheses in the compacted context. "
            "After compaction recover exact C with get_candidate and consult saved experiments instead of guessing. "
            "Do not claim other functions. Stop if the app ends the run; all normal safety and judge rules apply.")
