"""Local audit evidence and human review, independent of function status and matching."""
import hashlib
import json
import time

from ..network import NetworkError

STATES=('unreviewed','needs_investigation','confirmed_code','dismissed')


def key(pid):return f'code-audit:{pid}'


def revision(net,pid):
    proj=net.project_store(pid).one('SELECT config FROM projects WHERE id=?',pid)
    cfg=json.loads(proj['config'])
    version=net.project_store(pid).read_version('audit_layout',pid)
    layout=net.project_store(pid).read_cache.get(('audit-layout',pid),version,
        lambda:[tuple(r) for r in net.project_store(pid).all('SELECT name,vram,rom_offset,size FROM functions WHERE project_id=? ORDER BY id',pid)])
    inputs=json.dumps([4,cfg.get('rom'),cfg.get('segments'),cfg.get('platform'),cfg.get('big_endian'),cfg.get('audit_data_mappings')],sort_keys=True)
    return net.project_store(pid).read_cache.get(('audit-revision',pid),(inputs,version),
        lambda:hashlib.sha256(json.dumps([4,cfg.get('rom'),cfg.get('segments'),cfg.get('platform'),cfg.get('big_endian'),cfg.get('audit_data_mappings'),layout],sort_keys=True).encode()).hexdigest())


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


def record(net,pid,result,image):
    # Include the whole image and the examined bounds: reviews cannot silently
    # follow an address into another ROM revision or changed candidate extent.
    if result.get('partial'):raise NetworkError('A partial range scan cannot replace the whole-project coverage audit.')
    result.setdefault('input_revision',revision(net,pid))
    identity=hashlib.sha256(image).hexdigest()
    for r in result['candidates']:
        facts=[identity,r['rom_offset'],r['vram'],r['gap_start'],r['gap_end'],r['extent_bytes']]
        r['review_id']=hashlib.sha256(json.dumps(facts).encode()).hexdigest()
    with net.project_store(pid).tx() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(key(pid),)).fetchone()
        state=json.loads(row['value']) if row else {'reviews':{}}
        state['report']={**result,'audited_at':time.time()}
        # Retain old evidence only for currently reported candidates. A changed
        # image or extent becomes fresh work rather than an inherited dismissal.
        state['reviews']={r['review_id']:state['reviews'][r['review_id']] for r in result['candidates'] if r['review_id'] in state['reviews']}
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key(pid),json.dumps(state)))
    return decorated(state)


def review(net,slug,review_id,status,notes):
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
    return decorated(state)
