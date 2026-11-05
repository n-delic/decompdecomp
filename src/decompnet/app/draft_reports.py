"""Persist bounded generation/compiler/link diagnostics independently of matching history."""
import hashlib
import json
import time

PREFIX = 'machine-draft:'
GENERATION_PREFIX = 'machine-draft-generation:'


def generation(net, fid):
    row = net.store.one('SELECT value FROM settings WHERE key=?',GENERATION_PREFIX+str(fid))
    return int(row['value']) if row else 0


def key(plan, header, data, fn):
    return hashlib.sha256(repr((plan, header, data, fn['target'], fn['relocs'])).encode()).hexdigest()


def read(net, fid):
    row = net.store.one('SELECT value FROM settings WHERE key=?', PREFIX+str(fid))
    if not row:
        return {}
    try:return json.loads(row['value'])
    except (ValueError,TypeError):return {}


def save(net, fid, report):
    report = dict(report, updated_at=time.time())
    with net.store.tx() as db:
        db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (PREFIX+str(fid),json.dumps(report)))


def failure(report, phase, family, reason):
    report.setdefault('failures', [])
    item = {'phase':phase, 'family':family, 'reason':str(reason)[-3000:]}
    if item not in report['failures']:
        report['failures'] = (report['failures']+[item])[-8:]


def summary(report):
    if not report:return ''
    state = 'matched' if report.get('matched') else 'scored' if report.get('scored') else 'compiled; linking needs review' if report.get('compiled') else 'no compiling draft'
    if report.get('matched') and report.get('host_confirmed') is False:
        state = 'locally matched; awaiting host confirmation'
    failures = report.get('failures') or []
    reason = (' Last '+failures[-1]['phase']+': '+failures[-1]['reason'][-500:]) if failures else ''
    return 'Machine draft check: '+state+'.'+reason
