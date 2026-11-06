"""Keep automatic workers from passing an unchanged failed candidate around indefinitely."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

VERSION = 'local-repairs-v21'
RELEASES = 3
PREFIX = 'agent-stall:'


def common_state(net, proj):
    # Kept as a cheap shared stamp; function-specific build dependencies are hashed below.
    return [VERSION,repair_rules()]


def repair_rules():
    """Persist across restarts; invalidate when installed repair implementations change."""
    app = Path(__file__).parent / 'app'
    # Shared generators and raw hardware readers outside *repairs.py also invalidate old searches.
    paths = sorted(set(app.glob('*repairs.py')) | {app / 'trial_evidence.py', app / 'proposal_progress.py', app / 'repair_source_view.py', app / 'return_contract_evidence.py', app / 'repair_localisation.py', app / 'imported_output.py', app / 'deep_repair_feedback.py', app / 'repair_sequence_lane.py', app / 'branch_call_support.py', app / 'pointer_index_scope.py', app / 'first_difference.py', app / 'match_reconciliation.py', app / 'minimized_recipes.py', app / 'rtl_value_trace.py', app / 'verified_unit_context.py', app / 'repair_promotion.py', app / 'prototype_reuse.py', app / 'equal_value_flow.py', app / 'allocator_call_guards.py', app / 'initializer_clusters.py', app / 'local_experiments.py', app / 'loop_liveness.py', app / 'local_flow.py', app / 'repair_selection.py', app / 'operand_focus.py', app / 'repair_effects.py', app / 'repair_diversity.py', app / 'scan_donor_ranking.py', app / 'inert_pair_search.py', app / 'lifetime_diagnostics.py', app / 'compiler_diagnostics.py', app / 'stack_layout.py', app / 'permuter_focus.py', app / 'search_frontier.py', app / 'allocation_evidence.py', app / 'repair_sequences.py', app / 'expression_recipes.py', app / 'repair_tokens.py', app / 'matching_preflight.py', app / 'allocation_search.py', app / 'repair_ranking.py', app / 'repair_preflight.py', app / 'later_local_operations.py', app / 'later_local_phases.py', app / 'lifetime_donor_ranking.py', app / 'residual_followups.py', app / 'constant_initializer_staging.py', app / 'integer_abi.py', app / 'integer_repair_support.py'})
    paths += [Path(__file__).parent/'arch/mips_local_storage.py',Path(__file__).parent/'arch/mips_hazards.py',Path(__file__).parent/'arch/mips_source_recovery.py',Path(__file__).parent/'arch/mips_materializations.py',Path(__file__).parent/'arch/mips_regions.py',Path(__file__).parent/'arch/mips_search.py',Path(__file__).parent/'arch/mips_live_return_address.py',Path(__file__).parent/'arch/mips_addresses.py']
    return [(p.name,file_digest(p)) for p in paths]


def file_digest(path):
    try:
        stamp = path.stat()
        return _code_digest(path, stamp.st_mtime_ns, stamp.st_ctime_ns, stamp.st_size, stamp.st_ino)
    except OSError:
        return 'unavailable'


@lru_cache(maxsize=512)
def _code_digest(path, mtime, ctime, size, inode):
    # Only implementation files use this cache; game/header byte verification is unchanged.
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature(net, proj, fn, common=None, shared=None):
    from . import candidate_scope
    stamp = common if common is not None else common_state(net,proj)
    return hashlib.sha256(repr((stamp,candidate_scope.key(net,proj,fn,shared),json.loads(proj['config']).get('instructions'),fn['best_source'])).encode()).hexdigest()


def released(net, fn, *, exhausted=False, agent=None):
    proj = net.store.one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    current = net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    if current['status'] != 'open' or not current['best_source']:
        return
    stamp = signature(net,proj,current)
    from .pause_scope import identity
    key = PREFIX+str(fn['id'])+identity(net,proj,agent)[0]
    with net.store.tx() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        old = json.loads(row['value']) if row else {}
        history = old.get('history', {})
        if old.get('stamp'):
            history[old['stamp']] = old.get('count', 0)
        count = RELEASES if exhausted else history.get(stamp, 0)+1
        history[stamp] = count
        history = dict(list(history.items())[-32:])
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps({'stamp':stamp,'count':count,'history':history})))


def blocked(net, proj, agent=None):
    from . import candidate_quality, candidate_scope
    from .pause_scope import identity
    suffix=identity(net,proj,agent)[0]
    # Read small pause records first. Repeated workers of the same recorded model
    # need not reload every source/header or fingerprint every function again.
    records=net.store.all("SELECT f.id,s.value AS stall FROM functions f JOIN settings s ON s.key=? || f.id || ? "
                          "WHERE f.project_id=? AND f.status='open' ORDER BY f.id",PREFIX,suffix,proj['id'])
    possible=[]
    for record in records:
        old=json.loads(record['stall'])
        if max([old.get('count',0),*old.get('history',{}).values()])>=RELEASES:
            possible.append((record['id'],record['stall']))
    result=candidate_quality.blocked(net,proj)
    if not possible:
        return result
    common=common_state(net,proj)
    def calculate():
        rows=net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' "
                           'AND id IN (SELECT value FROM json_each(?)) ORDER BY id',
                           proj['id'],json.dumps([ident for ident,_ in possible]))
        saved={ident:json.loads(value) for ident,value in possible}
        shared=candidate_scope.batch_context(net,proj)
        out=[]
        for fn in rows:
            old=saved[fn['id']]
            stamp=signature(net,proj,fn,common,shared=shared)
            count=old.get('count',0) if old.get('stamp')==stamp else old.get('history',{}).get(stamp,0)
            if count>=RELEASES:
                out.append(fn['id'])
        return tuple(out)
    # Imported build files may change outside SQLite; always inspect those again.
    # ROM scheduling inputs are tracked by revision, while settings are keyed by
    # their actual records. No TTL can hide a new header/source, retry or model.
    if hasattr(net.store,'read_cache') and not net.fork_slug(proj['slug']):
        version=(net.store.read_version('inputs',proj['id']),proj['config'],tuple(possible),repr(common),getattr(net._context,'__func__',net._context))
        paused=net.store.read_cache.get(('model-paused-functions',proj['id'],suffix),version,calculate)
    else:
        paused=calculate()
    return list(dict.fromkeys([*result,*paused]))


def resume(net, fn, agent=None, *, single_turn=False, _stamp=None):
    """Explicit review resets one input; retry grants only one further unchanged release."""
    from .pause_scope import identity
    proj = net.store.one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    # Retry already checked this stamp under the same Store lock; do not hash it twice.
    stamp = _stamp if _stamp is not None else signature(net,proj,fn)
    prefix=PREFIX+str(fn['id'])
    keys=([prefix+identity(net,proj,agent)[0]] if agent is not None else
          [r['key'] for r in net.store.all('SELECT key FROM settings WHERE key=? OR key LIKE ?',prefix,prefix+':%')] or [prefix])
    with net.store.tx() as db:
        for key in keys:
            row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            old = json.loads(row['value']) if row else {}
            history = old.get('history',{})
            if old.get('stamp'):history[old['stamp']] = old.get('count',0)
            count=RELEASES-1 if single_turn else 0
            history[stamp] = count
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       (key,json.dumps({'stamp':stamp,'count':count,'history':dict(list(history.items())[-32:]),'retry_grant':single_turn})))


def grants(net, proj, agent):
    """Explicit grants alone bypass this worker's recent-release cooldown for the same inputs."""
    from .pause_scope import identity
    suffix=identity(net,proj,agent)[0]
    rows=net.store.all("SELECT f.*,s.value AS stall FROM functions f JOIN settings s ON s.key=? || f.id || ? WHERE f.project_id=? AND f.status='open'",PREFIX,suffix,proj['id'])
    common=common_state(net,proj) if rows else None
    return [f['id'] for f in rows if (r:=json.loads(f['stall'])).get('retry_grant')
            and r.get('stamp')==signature(net,proj,f,common)]


def consume(net, proj, fn, agent, db):
    """Consume a fresh turn in the claim transaction, even if its worker subsequently crashes."""
    from .pause_scope import identity
    key=PREFIX+str(fn['id'])+identity(net,proj,agent)[0]
    row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    if not row:return
    record=json.loads(row['value'])
    if not record.get('retry_grant') or record.get('stamp')!=signature(net,proj,fn):return
    record.update(count=RELEASES,retry_grant=False)
    record.setdefault('history',{})[record['stamp']]=RELEASES
    db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(record),key))
