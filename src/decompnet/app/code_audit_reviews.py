"""Local audit evidence and human review, independent of function status and matching."""
import hashlib
import json
import time

from ..network import NetworkError

STATES=('unreviewed','needs_investigation','confirmed_code','dismissed')
PAGE_SIZE=50
PAGED_THRESHOLD=1000


def key(pid):return f'code-audit:{pid}'


def indirect_checkpoint(net,pid,revision_id,image,functions,order):
    """Persist completed per-function callback traces for an interrupted full scan."""
    from ..arch import mips_callback_flow, mips_indirect

    store=net.project_store(pid)
    checkpoint_key=f'code-audit-indirect:{pid}'
    with open(mips_callback_flow.__file__,'rb') as source:
        algorithm=hashlib.file_digest(source,'sha256').hexdigest()
    facts=[1,revision_id,hashlib.sha256(image).hexdigest(),order,algorithm,
           mips_indirect.MAX_WORDS,mips_indirect.MAX_CALLS,functions]
    identity=hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()

    def load():
        row=store.one('SELECT value FROM settings WHERE key=?',checkpoint_key)
        try:
            state=json.loads(row['value']) if row else {}
            index,records,limited=state.get('index'),state.get('records'),state.get('limited')
            if (state.get('identity')==identity and type(index) is int and 0<=index<=len(functions)
                    and isinstance(records,list) and type(limited) is bool
                    and all(isinstance(item,dict) and isinstance(item.get('function'),str)
                            and type(item.get('address')) is int and isinstance(item.get('load_address'),list)
                            and len(item['load_address'])==2 and type(item['load_address'][0]) is int
                            and isinstance(item['load_address'][1],dict)
                            and all(isinstance(k,str) and type(v) is int for k,v in item['load_address'][1].items())
                            and isinstance(item.get('evidence_chain',[]),list)
                            for item in records)):
                for item in records:
                    item['load_address']=tuple(item['load_address'])
                return index,records,limited
        except (ValueError,TypeError,AttributeError):
            pass
        return None

    def save(index,records,limited):
        with store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       (checkpoint_key,json.dumps({'identity':identity,'index':index,
                                                   'records':records,'limited':limited})))
    return {'load':load,'save':save}


def runtime_checkpoint(net,pid,revision_id,image):
    """Keep completed breakpoint checks across cancellation, tied to the exact audit input."""
    store=net.project_store(pid)
    checkpoint_key=f'code-audit-runtime:{pid}'
    identity='2:'+hashlib.sha256(image).hexdigest()+':'+revision_id
    row=store.one('SELECT value FROM settings WHERE key=?',checkpoint_key)
    try:
        saved=json.loads(row['value']) if row else {}
        entries=saved['entries'] if saved.get('identity')==identity and isinstance(saved.get('entries'),dict) else {}
    except (ValueError,TypeError,KeyError):
        entries={}

    def checkpoint(item,save=False):
        entry_key=f"{item['rom_start']}:{item['rom_end']}:{item['vram']}"
        if not save:
            value=entries.get(entry_key)
            return value if isinstance(value,dict) and type(value.get('executed')) is bool and (
                not value['executed'] or type(value.get('execution_retrace')) is int) and (
                'direct_call_site' not in value or type(value['direct_call_site']) is int) else None
        entries[entry_key]={'executed':bool(item.get('executed'))}
        if item.get('executed'):entries[entry_key]['execution_retrace']=item['execution_retrace']
        if 'direct_call_site' in item:entries[entry_key]['direct_call_site']=item['direct_call_site']
        with store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       (checkpoint_key,json.dumps({'identity':identity,'entries':entries})))
    return checkpoint


def revision(net,pid):
    proj=net.project_store(pid).one('SELECT config FROM projects WHERE id=?',pid)
    cfg=json.loads(proj['config'])
    version=net.project_store(pid).read_version('audit_layout',pid)
    layout=net.project_store(pid).read_cache.get(('audit-layout',pid),version,
        lambda:[tuple(r) for r in net.project_store(pid).all('SELECT name,vram,rom_offset,size FROM functions WHERE project_id=? ORDER BY id',pid)])
    inputs=json.dumps([6,cfg.get('rom'),cfg.get('segments'),cfg.get('platform'),cfg.get('big_endian'),cfg.get('audit_data_mappings')],sort_keys=True)
    return net.project_store(pid).read_cache.get(('audit-revision',pid),(inputs,version),
        lambda:hashlib.sha256(json.dumps([6,cfg.get('rom'),cfg.get('segments'),cfg.get('platform'),cfg.get('big_endian'),cfg.get('audit_data_mappings'),layout],sort_keys=True).encode()).hexdigest())


def load(net,pid):
    row=net.project_store(pid).one('SELECT value FROM settings WHERE key=?',key(pid))
    state=json.loads(row['value']) if row else {'report':None,'reviews':{}}
    if state['report'] and state['report'].get('input_revision')!=revision(net,pid):
        return {**state,'report':None,'stale':True}
    return state


def execution_candidates(net, pid):
    """Read current unreviewed or confirmed candidates with recorded direct-call evidence."""
    state = load(net, pid)
    if not state.get('report'):
        return []
    if state.get('paged'):
        rows = net.project_store(pid).all(
            "SELECT payload FROM code_audit_candidates WHERE project_id=? AND instr(payload, '\"direct_call_site\"')>0 "
            "AND (review_state IS NULL OR review_state IN ('unreviewed','confirmed_code')) ORDER BY ordinal", pid)
        return [json.loads(row['payload']) for row in rows]
    reviews = state.get('reviews', {})
    return [row for row in state['report'].get('candidates', [])
            if reviews.get(row.get('review_id'), {}).get('state', 'unreviewed') in ('unreviewed', 'confirmed_code')
            and any(isinstance(location, dict) and 'direct_call_site' in location
                    for location in row.get('runtime_locations') or ())]


def decorated(state):
    report=state['report']
    if report is None:return {'candidates':[],'available':False,'stale':state.get('stale',False)}
    return {**report,'available':True,'candidates':[{**r,'review':state['reviews'].get(r['review_id'],
            {'state':'unreviewed','notes':''})} for r in report['candidates']]}


def page(net,pid,number=0,show_reviewed=False):
    """Read one ordered page; old single-value reports keep their original shape."""
    state=load(net,pid)
    if not state.get('paged') or not state['report']:
        return decorated(state)
    number=max(0,number)
    store=net.project_store(pid)
    where='' if show_reviewed else " AND (review_state IS NULL OR review_state NOT IN ('confirmed_code','dismissed'))"
    total=store.one(f'SELECT COUNT(*) n FROM code_audit_candidates WHERE project_id=?{where}',pid)['n']
    number=min(number,max(0,(total-1)//PAGE_SIZE))
    rows=store.all(f'SELECT payload,review FROM code_audit_candidates WHERE project_id=?{where} ORDER BY ordinal LIMIT ? OFFSET ?',pid,PAGE_SIZE,number*PAGE_SIZE)
    candidates=[{**json.loads(r['payload']),'review':json.loads(r['review']) if r['review'] else {'state':'unreviewed','notes':''}} for r in rows]
    return {**state['report'],'available':True,'candidates':candidates,'paged':True,
            'page':number,'eligible_count':total,'candidate_count':state['candidate_count']}


def unresolved(net,pid,state):
    if state.get('paged'):
        return net.project_store(pid).one("SELECT COUNT(*) n FROM code_audit_candidates WHERE project_id=? AND (review_state IS NULL OR review_state!='dismissed')",pid)['n']
    return sum(state.get('reviews',{}).get(r.get('review_id'),{}).get('state')!='dismissed' for r in state['report']['candidates'])


def _record_paged(net,pid,result,check):
    store=net.project_store(pid)
    with store.tx() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(key(pid),)).fetchone()
        old=json.loads(row['value']) if row else {}
        db.execute('CREATE TABLE IF NOT EXISTS code_audit_candidates (project_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, review_id TEXT NOT NULL, payload TEXT NOT NULL, review_state TEXT, review TEXT, PRIMARY KEY(project_id,review_id))')
        db.execute('CREATE INDEX IF NOT EXISTS code_audit_candidates_order ON code_audit_candidates(project_id,ordinal)')
        db.execute('CREATE INDEX IF NOT EXISTS code_audit_candidates_state ON code_audit_candidates(project_id,review_state)')
        db.execute('CREATE TEMP TABLE IF NOT EXISTS audit_prior_reviews (review_id TEXT PRIMARY KEY, review TEXT)')
        db.execute('DELETE FROM audit_prior_reviews')
        db.execute('INSERT INTO audit_prior_reviews SELECT review_id,review FROM code_audit_candidates WHERE project_id=? AND review IS NOT NULL',(pid,))
        db.executemany('INSERT OR REPLACE INTO audit_prior_reviews VALUES(?,?)',((rid,json.dumps(review)) for rid,review in old.get('reviews',{}).items()))
        db.execute('DELETE FROM code_audit_candidates WHERE project_id=?',(pid,))
        for i,candidate in enumerate(result['candidates']):
            if not i%128:check()
            db.execute('INSERT INTO code_audit_candidates SELECT ?,?,?,?,json_extract(review,\'$.state\'),review FROM (SELECT (SELECT review FROM audit_prior_reviews WHERE review_id=?) review)',
                       (pid,i,candidate['review_id'],json.dumps(candidate),candidate['review_id']))
        report={k:v for k,v in result.items() if k!='candidates'}
        state={'report':{**report,'audited_at':time.time()},'reviews':{},'paged':True,'candidate_count':len(result['candidates'])}
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key(pid),json.dumps(state)))
        check()
    return page(net,pid)


def record(net,pid,result,image,*,check=lambda:None):
    # Include the whole image and the examined bounds: reviews cannot silently
    # follow an address into another ROM revision or changed candidate extent.
    if result.get('partial'):raise NetworkError('A partial range scan cannot replace the whole-project coverage audit.')
    result.setdefault('input_revision',revision(net,pid))
    identity=hashlib.sha256(image).hexdigest()
    from ..arch.audit_spool import editable
    for i,r in enumerate(editable(result['candidates'])):
        if not i % 128:check()
        facts=[identity,r['rom_offset'],r['vram'],r['gap_start'],r['gap_end'],r['extent_bytes']]
        r['review_id']=hashlib.sha256(json.dumps(facts).encode()).hexdigest()
    check()
    if len(result['candidates'])>PAGED_THRESHOLD:
        return _record_paged(net,pid,result,check)
    if not isinstance(result['candidates'],list):
        result['candidates']=list(result['candidates'])
    with net.project_store(pid).tx() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(key(pid),)).fetchone()
        state=json.loads(row['value']) if row else {'reviews':{}}
        if state.get('paged'):
            for r in result['candidates']:
                prior=db.execute('SELECT review FROM code_audit_candidates WHERE project_id=? AND review_id=?',(pid,r['review_id'])).fetchone()
                if prior and prior['review']:state['reviews'][r['review_id']]=json.loads(prior['review'])
            db.execute('DELETE FROM code_audit_candidates WHERE project_id=?',(pid,))
            state.pop('paged',None);state.pop('candidate_count',None)
        state['report']={**result,'audited_at':time.time()}
        # Retain old evidence only for currently reported candidates. A changed
        # image or extent becomes fresh work rather than an inherited dismissal.
        state['reviews']={r['review_id']:state['reviews'][r['review_id']] for r in result['candidates'] if r['review_id'] in state['reviews']}
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key(pid),json.dumps(state)))
    return decorated(state)


def review(net,slug,review_id,status,notes,*,compact=False):
    if not isinstance(review_id,str) or len(review_id)!=64:raise NetworkError('Choose a current code-audit candidate.')
    if status not in STATES:raise NetworkError('Choose a valid code-audit review state.')
    if not isinstance(notes,str) or len(notes)>4000:raise NetworkError('Review notes must be text up to 4,000 characters.')
    if status in ('confirmed_code','dismissed') and not notes.strip():raise NetworkError('Record the evidence for confirming or dismissing this candidate.')
    pid=net.project_row(slug)['id']
    with net.project_store(pid).tx() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(key(pid),)).fetchone()
        state=json.loads(row['value']) if row else {'report':None,'reviews':{}}
        if state.get('paged'):
            candidate=db.execute('SELECT payload FROM code_audit_candidates WHERE project_id=? AND review_id=?',(pid,review_id)).fetchone()
            if not state['report'] or state['report'].get('input_revision')!=revision(net,pid) or not candidate:
                raise NetworkError('This audit candidate changed or expired. Run the audit again before reviewing it.')
            decision={'state':status,'notes':notes.strip(),'reviewed_at':time.time()}
            db.execute('UPDATE code_audit_candidates SET review_state=?,review=? WHERE project_id=? AND review_id=?',
                       (status,json.dumps(decision),pid,review_id))
            state['updated_at']=time.time()
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key(pid),json.dumps(state)))
            saved={**json.loads(candidate['payload']),'review':decision}
        else:
            saved=None
        if saved is None and (not state['report'] or state['report'].get('input_revision')!=revision(net,pid) or review_id not in {r['review_id'] for r in state['report']['candidates']}):
            raise NetworkError('This audit candidate changed or expired. Run the audit again before reviewing it.')
        if saved is None:
            state['reviews'][review_id]={'state':status,'notes':notes.strip(),'reviewed_at':time.time()}
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key(pid),json.dumps(state)))
    if saved is not None:
        return {'candidate':saved} if compact else page(net,pid)
    if compact:
        candidate=next(r for r in state['report']['candidates'] if r['review_id']==review_id)
        return {'candidate':{**candidate,'review':state['reviews'][review_id]}}
    return decorated(state)
