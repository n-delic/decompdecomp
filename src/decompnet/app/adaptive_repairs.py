"""Measured repair priorities scoped to build profile and mismatch kind, with exploration retained.

Only new, uncached compiler trials train this policy. Historical counts without a compiler snapshot
must not be attributed to today's settings. These weights choose work, never a match verdict.
"""
import hashlib
import json
import math
import statistics
import time
from itertools import zip_longest

from ..project import compiler_for

MIN_TRIALS = 32
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


def record(net, key, focus, family, baseline, score, elapsed_ms=0, origin="local experiments", *, exact=None):
    """Called once per measured trial; unknown/failed results count as spent work, not wins."""
    if not family or not isinstance(baseline, (int, float)) or not math.isfinite(baseline):
        return
    prepare(net)
    improved = isinstance(score, (int, float)) and 0 <= score < baseline
    matched = improved and score == 0 and (exact is None or exact is True)
    with net.store.tx() as db:
        db.execute('''INSERT INTO repair_policy VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile,focus,family)
            DO UPDATE SET attempted=attempted+1, improved=improved+excluded.improved,
            matched=matched+excluded.matched, failed=failed+excluded.failed,
            elapsed_ms=elapsed_ms+excluded.elapsed_ms''',
                   (key, focus, family, 1, int(improved), int(matched),
                    int(score is None), max(0, int(elapsed_ms))))
        now = time.time()
        db.execute("INSERT INTO repair_policy_coverage VALUES(?,?,?,?,?,?,?) ON CONFLICT(origin) DO UPDATE SET last_at=excluded.last_at,attempted=attempted+1,improved=improved+excluded.improved,matched=matched+excluded.matched,elapsed_ms=elapsed_ms+excluded.elapsed_ms",
                   (origin,now,now,1,int(improved),int(matched),max(0,int(elapsed_ms))))


def factors(net, key, focus):
    prepare(net)
    result = {}
    rows=net.store.all('SELECT * FROM repair_policy WHERE profile=? AND focus=?', key, focus)
    times=[r['elapsed_ms']/r['attempted'] for r in rows if r['attempted']>=MIN_TRIALS and r['elapsed_ms']>0]
    typical=statistics.median(times) if times else None
    for row in rows:
        if row['attempted'] < MIN_TRIALS:
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
        result[row['family']] = max(MIN_FACTOR, min(MAX_FACTOR,
            min(2, math.sqrt(rate * MIN_TRIALS)) * speed * max(.5, min(2, throughput))))
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


def order(net, proj, fn, rows, families):
    from .permuter_focus import classify
    measured = factors(net, profile(net, proj, fn), classify(rows))
    from .experiment_evidence import factors as input_factors
    for family, factor in input_factors(net,proj,fn).items():
        measured[family] = min(measured.get(family,1),factor)
    from .repair_sequences import starting
    learned = starting(net,proj,fn,rows)
    ranked = sorted(families, key=lambda family: (family not in learned,-measured.get(family, 1)))
    # Interleave the original queue so a changed policy cannot starve unexplored families.
    return list(dict.fromkeys(family for pair in zip_longest(ranked, families)
                              for family in pair if family is not None))


def efficiency(net):
    """Measured trial-time rates, never CPU-time claims or cross-profile causal comparisons."""
    prepare(net)
    rows = net.store.all('SELECT * FROM repair_policy ORDER BY attempted DESC LIMIT 64')
    return [dict(row) | {'matches_per_compile':row['matched']/row['attempted'] if row['attempted'] else None,
                         'improvements_per_compile':row['improved']/row['attempted'] if row['attempted'] else None,
                         'matches_per_trial_minute':row['matched']*60000/row['elapsed_ms'] if row['elapsed_ms'] else None,
                         'trial_seconds':row['elapsed_ms']/1000,'cpu_seconds':None,
                         'evidence':'measured' if row['attempted']>=MIN_TRIALS else 'sparse'} for row in rows]


def coverage(net):
    """Expose denominators and collection windows; legacy aggregates cannot be compared as one cohort."""
    prepare(net)
    policy = dict(net.store.one("SELECT COALESCE(SUM(attempted),0) attempted,COALESCE(SUM(improved),0) improved,COALESCE(SUM(matched),0) matched FROM repair_policy"))
    sources = [dict(r) for r in net.store.all("SELECT * FROM repair_policy_coverage ORDER BY origin")]
    deep = None
    if net.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deep_permutation_recipes'"):
        deep = dict(net.store.one("SELECT COALESCE(SUM(tested),0) attempted,COALESCE(SUM(improvements),0) improved,COALESCE(SUM(matches),0) matched FROM deep_permutation_recipes"))
    from .experiment_evidence import listing
    return {'efficiency':efficiency(net),'policy':policy,'sources':sources,'deep':deep,'input_effects':listing(net),
            'scope':'All projects. Policy trials are uncached, scoped by build profile and mismatch. Deep history has a different collection window; do not compare rates directly. Coverage by origin starts with this update; elapsed time is summed trial time, not wall-clock duration.'}
