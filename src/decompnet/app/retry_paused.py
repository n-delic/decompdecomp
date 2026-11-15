"""User-requested fresh turns for a bounded set of model-paused candidates, never claims or calls."""
import json
from contextlib import nullcontext

from .. import claim_stalls, model_checks, pause_scope
from ..network import NetworkError


def retry(net, project, agent, limit=5, log=lambda *_: None):
    if type(limit) is not int or not 1<=limit<=50:
        raise NetworkError('Choose a whole number from 1 to 50 functions to retry.')
    proj=net._project(project)
    identity=pause_scope.identity(net,proj,agent)
    chosen=[]
    checked=0
    after=0
    common=claim_stalls.common_state(net,proj)
    log(f'Checking paused candidates · 0 checked · 0 / {limit} ready')
    # Page metadata only. Stop before loading more functions once the allowance is filled.
    # Recheck filters and stamps under a per-candidate lock so claims cannot race the grant.
    while len(chosen)<limit:
        rows=net.project_store(proj['id']).all("SELECT f.id FROM functions f JOIN settings s ON s.key=? || f.id || ? "
            "WHERE f.project_id=? AND f.status='open' AND f.id>? ORDER BY f.id LIMIT 32",
            claim_stalls.PREFIX,identity[0],proj['id'],after)
        if not rows:break
        for row in rows:
            after=row['id']
            # Synchronize before entering the writer lane, then read the fresh database
            # under the transaction. This lock order also serializes grants with the child.
            net._project(project)
            progress=getattr(net,'progress',None)
            with progress.view() if progress else nullcontext(), net.project_store(proj['id']).tx():
                proj=net.project_store(proj['id']).one('SELECT * FROM projects WHERE id=?',proj['id'])
                if pause_scope.identity(net,proj,agent)!=identity:
                    raise NetworkError('This worker changed models during retry. Check its settings and retry again.')
                policy=net.selection(project,agent.name)
                fn=net.project_store(proj['id']).one("SELECT f.*,s.value AS stall FROM functions f JOIN settings s ON s.key=? || f.id || ? "
                    "WHERE f.id=? AND f.project_id=? AND f.status='open' "
                    "AND NOT EXISTS (SELECT 1 FROM claims WHERE function_id=f.id) "
                    "AND (? IS NULL OR best_score IS NULL OR MAX(0.0,100.0*(1.0-CAST(best_score AS REAL)/MAX(1,n_insns*100)))<?) "
                    "AND (? IS NULL OR (SELECT COUNT(*) FROM attempts a WHERE a.function_id=f.id AND a.agent_id=?)<?) "
                    "AND (?!='no_compiling_c' OR (best_score IS NULL AND NOT EXISTS "
                    "(SELECT 1 FROM attempts WHERE function_id=f.id AND compiled=1)))",
                    claim_stalls.PREFIX,identity[0],after,proj['id'],policy.get('match_below'),policy.get('match_below'),
                    policy.get('max_tries'),agent.id,policy.get('max_tries'),policy.get('strategy',''))
                if fn is not None and policy.get('model_untried') and fn['id'] in model_checks.tried(net,project,agent):
                    fn=None
                if fn is not None:
                    old=json.loads(fn['stall'])
                    stamp=claim_stalls.signature(net,proj,fn,common)
                    count=old.get('count',0) if old.get('stamp')==stamp else old.get('history',{}).get(stamp,0)
                    if count>=claim_stalls.RELEASES:
                        claim_stalls.resume(net,fn,agent,single_turn=True,_stamp=stamp)
                        chosen.append(fn['name'])
            checked+=1
            log(f'Checked {checked} candidates · {len(chosen)} / {limit} ready')
            if len(chosen)>=limit:break
    return dict(resumed=len(chosen),functions=chosen,model=identity[1],
        note='One fresh automatic turn per function for this model. Existing compile limits and filters remain. Waiting workers check again within 30 seconds; stopped workers stay stopped.')
