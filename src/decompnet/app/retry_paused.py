"""User-requested fresh turns for a bounded set of model-paused candidates, never claims or calls."""
from .. import claim_stalls, model_checks, pause_scope
from ..network import NetworkError


def retry(net, project, agent, limit=5):
    if type(limit) is not int or not 1<=limit<=50:
        raise NetworkError('Choose a whole number from 1 to 50 functions to retry.')
    with net.store.lock:
        proj=net._project(project)
        paused=set(claim_stalls.blocked(net,proj,agent))
        policy=net.selection(project,agent.name)
        if policy.get('model_untried'):paused-=model_checks.tried(net,project,agent)
        # Apply the same match/attempt filters as automatic selection and leave held work alone.
        rows=net.store.all("SELECT f.* FROM functions f WHERE project_id=? AND status='open' "
            "AND f.id NOT IN (SELECT function_id FROM claims) "
            "AND (? IS NULL OR best_score IS NULL OR MAX(0.0,100.0*(1.0-CAST(best_score AS REAL)/MAX(1,n_insns*100)))<?) "
            "AND (? IS NULL OR (SELECT COUNT(*) FROM attempts a WHERE a.function_id=f.id AND a.agent_id=?)<?) ORDER BY f.id",
            proj['id'],policy.get('match_below'),policy.get('match_below'),policy.get('max_tries'),agent.id,policy.get('max_tries'))
        chosen=[r for r in rows if r['id'] in paused and (policy.get('strategy')!='no_compiling_c' or
            (r['best_score'] is None and not net.store.one('SELECT 1 FROM attempts WHERE function_id=? AND compiled=1',r['id'])))][:limit]
        for fn in chosen:claim_stalls.resume(net,fn,agent,single_turn=True)
        return dict(resumed=len(chosen),functions=[r['name'] for r in chosen],model=pause_scope.identity(net,proj,agent)[1],
                    note='One fresh automatic turn per function for this model. Existing compile limits and filters remain. Waiting workers check again within 30 seconds; stopped workers stay stopped.')
