"""Automatic staged application of narrow return hypotheses, using ordinary match records."""
import tempfile
from contextlib import nullcontext

from ..match_bundle import compiler_identity
from ..network import Network, NetworkError
from ..project import compiler_for
from . import exact_match_inputs, return_repair_plan, return_repair_stage, reviewed_search_seeds


def apply(net, agent, proj, fn, source, log):
    if not reviewed_search_seeds.supported(net,proj):
        return {'matched':False,'reason':'Automatic return repair needs a local ROM project.'}
    if not net.cfg(proj).get('rom',{}).get('file'):
        return {'matched':False,'reason':'Automatic return repair needs the original ROM and rebuild configuration.'}
    store=net.project_store(proj['id'])
    progress=getattr(net,'progress',None)
    gate=progress._project_lock(proj['slug']) if progress else nullcontext()
    committed = []
    verification = {}
    updates = []
    try:
        # Capture under the progress lock, then release it while compiling. Publication
        # reacquires it and rejects any intervening source or on-disk progress change.
        with gate:
            log.check_cancel()
            if progress:progress.sync(proj['slug'],force=True)
            proj=net.project_row(proj['slug']);fn=net._function(fn['name'],proj['slug'])
            if fn['status']!='open':raise NetworkError('The function is no longer open; the diagnostic was not applied.')
            with store.tx():state=return_repair_stage.snapshot(net,proj)
            frozen=return_repair_stage.fingerprint(state)
            files=return_repair_stage.input_files(proj)
            image=files[net.cfg(proj)['rom']['file']]
            updates=return_repair_plan.plan(net,proj,fn,source,state['functions'],state['contexts'],image)
            # The complete build compiles existing matches too; none may escape the snapshot
            # through a custom command or external include path.
            profiles = {}
            for row in state['functions']:
                if row['status']!='matched' and row['id']!=fn['id']:continue
                profile=compiler_for(net.cfg(proj),row['vram'],row)
                flags=profile.get('flags',[])
                flags=flags.split() if isinstance(flags,str) else flags
                profiles[repr(profile)] = (profile,compiler_identity(profile))
                if profile.get('cmd') or profile.get('ro_paths') or any(f.startswith(('-I','-include','-imacros')) for f in flags):
                    raise NetworkError('External compiler inputs require a manual return repair review.')
            for item in updates:
                row=item['function']
                item['exact_inputs']=exact_match_inputs.capture(net,proj,row,{'header':item['header']},item['source'])
                owner=store.one('SELECT agent_id FROM claims WHERE function_id=?',row['id'])
                if owner and owner['agent_id']!=agent.id:
                    raise NetworkError('An affected function is claimed by another worker; retry after it is released.')
        with tempfile.TemporaryDirectory(prefix='decomp-return-repair-',dir=net.workspace) as scratch:
            verification=return_repair_stage.verify(net,state,updates,files,scratch,log)
        log.check_cancel()
        with gate:
            if progress:progress.sync(proj['slug'],force=True)
            with store.tx() as db:
                fresh=net.project_row(proj['slug'])
                if any(compiler_identity(profile)!=identity for profile,identity in profiles.values()):
                    raise NetworkError('A compiler installation changed during rebuild; retry the return repair.')
                if (return_repair_stage.fingerprint(return_repair_stage.snapshot(net,fresh))!=frozen
                        or return_repair_stage.input_files(fresh)!=files):
                    raise NetworkError('Project inputs changed during the staged rebuild; retry the return repair.')
                for item in updates:
                    row=item['function']
                    owner=db.execute('SELECT agent_id FROM claims WHERE function_id=?',(row['id'],)).fetchone()
                    if owner and owner['agent_id']!=agent.id:
                        raise NetworkError('An affected function was claimed during verification; retry after it is released.')
                log.check_cancel()
                # This first after-commit marker distinguishes a callback failure from rollback.
                store.after_commit(lambda:committed.append(True))
                for item in updates:
                    row=item['function'];evidence=item['exact_inputs']
                    # Network bookkeeping captures normal match bundles and defers notifications until
                    # this outer transaction commits. The engine wrapper syncs files too early here.
                    Network._record(net,row,agent,0,item['source'],item['context']['version'],matched=True,
                                    via='autopilot return repair',effective_settings={'compiler':evidence['compiler']},
                                    exact_inputs=evidence)
                net._event(db,proj['id'],agent.id,'analysis',fn['name']+': automatic return repair; affected functions and full image verified')
                if net.cfg(proj).get('auto_commit_matches'):
                    from . import match_commits
                    store.after_commit(lambda:match_commits.enqueue(net,proj,fn['id']))
            log('Return repair applied: '+fn['name']+'; callers and complete rebuilt image still match.')
            return dict(matched=True,affected=[u['function']['name'] for u in updates],**verification)
    except NetworkError as error:
        if committed:
            log('Return repair was committed; follow-up notification failed: '+str(error))
            return dict(matched=True,affected=[u['function']['name'] for u in updates],notification_error=str(error),**verification)
        log('Return repair remains diagnostic: '+str(error))
        return dict(matched=False,reason=str(error),**getattr(error,'verification',{}))
