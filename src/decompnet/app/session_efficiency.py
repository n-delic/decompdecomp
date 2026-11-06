"""Completed-session averages with explicit usage coverage and comparable build cohorts."""
import hashlib
import json

FIELDS=('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')


def context(net,slug,options):
    proj=net.project_row(slug);cfg=net.cfg(proj)
    data={key:cfg.get(key) for key in ('kind','platform','compiler','compiler_ranges')}
    data['selection']=options.get('selection',{})
    data['task']=options.get('task','')
    from .prompt_settings import effective
    templates=effective(net,options)
    key='initial_readable' if options.get('task')=='readable' else 'initial_project' if net.fork_slug(slug) else 'initial_rom'
    data['prompt_profile']=hashlib.sha256(json.dumps([templates[key],templates['instructions']]).encode()).hexdigest()[:16]
    stamp=hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()[:16]
    return {'cohort':stamp,'prompt_profile':data['prompt_profile'],'platform':cfg.get('platform'),'mode':cfg.get('kind','rom'),
            'compiler':(cfg.get('compiler') or {}).get('toolchain','project build'),
            'scope':'Session-start build, work-selection and prompt-profile snapshot; ranges can contain several function profiles.'}


def summarize(records,contexts):
    groups={}
    for r in records:
        ctx=contexts.get(r['id'])
        if not ctx:
            continue  # Legacy sessions have no trustworthy profile snapshot.
        key=(ctx['cohort'],r['kind'],r['model'])
        group=groups.setdefault(key,{**ctx,'kind':r['kind'],'model':r['model'],'sessions':0,'matches':0,'compiles':0,'seconds':0,
                                     'last_session_id':0,'missing_usage_sessions':0,'known_function_sizes':0,'instruction_total':0,**dict.fromkeys(FIELDS,0)})
        group['last_session_id']=max(group['last_session_id'],r['id'])
        workload=ctx.get('workload',{})
        group['known_function_sizes']+=workload.get('known_sizes',0)
        group['instruction_total']+=workload.get('instructions',0)
        group['sessions']+=1;group['matches']+=r['matches'];group['compiles']+=r['compiles']
        group['seconds']+=max(0,(r['ended_at'] or r['started_at'])-r['started_at'])
        group['missing_usage_sessions']+=int(r['tool_calls']>0 and (not r['tokens'] or ('usage_coverage' in r.keys() and r['usage_coverage'] == 'partial')))
        for field in FIELDS:
            group[field]+=r[field]
    for group in groups.values():
        group['average_function_instructions']=group['instruction_total']/group['known_function_sizes'] if group['known_function_sizes'] else None
        count=group['matches']
        for name,field in (('compiles_per_match','compiles'),('seconds_per_match','seconds')):
            group[name]=group[field]/count if count else None
        group['tokens_per_match']={field:group[field]/count for field in FIELDS} if count and not group['missing_usage_sessions'] else None
        group['note']='Completed-session averages include unsuccessful work. Categories are reported usage, not billed cost or exact per-function attribution.'
    return sorted(groups.values(),key=lambda group:group['last_session_id'])


def for_sessions(net,records):
    if not records:
        return []
    ids=[r['id'] for r in records]
    keys=['session-efficiency:'+str(i) for i in ids]
    rows=[]
    for at in range(0,len(keys),400):
        batch=keys[at:at+400]
        rows.extend(net.store.all('SELECT key,value FROM settings WHERE key IN ('+','.join('?' for _ in batch)+')',*batch))
    contexts={int(r['key'].rsplit(':',1)[1]):json.loads(r['value']) for r in rows}
    return summarize(records,contexts)


def finish(net,agent,slug,sid,began):
    """Observe workload size so matching rates are not compared without difficulty context."""
    pid=net.project_row(slug)['id']
    rows=net.store.all('SELECT f.id,f.n_insns FROM attempts a JOIN functions f ON f.id=a.function_id '
                       'WHERE a.agent_id=? AND a.created_at>=? AND f.project_id=? GROUP BY f.id',agent.id,began,pid)
    key='session-efficiency:'+str(sid)
    saved=net.store.one('SELECT value FROM settings WHERE key=?',key)
    if not saved:
        return
    value=json.loads(saved['value'])
    sizes=[r['n_insns'] for r in rows if r['n_insns'] is not None]
    value['workload']={'functions':len(rows),'known_sizes':len(sizes),'instructions':sum(sizes),
                       'small':sum(n<32 for n in sizes),'medium':sum(32<=n<128 for n in sizes),
                       'large':sum(n>=128 for n in sizes)}
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value)))
