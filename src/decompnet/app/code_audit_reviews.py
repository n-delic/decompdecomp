"""Local audit evidence and human review, independent of function status and matching."""
import hashlib
import json
import time

from ..network import NetworkError

STATES=('unreviewed','needs_investigation','confirmed_code','dismissed')


def key(pid):return f'code-audit:{pid}'


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


def decorated(state):
    report=state['report']
    if report is None:return {'candidates':[],'available':False,'stale':state.get('stale',False)}
    return {**report,'available':True,'candidates':[{**r,'review':state['reviews'].get(r['review_id'],
            {'state':'unreviewed','notes':''})} for r in report['candidates']]}


def record(net,pid,result,image,*,check=lambda:None):
    # Include the whole image and the examined bounds: reviews cannot silently
    # follow an address into another ROM revision or changed candidate extent.
    if result.get('partial'):raise NetworkError('A partial range scan cannot replace the whole-project coverage audit.')
    result.setdefault('input_revision',revision(net,pid))
    identity=hashlib.sha256(image).hexdigest()
    for i,r in enumerate(result['candidates']):
        if not i % 128:check()
        facts=[identity,r['rom_offset'],r['vram'],r['gap_start'],r['gap_end'],r['extent_bytes']]
        r['review_id']=hashlib.sha256(json.dumps(facts).encode()).hexdigest()
    check()
    with net.project_store(pid).tx() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(key(pid),)).fetchone()
        state=json.loads(row['value']) if row else {'reviews':{}}
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
        if not state['report'] or state['report'].get('input_revision')!=revision(net,pid) or review_id not in {r['review_id'] for r in state['report']['candidates']}:
            raise NetworkError('This audit candidate changed or expired. Run the audit again before reviewing it.')
        state['reviews'][review_id]={'state':status,'notes':notes.strip(),'reviewed_at':time.time()}
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key(pid),json.dumps(state)))
    if compact:
        candidate=next(r for r in state['report']['candidates'] if r['review_id']==review_id)
        return {'candidate':{**candidate,'review':state['reviews'][review_id]}}
    return decorated(state)
