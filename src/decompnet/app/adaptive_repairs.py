"""Measured repair priorities scoped to build profile and mismatch kind, with exploration retained.

Only new, uncached compiler trials train this policy. Historical counts without a compiler snapshot
must not be attributed to today's settings. These weights choose work, never a match verdict.
"""
import hashlib
import json
import math
import statistics
import time

from ..project import compiler_for
from . import trial_bookkeeping

MIN_TRIALS = 32
# A family with verified matches counts as measured before it has MIN_TRIALS, at most EARLY_MAX_FACTOR until it has them.
# The narrow repairs that match in a few trials apply to few functions, so spread over profiles and mismatch kinds they
# rarely reach MIN_TRIALS in any one cell: on a 1,931-function N64 game 68 cells held 100 matches (mmio_address: 11
# matches in 13 trials) and every one of them was queued as unmeasured, one slot in EXPLORE_EVERY. The smoothing below
# already shrinks a few trials toward the prior; the lower ceiling keeps one lucky hit from leading the queue.
EARLY_MATCHES = 1
EARLY_MAX_FACTOR = 2.0
MIN_FACTOR, MAX_FACTOR = 0.25, 4.0
SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_policy(
 profile TEXT NOT NULL, focus TEXT NOT NULL, family TEXT NOT NULL,
 attempted INTEGER NOT NULL, improved INTEGER NOT NULL, matched INTEGER NOT NULL,
 failed INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL,
 PRIMARY KEY(profile,focus,family))'''


def profile(net, proj, fn):
    """Share ROM measurements only under identical compiler flags/ABI; isolate imported builds."""
    cfg = net.cfg(proj)
    if net.fork_slug(proj['slug']):
        from .. import candidate_scope
        # Imported settings belong to a file/build system. Do not guess their compiler from config.
        settings = ('project', candidate_scope.key(net, proj, fn))
    else:
        settings = ('rom', proj['arch'], bool(proj['big_endian']), cfg.get('platform'),
                    compiler_for(cfg, fn['vram'], fn))
    return hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()


def prepare(net):
    if not getattr(net, '_repair_policy_ready', False):
        with net.store.tx() as db:
            db.execute(SCHEMA)
            db.execute("CREATE TABLE IF NOT EXISTS repair_policy_coverage(origin TEXT PRIMARY KEY, first_at REAL NOT NULL,last_at REAL NOT NULL,attempted INTEGER NOT NULL,improved INTEGER NOT NULL,matched INTEGER NOT NULL,elapsed_ms INTEGER NOT NULL)")
        net._repair_policy_ready = True


POLICY_COUNT = """INSERT INTO repair_policy VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile,focus,family)
        DO UPDATE SET attempted=attempted+excluded.attempted, improved=improved+excluded.improved,
        matched=matched+excluded.matched, failed=failed+excluded.failed,
        elapsed_ms=elapsed_ms+excluded.elapsed_ms"""
POLICY_COLUMNS = ('attempted', 'improved', 'matched', 'failed', 'elapsed_ms')


def record(net, key, focus, family, baseline, score, elapsed_ms=0, origin="local experiments", *, exact=None, coverage=True):
    """Called once per measured trial; unknown/failed results count as spent work, not wins."""
    if not family or not isinstance(baseline, (int, float)) or not math.isfinite(baseline):
        return
    prepare(net)
    improved = isinstance(score, (int, float)) and 0 <= score < baseline
    matched = improved and score == 0 and (exact is None or exact is True)
    now = time.time()
    trial_bookkeeping.count(net.store, POLICY_COUNT,
        (key, focus, family, 1, int(improved), int(matched), int(score is None), max(0, int(elapsed_ms))),
        3, lambda old, new: trial_bookkeeping.sums(old, new, 3))
    if not coverage:  # (a second key for the same trial, such as the statement's shape: one trial in the coverage totals)
        return
    trial_bookkeeping.count(net.store,
        "INSERT INTO repair_policy_coverage VALUES(?,?,?,?,?,?,?) ON CONFLICT(origin) DO UPDATE SET first_at=MIN(first_at,excluded.first_at),last_at=MAX(last_at,excluded.last_at),attempted=attempted+excluded.attempted,improved=improved+excluded.improved,matched=matched+excluded.matched,elapsed_ms=elapsed_ms+excluded.elapsed_ms",
        (origin, now, now, 1, int(improved), int(matched), max(0, int(elapsed_ms))),
        1, lambda old, new: trial_bookkeeping.sums(old, new, 1, first={1}, last={2}), immediate=improved)


def policy_rows(net, key, focus):
    """The stored rows of one profile and focus, plus what this search has counted and not yet written, in the order
    the table gives them (by family): the same rows a flush followed by the read would return, without the
    transaction (the policy is read before every batch of candidates, and a flush each time kept the batch from
    batching: 18% of a lane's writer time)."""
    stored = trial_bookkeeping.stored(net.store, ('policy', key, focus), lambda: [dict(row) for row in net.store.all(
        'SELECT * FROM repair_policy WHERE profile=? AND focus=?', key, focus)])
    rows = {row['family']: dict(row) for row in stored}
    for (profile, queued_focus, family), args in trial_bookkeeping.pending_counters(net.store, POLICY_COUNT).items():
        if profile == key and queued_focus == focus:
            row = rows.setdefault(family, {'profile': key, 'focus': focus, 'family': family,
                                           **dict.fromkeys(POLICY_COLUMNS, 0)})
            for column, amount in zip(POLICY_COLUMNS, args[3:]):
                row[column] += amount
    return [rows[family] for family in sorted(rows)]


def factors(net, key, focus):
    prepare(net)
    result = {}
    rows = policy_rows(net, key, focus)
    times=[r['elapsed_ms']/r['attempted'] for r in rows if r['attempted']>=MIN_TRIALS and r['elapsed_ms']>0]
    typical=statistics.median(times) if times else None
    for row in rows:
        early = row['attempted'] < MIN_TRIALS
        if early and row['matched'] < EARLY_MATCHES:
            continue
        # Smooth sparse evidence, favour verified wins, and never remove an applicable mutation.
        rate = (row['improved'] + 3 * row['matched'] + 1) / (row['attempted'] + MIN_TRIALS)
        # Prefer wins per unit of observed compiler time within this exact profile/focus.
        # Unknown durations are neutral; bounded factors and queue interleaving keep exploration.
        elapsed=row['elapsed_ms']/row['attempted']
        speed=max(.5,min(2,math.sqrt(typical/elapsed))) if typical and elapsed>0 else 1
        # Verified exact throughput leads once a cohort has enough trials. Smooth one
        # win over 32 typical trials so a lucky fast hit cannot monopolize the queue.
        # These are summed elapsed trial durations, not measured CPU seconds.
        prior_ms = MIN_TRIALS * typical if typical else 0
        exact_rate = ((row['matched'] + 1) / (row['elapsed_ms'] + prior_ms)
                      if row['elapsed_ms'] > 0 and prior_ms else None)
        reference = 1 / prior_ms if prior_ms else None
        throughput = math.sqrt(exact_rate / reference) if exact_rate and reference else 1
        factor = min(2, math.sqrt(rate * MIN_TRIALS)) * speed * max(.5, min(2, throughput))
        result[row['family']] = max(MIN_FACTOR, min(EARLY_MAX_FACTOR if early else MAX_FACTOR, factor))
    from . import repair_promotion
    return repair_promotion.cap(net,key,result)


def boosts(net, proj, fn, rows, defaults):
    from .permuter_focus import classify
    measured = factors(net, profile(net, proj, fn), classify(rows))
    from .experiment_evidence import factors as input_factors
    for family, factor in input_factors(net,proj,fn).items():
        measured[family] = min(measured.get(family,1),factor)
    return {family: max(MIN_FACTOR, min(MAX_FACTOR, defaults.get(family, 1) * measured.get(family, 1)))
            for family in defaults.keys() | measured.keys()}


def with_shape(net, proj, fn, boosts, focus, shape):
    """`boosts` scaled by what has worked for this mismatch kind on statements of this shape (`call+field`), learnt under
    the key `focus@shape`; no change until a family has MIN_TRIALS of its own on it."""
    measured = factors(net, profile(net, proj, fn), f'{focus}@{shape}')
    return {family: max(MIN_FACTOR, min(MAX_FACTOR, boosts.get(family, 1) * measured.get(family, 1)))
            for family in boosts.keys() | measured.keys()}


def order(net, proj, fn, rows, families):
    from .permuter_focus import classify
    measured = factors(net, profile(net, proj, fn), classify(rows))
    from .experiment_evidence import factors as input_factors
    for family, factor in input_factors(net,proj,fn).items():
        measured[family] = min(measured.get(family,1),factor)
    from .repair_sequences import starting
    learned = starting(net,proj,fn,rows)
    ranked = sorted(families, key=lambda family: (family not in learned,-measured.get(family, 1)))
    from .repair_ranking import guarded_first
    return guarded_first(explore(ranked, learned, measured))


EXPLORE_EVERY = 3  # one unmeasured family after this many ranked ones


def explore(ranked, learned, measured):
    """`ranked` (best first) with the families nobody has measured yet fed in among the proven ones, one per EXPLORE_EVERY,
    and every family measured as poor (a factor under 1, not `learned` for this function) after all the rest.

    The whole original queue used to be interleaved one for one with the ranking, which pulled a family measured as poor
    back to where it started: on a 588-function N64 game the weight ladders spent 48 of 273 minutes on misses and 20 more
    went to families that never improved anything in hundreds of functions, all of them early in the queue and kept near
    it. A poor family still runs once the others are spent, and a loop's larger budget reaches it."""
    def poor(family):
        return family in measured and measured[family] < 1 and family not in learned
    fresh = [family for family in ranked if family not in measured and family not in learned]
    proven = [family for family in ranked if family not in fresh and not poor(family)]
    result = []
    for index, family in enumerate(proven, 1):
        result.append(family)
        if index % EXPLORE_EVERY == 0 and fresh:
            result.append(fresh.pop(0))
    return result + fresh + [family for family in ranked if poor(family)]


def efficiency(net):
    """Measured trial-time rates, never CPU-time claims or cross-profile causal comparisons."""
    trial_bookkeeping.flush(net.store)
    prepare(net)
    rows = net.store.all('SELECT * FROM repair_policy ORDER BY attempted DESC LIMIT 64')
    return [dict(row) | {'matches_per_compile':row['matched']/row['attempted'] if row['attempted'] else None,
                         'improvements_per_compile':row['improved']/row['attempted'] if row['attempted'] else None,
                         'matches_per_trial_minute':row['matched']*60000/row['elapsed_ms'] if row['elapsed_ms'] else None,
                         'trial_seconds':row['elapsed_ms']/1000,'cpu_seconds':None,
                         'evidence':'measured' if row['attempted']>=MIN_TRIALS else 'sparse'} for row in rows]


def coverage(net):
    """Expose denominators and collection windows; legacy aggregates cannot be compared as one cohort."""
    trial_bookkeeping.flush(net.store)
    prepare(net)
    policy = dict(net.store.one("SELECT COALESCE(SUM(attempted),0) attempted,COALESCE(SUM(improved),0) improved,COALESCE(SUM(matched),0) matched FROM repair_policy"))
    sources = [dict(r) for r in net.store.all("SELECT * FROM repair_policy_coverage ORDER BY origin")]
    deep = None
    for store, _ in net.project_stores():
        if store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deep_permutation_recipes'"):
            row = store.one("SELECT COALESCE(SUM(tested),0) attempted,COALESCE(SUM(improvements),0) improved,COALESCE(SUM(matches),0) matched FROM deep_permutation_recipes")
            if deep is None:deep = {'attempted':0,'improved':0,'matched':0}
            for key in deep:deep[key] += row[key]
    from .experiment_evidence import listing
    return {'efficiency':efficiency(net),'policy':policy,'sources':sources,'deep':deep,'input_effects':listing(net),
            'scope':'All projects. Policy trials are uncached, scoped by build profile and mismatch. Deep history has a different collection window; do not compare rates directly. Coverage by origin starts with this update; elapsed time is summed trial time, not wall-clock duration.'}
