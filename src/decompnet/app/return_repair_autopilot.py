"""Use the existing autopilot claim and progress for a bounded saved-source return lane."""
import logging

from . import deep_permuter, return_path_search, reviewed_seed_search


def run(net, agent, proj, fn):
    # Cheap eligibility check precedes search construction. Existing completed draft reports do
    # not hide new saved-source repairs; normal draft retries still keep their own cache.
    if not fn['best_source'] or not return_path_search.roots(net,proj,fn):return False
    stop=net.autopilots.get(proj['slug'],{}).get('stop')
    class Log:
        def __call__(self,text):logging.getLogger('decompdecomp').info('autopilot return repair: %s',text)
        def progress(self,value):pass
        def check_cancel(self):
            if stop and stop.is_set():raise deep_permuter.SearchLimit('Stopped')
    search=deep_permuter.Search(net,2,12,Log(),fn)
    try:return reviewed_seed_search.run(search,agent,proj,fn,generated_only=True)['matched']
    except deep_permuter.SearchLimit:return False
