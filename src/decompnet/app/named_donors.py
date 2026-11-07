"""Same-name verified C leads across project modes, with bounded destination-judged trials.

A meaningful name selects candidates, never establishes identity or a match. ROM contexts are
private and promoted only on improvement; imported destinations keep their own build/header setup.
"""
import hashlib
import json
import re
import time

from .. import toolchains
from ..forks import csource
from ..network import NetworkError
from ..project import compiler_for
from ..repair_feedback import trial as repair_trial
from ..rom.symbols import AUTO_NAME
from . import donor_sources, draft_repairs, free_candidates, repair_ranking, shared_reuse, source_preparation
from .permute import Permuter
from .similar_matches import trial_key

MAX_FUNCTIONS=64
MAX_DONORS=2
MAX_REPAIRS=6
MAX_SECONDS=45
MAX_ATTEMPTS=32


def meaningful(name):
    return (re.fullmatch(r'[A-Za-z_]\w*',name) is not None and len(name)>=3 and not AUTO_NAME.match(name)
            and not re.match(r'(?:FUN_|sub_|nullsub_|thunk_|loc_)[0-9A-Fa-f]+$',name))


def targets(net,proj,only=None):
    # Keep target/source/assembly blobs out of candidate discovery. Load only the bounded
    # meaningful-name window after cursor rotation and any caller restriction.
    rows=[r for r in net.project_store(proj['id']).all("SELECT id,name FROM functions WHERE project_id=? AND status='open' ORDER BY id",proj['id'])
          if meaningful(r['name']) and (only is None or r['id'] in only)]
    key='named-donor-cursor:'+str(proj['id'])
    cursor=net.project_store(proj['id']).one('SELECT value FROM settings WHERE key=?',key)
    after=int(cursor['value']) if cursor else -1
    selected=([r for r in rows if r['id']>after]+[r for r in rows if r['id']<=after])[:MAX_FUNCTIONS]
    if not selected:return []
    full={r['id']:r for r in net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='open' "
          "AND id IN (SELECT value FROM json_each(?))",proj['id'],json.dumps([r['id'] for r in selected]))}
    return [full[r['id']] for r in selected if r['id'] in full]


def raw(net,other,a,proj,b):
    if net.fork_slug(other['slug']):
        return donor_sources.imported(net,other,a)
    source=a['matched_source']
    if not source or csource.is_stub(source):
        return []
    context=net._context(other['id'],a['unit'])
    return [(source,context['header'] if context else '', 'verified ROM source')]


def prepared(net,proj,b,a,source,header,label,job):
    """Prepare imported donors once per actual source/header/compiler content, never once per pass."""
    if net.fork_slug(proj['slug']):
        # The imported destination already has its own include/type closure. Foreign SDK
        # headers must not replace it. Missing types remain an ordinary compiler diagnostic.
        return source,''
    if label=='verified ROM source':
        return source,header
    settings=compiler_for(net.cfg(proj),b['vram'], b)
    key='named-donor-prep-v1:'+donor_sources.signature(source,header,settings)
    old=net.project_store(proj['id']).one('SELECT outcome,feedback,reason FROM reuse_trials WHERE function_id=? AND signature=?',b['id'],key)
    if old:
        if old['outcome']=='prepared':
            pair=json.loads(old['feedback'])
            return pair['source'],pair['header']
        raise NetworkError(old['reason'])
    if free_candidates.exhausted(job):
        raise NetworkError('Named donor preparation budget reached; run Autopilot again.')
    job.reuse_attempts+=1  # preprocessing consumes this pass's CPU budget too
    try:
        pair=donor_sources.preprocess(net,proj,b,source,header)
    except NetworkError as error:
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                       (b['id'],key,'prepare-error',str(error)))
        raise
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (b['id'],key,'prepared',label,json.dumps(dict(zip(('source','header'),pair)))))
    return pair


def destination_scope(net,proj,fn):
    if net.fork_slug(proj['slug']):
        from .source_lead_analysis import destination
        return destination(net,proj,fn)
    # ROM donor trials supply their complete private header. A successful promotion
    # changes the old unit/header, but neither was used to compile this candidate.
    # Include the actual candidate header in trial_key, and all linker inputs here.
    cfg=net.cfg(proj)
    symbols=[tuple(r) for r in net.project_store(proj['id']).all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])]
    payload=[cfg.get('gp'),cfg.get('rom'),proj['arch'],proj['big_endian'],fn['size'],symbols]
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def record(net,proj,b,other,a,outcome,reason):
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                   (proj['id'],b['id'],other['id'],a['id'],outcome,reason[:600],time.time()))


def run(twins,slug,job=None,*,only=None):
    from .twins import Job
    net=twins.net
    proj=net.project_row(slug)
    if slug in net.links:
        return 0
    job=job if job is not None else Job()
    free_candidates.budget(job)
    deadline=min(job.reuse_deadline,time.monotonic()+MAX_SECONDS)
    ceiling=min(job.reuse_limit,job.reuse_attempts+MAX_ATTEMPTS)
    def exhausted():
        return free_candidates.exhausted(job) or time.monotonic()>=deadline or job.reuse_attempts>=ceiling
    others=list(shared_reuse.compatible(net,proj))[:64]
    worker=Permuter(net)
    agent,_=net.local_agent('autopilot-named-donors')
    matched=0
    eligible=targets(net,proj,only)
    from .cross_game_progress import report as publish_progress
    for index, b in enumerate(eligible[:MAX_FUNCTIONS]):
        publish_progress(job, 'Checking same-name source candidates', function=b['name'], done=index, total=len(eligible[:MAX_FUNCTIONS]))
        if exhausted():break
        if only is not None and b['id'] not in only:continue
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('named-donor-cursor:'+str(proj['id']),str(b['id'])))
        if net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?',b['id'],time.time()):continue
        donors=[]
        for other in others:
            donors.extend((other,a) for a in net.project_store(other['id']).all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND name=? ORDER BY id LIMIT ?",other['id'],b['name'],MAX_DONORS))
        # A failed pair must not permanently hide a useful third project.
        cursor_key='named-donor-project-cursor:'+str(b['id'])
        old_cursor=net.project_store(proj['id']).one('SELECT value FROM settings WHERE key=?',cursor_key)
        offset=int(old_cursor['value'])%len(donors) if old_cursor and donors else 0
        donors=donors[offset:]+donors[:offset]
        if donors:
            with net.project_store(proj['id']).tx() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(cursor_key,str((offset+MAX_DONORS)%len(donors))))
        for other,a in donors[:MAX_DONORS]:
            if exhausted():break
            try:
                choices=raw(net,other,a,proj,b)
            except (NetworkError,OSError,ValueError,toolchains.ToolchainError) as error:
                # Visible but no fake compilation/match. Another donor can still help.
                record(net,proj,b,other,a,'skip','same-name donor unavailable: '+str(error))
                continue
            for source,header,label in choices[:2]:
                if exhausted():break
                try:
                    source,header=prepared(net,proj,b,a,source,header,label,job)
                except (NetworkError,toolchains.ToolchainError) as error:
                    record(net,proj,b,other,a,'skip',str(error))
                    continue
                # No claims or shared-header edits merely to discover or preprocess a donor.
                if not worker._hold(agent,b,count_turn=False,seconds=max(1,deadline-time.monotonic())):
                    continue
                try:
                    origin=f"{a['name']} in {other['name']}; {label}".replace('*/','* /').replace('\n',' ')
                    comment=f"/* Same-name source lead: {origin}. Destination verification required. */\n"
                    queue=[('named_donor',comment+source)]
                    repaired=False
                    for family,candidate in queue:
                        if exhausted():break
                        current=net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',b['id'])
                        if current['status']!='open':break
                        fresh=net.project_row(slug)
                        fork=net.fork_slug(slug)
                        scope=destination_scope(net,fresh,current)
                        settings=compiler_for(net.cfg(fresh),current['vram'], current) if not fork else {}
                        key='named-donor-trial-v1:'+trial_key(candidate,header,settings,current,[scope])
                        old=net.project_store(proj['id']).one('SELECT outcome,feedback FROM reuse_trials WHERE function_id=? AND signature=?',b['id'],key)
                        if old and old['outcome']!='match':
                            score=json.loads(old['feedback'] or '{}').get('score')
                            if score is None or current['best_score'] is not None and score>=current['best_score']:
                                continue
                        job.reuse_attempts+=1;job.cross_candidates+=1;job.cross_tried+=1
                        score=None
                        try:
                            with repair_trial({'kind':'same-name donor','family':family,'source_project':other['slug'],'source_function':a['name']}):
                                score=source_preparation.attempt(worker,fresh,current,candidate,header,agent,fork,
                                     lead={'symbol':a['name'],'file':a['unit'],'family':'same-name verified source'})
                            accepted=net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?',b['id'])['status']=='matched'
                            outcome='match' if score==0 and accepted else 'miss'
                            reason=f'same-name donor; {label}; {family}; score {score}'
                        except (NetworkError,toolchains.ToolchainError) as error:
                            # Cache candidate compile/link rejections, but not claim/input races or
                            # unavailable tools. A context/target/profile change creates a new key.
                            if str(error).startswith(('Prepared C did not compile','Prepared declarations emitted','Prepared C could not be linked')):
                                with net.project_store(proj['id']).tx() as db:
                                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                                               (b['id'],key,'error',str(error),json.dumps({'score':None})))
                            record(net,fresh,current,other,a,'skip',str(error));continue
                        with net.project_store(proj['id']).tx() as db:
                            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                                       (b['id'],key,outcome,reason,json.dumps({'score':score})))
                        record(net,fresh,current,other,a,outcome,reason)
                        if outcome=='match':
                            matched+=1;job.cross_matched+=1;job.reuse_matched+=1;job.reuse_bytes+=b['size'] or 0
                            break
                        # One bounded deterministic follow-up. Do not use random AI or change flags.
                        if not repaired and score is not None:
                            repaired=True
                            labels={}
                            context=net.forks.permuter_header(slug,b['name']) if fork else header
                            variants=draft_repairs.variants(candidate,b['name'],shared_reuse.machine(net,proj)[0],b['asm'] or '',
                                                            labels=labels,header=context,limit=None,entry_scope=draft_repairs.promoted_entry_repairs.scope(net,proj,b))
                            order=repair_ranking.order(dict.fromkeys(labels.get(s,'local_repair') for s in variants),
                                                       repair_ranking.evidence(net,b['id'],agent.id))
                            variants.sort(key=lambda s:order.index(labels.get(s,'local_repair')))
                            queue.extend((labels.get(s,'local_repair'),s) for s in variants[:MAX_REPAIRS])
                finally:
                    worker._let_go(agent,b)
            if net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?',b['id'])['status']=='matched':break
    return matched
