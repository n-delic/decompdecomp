"""Display-only activity for nonexclusive single-function searches; never a work lease."""
import json

from ..network import IN_BUILD


class Searches(dict):
    def __init__(self, jobs):
        super().__init__()
        self.jobs=jobs

    def active(self, slug):
        with self.jobs._lock:
            return tuple((name,jid,self.jobs.jobs[jid]['t']) for (project,name),jid in self.items()
                         if project==slug and self.jobs.jobs.get(jid,{}).get('state')=='running')


def active(net, slug):
    searches=getattr(net,'function_searches',None)
    return searches.active(slug) if searches is not None else ()


def activity(net, proj):
    searches={name:(jid,started) for name,jid,started in active(net,proj['slug'])}
    if not searches:return []
    rows=net.store.all('SELECT name,n_insns,best_score,attempts FROM functions '
                       f"WHERE project_id=? AND status='open' AND {IN_BUILD} "
                       'AND name IN (SELECT value FROM json_each(?))',proj['id'],json.dumps(list(searches)))
    return [dict(row) | {'agent':'permuter','claimed_at':searches[row['name']][1],
                        'job':searches[row['name']][0],'nonexclusive':True} for row in rows]
