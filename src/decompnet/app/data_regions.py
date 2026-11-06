"""Overlay-scoped hypotheses about data mistaken for functions; never a matching verdict."""
import hashlib
import json
import time

from ..network import NetworkError
from .agent_review import view

PREFIX = 'data-region:'


def entries(net, proj, region):
    rows = net.store.all('SELECT id,name,vram,rom_offset,size,target,status FROM functions WHERE project_id=? AND unit IS ? AND vram>=? AND vram<? ORDER BY vram,id',
                         proj['id'], region['unit'], region['start'], region['end'])
    return [r for r in rows if (r['rom_offset'] - r['vram'] if r['rom_offset'] is not None else None) == region['image']]


def stamp(rows):
    return hashlib.sha256(repr([(r['id'], r['vram'], r['size'], r['target']) for r in rows]).encode()).hexdigest()


def records(net, pid):
    return [(r['key'], json.loads(r['value'])) for r in net.store.all('SELECT key,value FROM settings WHERE key LIKE ?', PREFIX+str(pid)+':%')]


def blocked(net, proj):
    from .boundary_repairs import blocked as boundary_blocked
    result = list(boundary_blocked(net,proj))
    for _, region in records(net, proj['id']):
        if region['status'] != 'paused':
            continue
        rows = entries(net, proj, region)
        if stamp(rows) == region['stamp']:
            result.extend(r['id'] for r in rows if r['status'] == 'open' and r['vram']+r['size'] <= region['end'])
    return result


def report(net, agent, slug, function, start, end, reason):
    with view(net):
        proj = net._project(slug)
        if getattr(net, '_link', lambda _: None)(slug) is not None:
            raise NetworkError('Report this region on the room host; its project controls new claims. Send the host your byte evidence in a function note.')
        fn = net._function(function, slug)
        if fn['vram'] is None:
            raise NetworkError('A data region needs a representative function with a known address.')
        try:
            lo, hi = int(str(start), 0), int(str(end), 0)
        except ValueError:
            raise NetworkError('Use hexadecimal start and exclusive end addresses.') from None
        if not lo <= fn['vram'] < hi or not 0 < hi-lo <= 1024*1024:
            raise NetworkError('Choose a region containing the claimed function, no larger than 1 MiB.')
        reason = ' '.join(reason.split())[:2000]
        if not reason:
            raise NetworkError('Describe the bytes and evidence that this region is data; this remains a hypothesis.')
        region = dict(unit=fn['unit'], image=fn['rom_offset']-fn['vram'] if fn['rom_offset'] is not None else None,
                      start=lo, end=hi, status='review', evidence=[])
        rows = entries(net, proj, region)
        if not rows:
            raise NetworkError('No function entries belong to this original image and region.')
        region['stamp'] = stamp(rows)
        identity = hashlib.sha256(repr((region['unit'], region['image'], lo, hi)).encode()).hexdigest()[:24]
        key = PREFIX+str(proj['id'])+':'+identity
        with net.store.tx() as db:
            claim = db.execute('SELECT 1 FROM claims WHERE function_id=? AND agent_id=? AND expires_at>?',
                               (fn['id'], agent.id, time.time())).fetchone()
            if not claim:
                raise NetworkError('Claim the representative function before reporting its data region.')
            old = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            if old:
                prior = json.loads(old['value'])
                if prior['stamp'] == region['stamp']:
                    region['status'] = prior['status']
                region['evidence'] = prior['evidence']
            evidence = dict(agent=agent.name, reason=reason, t=time.time())
            if not any(e['agent']==agent.name and e['reason']==reason for e in region['evidence']):
                region['evidence'] = (region['evidence']+[evidence])[-12:]
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(region)))
            net._event(db, proj['id'], agent.id, 'data_review', fn['name'])
        return f"Data region reported for review: {region['unit']} 0x{lo:X}–0x{hi:X} (exclusive), {len(rows)} entries. No matching status changed. The user can pause this region in Settings → Data region review."


def listing(net, slug):
    with view(net):
        proj = net._project(slug)
        result = []
        for key, region in records(net, proj['id']):
            rows = entries(net, proj, region)
            result.append(dict(region, id=key, rom_start=region['start']+region['image'] if region['image'] is not None else None, stale=stamp(rows)!=region['stamp'],
                               functions=[dict(name=r['name'], status=r['status']) for r in rows]))
        return {'items': result}


def decide(net, slug, key, action):
    if action not in {'pause', 'resume', 'dismiss'}:
        raise NetworkError('Choose pause, resume or dismiss.')
    with view(net):
        proj = net._project(slug)
        with net.store.tx() as db:
            row = db.execute('SELECT value FROM settings WHERE key=? AND key LIKE ?',
                             (key, PREFIX+str(proj['id'])+':%')).fetchone()
            if not row:
                raise NetworkError('This data region is no longer available.')
            region = json.loads(row['value'])
            if action == 'pause' and stamp(entries(net, proj, region)) != region['stamp']:
                raise NetworkError('Region bytes or boundaries changed. Ask an agent to recheck before pausing.')
            region['status'] = {'pause':'paused', 'resume':'review', 'dismiss':'dismissed'}[action]
            net._event(db,proj['id'],None,'data_review',action)
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(region)))
        return listing(net, slug)
