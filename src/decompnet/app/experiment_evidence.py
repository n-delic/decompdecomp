"""Input-scoped repair costs: no edits, identical function output and regressions are distinct.

Fingerprints guide budgets only; they never prove matching data sections or replace the exact judge.
Only fresh trials are recorded; the existing search cache owns repeated inputs.
"""
import difflib
import hashlib
import json
import time
from itertools import accumulate

from ..repair_feedback import current_trial
from . import trial_bookkeeping

MIN_TRIALS = 8
SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_input_effects(
 function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE, scope TEXT NOT NULL,
 family TEXT NOT NULL, attempted INTEGER NOT NULL, no_edit INTEGER NOT NULL, same_output INTEGER NOT NULL,
 worse INTEGER NOT NULL, failed INTEGER NOT NULL, improved INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL,
 last_edits TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(function_id,scope,family))'''


def prepare(net):
    if not getattr(net,'_repair_input_effects_ready',False):
        with net.store.tx() as db:
            db.execute(SCHEMA)
        net._repair_input_effects_ready = True


def build_scope(net, proj, fn):
    from ..match_bundle import compiler_identity
    from ..project import compiler_for
    from .agent_tokens import target_scope
    cfg = net.cfg(proj)
    # Imported projects describe their compiler with a label, not ROM toolchain settings.
    # target_scope already fingerprints their unit and build inputs.
    compiler = compiler_for(cfg,fn['vram'], fn) if cfg.get('compiler') and not net.fork_slug(proj['slug']) else {}
    return hashlib.sha256((target_scope(net,fn,'')+repr(compiler_identity(compiler))).encode()).hexdigest()


def scope(net, proj, fn):
    return hashlib.sha256((build_scope(net,proj,fn)+hashlib.sha256((fn['best_source'] or '').encode()).hexdigest()).encode()).hexdigest()


def edits(before, after):
    """Record actual changed spans; a zero-edit proposal must never count as a compiler experiment."""
    left, right = before.splitlines(True), after.splitlines(True)
    left_offsets, right_offsets = [0, *accumulate(map(len,left))], [0, *accumulate(map(len,right))]
    return [{'before_start':left_offsets[a],'before_end':left_offsets[b],
             'after_start':right_offsets[c],'after_end':right_offsets[d]}
            for tag,a,b,c,d in difflib.SequenceMatcher(None,left,right).get_opcodes() if tag != 'equal'][:8]


def output(candidate):
    if candidate is None or not all(hasattr(candidate,key) for key in ('arch','big_endian','code','relocs')):
        return None
    return hashlib.sha256(repr((candidate.arch,candidate.big_endian,candidate.code,
                               sorted(r.key() for r in candidate.relocs))).encode()).hexdigest()


def record(net, fn, key, family, before, after, baseline, score, prior_output, measured_output, elapsed_ms):
    prepare(net)
    changes = edits(before, after)
    improved = score is not None and score < baseline
    same = prior_output is not None and prior_output == measured_output
    trial_bookkeeping.submit(net.store, [(
        """INSERT INTO repair_input_effects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(function_id,scope,family) DO UPDATE SET attempted=attempted+1,
        no_edit=no_edit+excluded.no_edit,same_output=same_output+excluded.same_output,
        worse=worse+excluded.worse,failed=failed+excluded.failed,improved=improved+excluded.improved,
        elapsed_ms=elapsed_ms+excluded.elapsed_ms,last_edits=excluded.last_edits,updated_at=excluded.updated_at""",
        (fn['id'],key,family,1,int(not changes),int(bool(changes) and same),
         int(score is not None and score > baseline),int(bool(changes) and score is None),
         int(improved),max(0,round(elapsed_ms)),json.dumps(changes),time.time()))], immediate=improved)


def factors(net, proj, fn):
    trial_bookkeeping.flush(net.store)
    prepare(net)
    key = scope(net,proj,fn)
    rows = net.store.all('SELECT * FROM repair_input_effects WHERE function_id=? AND scope=?',fn['id'],key)
    result = {}
    for row in rows:
        if row['improved']:
            continue
        if row['attempted']>=4 and row['no_edit']+row['same_output']>=row['attempted']*.8:
            result[row['family']] = .25
        elif row['attempted']>=MIN_TRIALS and row['worse']>=row['attempted']*.8:
            # Regressions can carry useful structural changes; reduce these more gently.
            result[row['family']] = .65
    return result


def listing(net):
    trial_bookkeeping.flush(net.store)
    prepare(net)
    return [dict(row) for row in net.store.all('''SELECT family,SUM(attempted) attempted,SUM(no_edit) no_edit,
      SUM(same_output) same_output,SUM(worse) worse,SUM(failed) failed,SUM(improved) improved,
      SUM(elapsed_ms) elapsed_ms FROM repair_input_effects GROUP BY family ORDER BY attempted DESC LIMIT 12''')]


def measured(permuter, agent, proj, fn, source, best, fork):
    """Wrap a fresh local compiler trial, without counting baselines or cached deep misses as experiments."""
    net = permuter.net
    provenance = current_trial() or {}
    family = provenance.get('family')
    before = fn['best_source'] or ''
    # A random/retained parent can differ from the saved best: use its cached source/output if known.
    cache = net.__dict__.setdefault('_repair_compile_outputs', {})
    base_hash = provenance.get('base_source_sha256') or hashlib.sha256(before.encode()).hexdigest()
    context = build_scope(net,proj,fn)
    previous = cache.get((fn['id'],context,base_hash))
    if previous:
        before = previous[0]
    key = hashlib.sha256((context+base_hash).encode()).hexdigest() if family else None
    no_edit = family and hashlib.sha256(source.encode()).hexdigest() == base_hash
    started = time.monotonic()
    permuter._trial_output = None
    if no_edit:
        before = source
        permuter._trial_outcome = 'no_edit'
        score = None
    else:
        score = permuter._score_work(agent,proj,fn,source,best,fork)
    if not no_edit and score is not None:
        from .search_evidence import remember
        remember(net,agent,proj,fn,source,score)
    measured_output = getattr(permuter,'_trial_output',None)
    if measured_output:
        cache[fn['id'],context,hashlib.sha256(source.encode()).hexdigest()] = (source,measured_output)
        while len(cache) > 256:
            del cache[next(iter(cache))]
    if family:
        record(net,fn,key,family,before,source,best,score,previous[1] if previous else None,
               measured_output,(time.monotonic()-started)*1000)
    return score
