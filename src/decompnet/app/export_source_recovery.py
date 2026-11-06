"""Repair exact ROM source that cannot export, under the same claim and byte/data judge."""
import time

from ..network import NetworkError
from .cpu_source_recovery import trial_key
from .source_preparation import inputs


def run(net, proj, names, st, log, budget=4, deadline=None):
    """Bounded matched-only pool recovery; success preserves coverage and readability."""
    from ..rom.export import _single_definition
    from .scalar_pool_recovery import variants
    report={'tested':0,'export_repaired':0}
    if not net.is_rom(proj) or net.fork_slug(proj['slug']) or not names:return report
    deadline=deadline if deadline is not None else time.monotonic()+20
    agent,_=net.local_agent('autopilot')
    for name in sorted(names):
        if st.stop.is_set() or time.monotonic()>=deadline or report['tested']>=budget:break
        fn=net.store.one("SELECT * FROM functions WHERE project_id=? AND name=? AND status='matched'",proj['id'],name)
        if not fn or not net.permuter._hold(agent,fn,count_turn=False,seconds=60):continue
        try:
            proj=net.project_row(proj['slug']);fn=net._function(name,proj['slug'])
            context=net._context(proj['id'],fn['unit']);source=fn['matched_source'];header=context['header']
            expected=inputs(net,proj,fn)
            compiled=net._compile(proj,header,source,fn)
            if not compiled.ok:continue
            readiness=net._candidate_checks(fn,proj,compiled.obj).get('export',{})
            if readiness.get('ready') is True or readiness.get('state')=='requires_normalization':continue
            pairs=variants(source,header,fn['asm'] or '',net.reader(proj,fn),net.symbols(proj['id']).resolve,
                           'big' if proj['big_endian'] else 'little')
            for _,body,private in pairs:
                if st.stop.is_set() or time.monotonic()>=deadline or report['tested']>=budget:break
                key=trial_key(expected,body,private,{'export-recovery':2})
                if net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],key):continue
                report['tested']+=1;st.add(variants=1)
                result=net._compile(proj,private,body,fn)
                exact=False
                if result.ok and _single_definition(result.obj,name):
                    candidate,verdict,_=net._evaluate(fn,proj,result.obj)
                    layout=net._candidate_checks(fn,proj,result.obj).get('export',{})
                    exact=candidate is not None and verdict is True and (layout.get('ready') is True or layout.get('state')=='requires_normalization')
                if exact:
                    def guard(name=name,proj=proj,fn=fn,source=source,expected=expected):
                        current=net._function(name,proj['slug'])
                        claim=net.store.one('SELECT agent_id,expires_at FROM claims WHERE function_id=?',fn['id'])
                        if (current['status']!='matched' or current['matched_source']!=source
                                or inputs(net,net.project_row(proj['slug']),current)!=expected or not claim
                                or claim['agent_id']!=agent.id or claim['expires_at']<=time.time()):
                            raise NetworkError('The exact source or claim changed during export repair. Run it again.')
                    with net.progress.judging(proj['slug']),net.progress._project_lock(proj['slug']):
                        net._record(fn,agent,0,body,context['version'],matched=True,via='cpu-export-repair',
                                    effective_settings=result.effective_settings,before_record=guard)
                    report['export_repaired']+=1;log(name+': exact source now passes export layout checks; full repository rebuild is still required.')
                    break
                with net.store.tx() as db:
                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                               (fn['id'],key,'miss','Exact export repair failed byte/data or layout checks'))
        finally:net.permuter._let_go(agent,fn)
    return report
