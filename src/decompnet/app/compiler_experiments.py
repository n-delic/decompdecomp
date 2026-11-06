"""Bounded source/profile diagnostics from one revision; never apply compiler choices implicitly."""
import hashlib
import json
import re
import time

from .. import consoles
from ..network import NetworkError
from ..project import compiler_for
from . import agent_targets, agent_tokens


def trial_table(net):
    with net.store.tx() as db:
        db.execute('CREATE TABLE IF NOT EXISTS compiler_trial_evidence('
                   'id TEXT PRIMARY KEY,project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,'
                   'function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,'
                   'agent_id INTEGER NOT NULL,record TEXT NOT NULL,created_at REAL NOT NULL)')
        from ..large_text_storage import install
        install(db, ('compiler_trial_evidence',))


def save_trial(net,fn,key,record):
    trial_table(net)
    with net.store.tx() as db:
        db.execute('INSERT OR IGNORE INTO compiler_trial_evidence VALUES(?,?,?,?,?,?)',
                   (key,fn['project_id'],fn['id'],record['agent'],json.dumps(record),time.time()))


def run(net,agent,slug,function,revision,trials):
    if not isinstance(trials,list) or not 1<=len(trials)<=6:
        raise NetworkError('Send 1–6 source/compiler trials.')
    fn=net._function(function,slug)
    with net.attempt_turn(agent,fn):
        source,actual=agent_tokens.candidate(net,agent,slug,function)
        if actual!=revision:
            raise agent_tokens.stale(net,agent,fn,revision,source,actual)
        proj=net._project(slug);cfg=net.cfg(proj);fork=bool(net.fork_slug(slug))
        planned=[]
        for t in trials:
            if isinstance(t,dict) and any(k in t for k in ('family','target')):
                raise NetworkError('Repair families belong in try_local_repairs(function, revision, families=[...]); compiler_experiments requires compiler=current or an exact compiler ID.')
            if not isinstance(t,dict) or not isinstance(t.get('compiler'),str) or not t['compiler'].strip() or t['compiler'].strip().lower()=='all':
                raise NetworkError('Each trial needs compiler="current" or an explicit compiler ID; all is not a bounded profile.')
            if not isinstance(t.get('flags',''),str):
                raise NetworkError('flags must be a string.')
            current = t['compiler'].strip().lower() == 'current'
            if current:
                if fork:
                    _, fp, _ = net.forks.get(slug)
                    unit = fp.unit(fn['unit'])
                    own = consoles.get(cfg.get('platform')).file_compiler(fp,unit.source) if unit.source else None
                    if not own:
                        raise NetworkError('This imported build does not expose a selectable per-file compiler.')
                else:
                    own = compiler_for(cfg,fn['vram'], fn)['toolchain']
                t = {**t,'compiler':own}
            text=agent_targets.replace_anchor(source,t['old'],t.get('new',''),t.get('occurrence',0)) if 'old' in t else source
            if not fork:
                compiler=net._toolchain_named(cfg,t['compiler'])
                flags=t.get('flags','').split() or (compiler_for(cfg,fn['vram'], fn).get('flags',[]) if current else consoles.default_flags(cfg,compiler))
                t={**t,'compiler':compiler,'flags':' '.join(flags)}
            planned.append((t,text))
        scope=hashlib.sha256(repr((agent_tokens.target_scope(net,fn,revision),dict(fn),
            [dict(r) for r in net.store.all('SELECT * FROM symbols WHERE project_id=? ORDER BY name',fn['project_id'])],
            net._generation.get(fn['project_id'],0))).encode()).hexdigest()
        began=time.monotonic();out=[];seen=set();promotion_hint=''
        ledger=getattr(net,'_compiler_trials',{})
        net._compiler_trials=ledger
        for i,(t,text) in enumerate(planned,1):
            if out and time.monotonic()-began>=60:
                out.append('60-second budget reached; remaining profiles untested.');break
            key=hashlib.sha256(repr((agent.id,slug,fn['id'],scope,text,t['compiler'],t.get('flags',''))).encode()).hexdigest()[:20]
            if key in seen:
                continue
            seen.add(key)
            cached=ledger.get(key) if not fork else None
            if cached:
                result=cached['result'];tag='cached diagnostic'
            else:
                result=net.try_compiler(agent,slug,function,t['compiler'],t.get('flags',''),text,keep_match=False,**({'structured':True} if not fork else {}))
                from .session_followups import diagnostic_completed
                diagnostic_completed(net,agent,fn['name'],slug)
                ledger[key]={'agent':agent.id,'slug':slug,'function':fn['name'],'source':text,'compiler':t['compiler'],
                             'flags':t.get('flags',''),'revision':revision,'scope':scope,'project_config':cfg,'flags_source':'project build owns per-file flags' if fork else 'explicit effective flags','result':result}
                header = (net._context(fn['project_id'],fn['unit']) or {'header':''})['header']
                ledger[key].update(header=header,source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                                   header_sha256=hashlib.sha256(header.encode()).hexdigest())
                save_trial(net,fn,key,ledger[key])
                while len(ledger)>64:
                    del ledger[next(iter(ledger))]
                tag='diagnostic'
            # ROM trials carry the linked judge's boolean; never infer exactness from score zero.
            # Imported builds still expose their legacy textual diagnostic.
            if isinstance(result,dict):
                exact=result.get('exact') is True
                score=result.get('score')
                message=result.get('text','No diagnostic returned.')
            else:
                message=result
                parsed=re.search(r'\bscore\s+(\d+)',message)
                score=int(parsed[1]) if parsed else None
                exact='MATCHES the game byte for byte' in message or 'MATCH!' in message
            status='EXACT (diagnostic only)' if exact else 'score '+str(score) if score is not None else message.splitlines()[0][:180]
            out.append(f'{i}: {status}; {tag}; trial_id={key}; compiler={t["compiler"]}; flags={t.get("flags","") or "platform defaults"}')
            if exact:
                if not fork:
                    promotion_hint=(f'\nTo save this exact pair, retrieve trial {key}, then suggest(function=\"{fn["name"]}\", compiler=\"{t["compiler"]}\", flags=\"{t.get("flags","")}\", source=<the exact trial C>). No ranges argument is needed: function-only suggestions reverify and save only that entry; automatic changes must be enabled, otherwise they wait for review.')
                break
        return '\n'.join(out)+f'\nCandidate revision {actual} unchanged. get_compiler_trial retrieves the exact C/profile pair. No match credited or compiler choice applied.'+promotion_hint


def retrieve(net,agent,slug,function,trial_id,include_header=False):
    fn=net._function(function,slug)
    row = None
    if hasattr(net,'store') and net.store.one("SELECT 1 FROM sqlite_master WHERE name='compiler_trial_evidence'"):
        row=net.store.one('SELECT record FROM compiler_trial_evidence WHERE id=? AND project_id=? AND function_id=?',
                          trial_id,fn['project_id'],fn['id'])
    record=json.loads(row['record']) if row else getattr(net,'_compiler_trials',{}).get(trial_id)
    if (not record or (record['slug'],record['function'])!=(slug,fn['name'])
        or not row and record['agent']!=agent.id):
        raise NetworkError('No saved trial for this project/function. The reference may predate durable trial storage; rerun a bounded experiment if the source/settings are known.')
    # The complete private configuration remains in the ledger for reproduction.
    # Range checks/surveys are unrelated to this selected C/profile pair.
    fields = ('function','source','compiler','flags','revision','scope','flags_source','result','source_sha256','header_sha256')
    compact = {key: record[key] for key in fields if key in record}
    compact['context_id'] = hashlib.sha256(json.dumps(record.get('project_config',{}),sort_keys=True,default=str).encode()).hexdigest()[:20]
    if include_header:compact['header']=record.get('header')
    compact['header_note']='Recorded shared header only; imported project transitive build inputs are owned by the project. Request include_header=true only if needed.'
    compact['verification'] = 'Historical diagnostic only; not a current match or applied compiler choice. Rejudge on current inputs before relying on the score.'
    compact['same_current_source'] = (hashlib.sha256(agent_tokens.candidate(net,agent,slug,function)[0].encode()).hexdigest()==hashlib.sha256(record['source'].encode()).hexdigest()) if hasattr(net,'store') else False
    compact['same_current_scope'] = (record['scope'] == agent_tokens.target_scope(net,fn,agent_tokens.candidate(net,agent,slug,function)[1])) if hasattr(net,'store') else False
    return json.dumps(compact,ensure_ascii=False)
