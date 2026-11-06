"""Optional CPU-only repairs after a real agent submission, before returning another model turn."""
import difflib
import re

from ..network import NetworkError
from . import agent_tokens, local_experiments


def configure(net,data):
    net.agent_local_repairs={'enabled':bool(data.get('agent_local_enabled',False)),
                             'trials':max(1,min(6,int(data.get('agent_local_trials',2)))),
                             'seconds':max(1,min(15,int(data.get('agent_local_seconds',5))))}


def after_tool(net,agent,slug,name,args,text):
    config=getattr(net,'agent_local_repairs',{})
    function=args.get('function','')
    if not config.get('enabled') or name not in {'compile_and_diff','compile_patch'} or not function or ',' in function:
        return text
    if 'COMPILE ERROR' in text or text.startswith(('error:','❌')):return text
    fn=net._function(function,slug)
    if fn['status']!='open' or net.attempt_limit_note(agent,function,slug):return text
    measured=getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
    if not measured or measured.get('score') is None or not 0<measured['score']<=6*max(1,fn['n_insns'] or 0):return text
    try:
        with net.attempt_turn(agent,fn):
            source,revision=agent_tokens.candidate(net,agent,slug,function)
            if measured['source']!=source:return text
            before=net.store.one('SELECT MAX(id) AS id FROM attempts WHERE function_id=?',fn['id'])['id'] or 0
            local_experiments.run(net,agent,slug,function,revision,max_trials=config['trials'],seconds=config['seconds'],
                                  provenance_kind='automatic agent local pass')
            chosen,current=agent_tokens.candidate(net,agent,slug,function)
            trials=net.store.one('SELECT COUNT(*) AS n FROM attempts WHERE function_id=? AND id>?',fn['id'],before)['n']
            changed=chosen!=source
            status=net._function(function,slug)['status']
            # The final revision/budget replaces the pre-pass lines rather than contradicting them.
            text=re.sub(r'\nBudget:[^\n]*|\nCandidate revision [^\n]*','',text)
            message=f'\nAutomatic local pass: {trials} compile attempts; '+('MATCH! Saved by the normal judge.' if status=='matched' else 'selected an improvement.' if changed else 'kept your candidate.')
            if changed:
                text='Submission before the local pass:\n'+text
                selected=getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id'])) or {}
                if status=='open' and selected.get('score') is not None:
                    message+=f" Score {selected['score']}; get_diff shows the selected instructions."
                lines=list(difflib.unified_diff(source.splitlines(),chosen.splitlines(),n=1,lineterm=''))
                message+='\nSelected changes:\n'+'\n'.join(lines[:35])[:2000]
                if len(lines)>35 or sum(map(len,lines))>2000:message+='\nDiff truncated; get_candidate recovers the selected C.'
            if status=='open':
                state=net.stall_status(fn['id'],agent.id)
                message+=f"\nBudget: {max(0,state['after']-state['no_better'])}/{state['after']} without improvement remaining."
            return text+message+f'\nCandidate revision {current}.'
    except NetworkError:
        return text+'\nAutomatic local pass skipped because the claim or work budget changed.'
