"""An automatic dependency pivot needs target evidence and must obey the normal pause policy."""
import hashlib
import json

from .. import claim_stalls
from ..arch.call_evidence import direct
from ..forks.project import ForkError
from ..network import NetworkError


def validate(net, fn, need, agent=None):
    proj = net.project_store(fn['project_id']).one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    if need['id'] in claim_stalls.blocked(net,proj,agent):
        raise NetworkError('needs: this candidate is paused after exhausting unchanged work. Release without needs; '
                           'automatic pivots cannot override pauses. The user can review it in Repair examples.')
    meta = json.loads(need['meta'] or '{}')
    names = [need['name'].split('@')[0],meta.get('symbol'),meta.get('real')]
    assembly = fn['asm'] or ''
    if not assembly and net.fork_slug(proj['slug']):
        try:
            _,fp,_ = net.forks.get(proj['slug'])
            assembly = fp.diff(fp.unit(fn['unit']),json.loads(fn['meta'] or '{}').get('symbol') or fn['name']).target_asm()
        except (ForkError,OSError) as error:
            raise NetworkError('needs: original call evidence is unavailable; release without needs and explain the concern in notes.') from error
    if not direct(assembly,names,need['vram']):
        raise NetworkError('needs: no direct call to this function is recorded in the original assembly. '
                           'Explain indirect, inline or header concerns in notes and release without needs. '
                           'Register differences alone do not require another function to match first.')


def pivot_key(net, fn, need):
    proj = net.project_store(fn['project_id']).one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    stamp = hashlib.sha256(repr((claim_stalls.signature(net,proj,fn),
                                claim_stalls.signature(net,proj,need))).encode()).hexdigest()
    return f'dependency-pivot:{fn["id"]}:{need["id"]}',stamp


def permitted(net, fn, need, agent=None):
    proj = net.project_store(fn['project_id']).one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    if need['id'] in claim_stalls.blocked(net,proj,agent):
        return False
    key,stamp = pivot_key(net,fn,need)
    row = net.project_store(fn['project_id']).one('SELECT value FROM settings WHERE key=?',key)
    return not row or row['value'] != stamp


def remember(net, fn, need):
    key,stamp = pivot_key(net,fn,need)
    with net.project_store(fn['project_id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,stamp))
