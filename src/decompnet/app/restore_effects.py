"""Coalesce optional reuse/archive work after a saved project's exact checks finish."""
import functools
import json
import logging
import threading
from contextvars import ContextVar

_current=ContextVar('saved_restore_effects',default=None)
log=logging.getLogger('decompdecomp')


def defer(net,proj,fid,*,matched):
    state=_current.get()
    if state is None or state['net'] is not net or state['owner']!=threading.get_ident():return False
    project=state['projects'].setdefault(proj['id'],{'slug':proj['slug'],'matched':set(),'improved':set()})
    project['matched' if matched else 'improved'].add(fid)
    return True


def _run(net,pid,pending,logger):
    from . import maintenance, repair_mappings
    logger.check_cancel()
    store=net.project_store(pid)
    proj=store.one('SELECT * FROM projects WHERE id=?',pid)
    if proj is None:return
    # Read current state after the restore: no captured source or historical verdict
    # may overwrite newer foreground work. All these outputs are derived indexes.
    rows=store.all("SELECT id,name FROM functions WHERE project_id=? AND status='matched' "
                   "AND id IN (SELECT value FROM json_each(?))",pid,json.dumps(sorted(pending['matched'])))
    logger.progress({'phase':'Indexing restored matches','done':0,'total':len(rows)})
    if rows:net.twins.index_project(proj)
    if rows and (library:=getattr(net,'match_library',None)):library.archive_project(proj,logger,rows=rows)
    for index,row in enumerate(rows):
        logger.check_cancel();maintenance.checkpoint()
        repair_mappings.harvest(net.twins,proj,row['id'])
        net.twins.after_match(proj,row['id'])
        logger.progress({'phase':'Updating restored match reuse','done':index+1,'total':len(rows)})
    net.cascades.notify_many(proj,pending['matched']|pending['improved'])
    if any(c['status'] in ('checking','waiting') for c in net.cfg(proj).get('range_checks',[])):
        net.rangechecks.recheck(proj['slug'])


def batched(function):
    @functools.wraps(function)
    def run(net,*args,**kwargs):
        if _current.get() is not None:return function(net,*args,**kwargs)
        state={'net':net,'owner':threading.get_ident(),'projects':{}}
        token=_current.set(state)
        try:result=function(net,*args,**kwargs)
        finally:_current.reset(token)
        # Only successful restores schedule work. A cancelled new import may be
        # deleted by its caller; derived indexes can be rebuilt on their next use.
        for pid,pending in state['projects'].items():
            try:_queue(net,pid,pending)
            except Exception:log.exception('Could not queue restored match indexing; normal indexing can retry')
        return result
    return run


def _queue(net,pid,pending):
    def work(logger,pid=pid,pending=pending):return _run(net,pid,pending,logger)
    if startup:=getattr(net,'startup',None):
        startup.add('Index restored matches',work,project=pending['slug'],with_log=True)
        startup.start()
    else:
        # Headless engines have no activity UI; reuse the same optional lane.
        def background(work=work):
            from . import maintenance
            class Logger:
                def __call__(self,line):log.info('%s',line)
                def check_cancel(self):pass
                def progress(self,value):pass
            try:
                with maintenance.slot():work(Logger())
            except Exception:log.exception('Could not index restored matches; normal indexing can retry')
        threading.Thread(target=background,name='restored-match-index',daemon=True).start()
