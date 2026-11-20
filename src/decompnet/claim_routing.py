"""Route hard claims using outcomes from comparable starting candidates and builds."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from .project import compiler_for

MIN_CLAIMS = 20
ACTIVE_SECONDS = 3600
HISTORY_SECONDS = 30 * 86400
HISTORY_LIMIT = 2048
PRIOR = 10
WEAK_SHARE = 0.6


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def build_profiles(proj):
    """Return a cheap function-to-profile lookup for one observation of the project build."""
    cfg = json.loads(proj['config'])
    if cfg.get('kind') == 'fork':
        return _imported_profiles(proj, cfg)
    default = cfg.get('compiler')
    if not default:
        return lambda fn: None
    default_profile = _digest(default)

    def profile(fn):
        selected = compiler_for(cfg, fn['vram'], fn)
        return default_profile if selected is default else _digest(selected)

    return profile


def _imported_profiles(proj, cfg):
    from . import consoles
    from .forks.project import ForkError, ForkProject

    root = Path(proj['root'])
    fp = ForkProject(root)
    console = consoles.get(cfg.get('platform'))
    files = []
    for name in ('objdiff.json', 'build.ninja', 'Makefile', 'makefile', 'configure.py', '.decomp.wtf.json'):
        try:
            stat = (root / name).stat()
        except OSError:
            continue
        files.append((name, stat.st_mtime_ns, stat.st_size, stat.st_ino))
    # Imports can use any build system. Only compare within the same unit and observed build controls.
    scope = _digest((str(root), cfg.get('version'), cfg.get('platform'), files)) if files else None
    profiles = {}

    def profile(fn):
        unit = fn['unit']
        if not scope or not unit:
            return None
        if unit not in profiles:
            try:
                source = fp.unit(unit).source
                compiler = console.file_compiler(fp, source) if source else None
            except (ForkError, OSError, ValueError):
                profiles[unit] = None
            else:
                profiles[unit] = _digest((scope, unit, compiler)) if source else None
        return profiles[unit]

    return profile


def candidate_band(fn):
    size = fn['n_insns'] or 0
    size_band = 'small' if size < 32 else 'medium' if size < 128 else 'large'
    score = fn['best_score']
    if score is None:
        starting = 'draft' if fn['has_draft'] else 'unscored'
    elif score <= size * 10:
        starting = 'near'
    elif score <= size * 50:
        starting = 'partial'
    else:
        starting = 'early'
    return size_band + ':' + starting


def start(db, fn, agent, claimed_at, profile):
    if profile is None:
        return
    band = candidate_band(dict(fn) | {'has_draft': bool(fn['seed'] or fn['best_source'])})
    db.execute('INSERT OR REPLACE INTO claim_routing_samples(project_id,function_id,agent_id,claimed_at,profile,band) '
               'VALUES(?,?,?,?,?,?)', (fn['project_id'], fn['id'], agent.id, claimed_at, profile, band))
    db.execute('DELETE FROM claim_routing_samples WHERE project_id=? AND claimed_at<?',
               (fn['project_id'], claimed_at - HISTORY_SECONDS))


def tested(db, proj, fn, agent, won):
    sample = db.execute(
        'SELECT r.claimed_at,r.profile,r.tested FROM claim_routing_samples r JOIN claims c '
        'ON c.function_id=r.function_id AND c.agent_id=r.agent_id AND c.claimed_at=r.claimed_at '
        'WHERE r.function_id=? AND r.agent_id=?', (fn['id'], agent.id)).fetchone()
    if sample is None or (sample['tested'] and not won):
        return
    if sample['profile'] == build_profiles(proj)(fn):
        db.execute('UPDATE claim_routing_samples SET tested=1,won=? WHERE function_id=? AND agent_id=? AND claimed_at=?',
                   (int(won), fn['id'], agent.id, sample['claimed_at']))


def preferences(rows, active, model):
    """Compare only active models with enough completed claims in the same cohort."""
    attempts, wins = defaultdict(Counter), defaultdict(Counter)
    seen = set()
    for row in rows:
        if (name := active.get(row['agent_id'])) is None:
            continue
        cohort = row['profile'], row['band']
        key = cohort, name, row['function_id']
        if key in seen:
            continue
        seen.add(key)
        attempts[cohort][name] += 1
        wins[cohort][name] += row['won']
    preferred, deferred = set(), set()
    for cohort, counts in attempts.items():
        eligible = {name: n for name, n in counts.items() if n >= MIN_CLAIMS}
        if model not in eligible or len(eligible) < 2:
            continue
        pooled = sum(wins[cohort][name] for name in eligible) / sum(eligible.values())
        rates = {name: (wins[cohort][name] + PRIOR * pooled) / (n + PRIOR) for name, n in eligible.items()}
        best = max(rates.values())
        if rates[model] < WEAK_SHARE * best:
            deferred.add(cohort)
        elif rates[model] == best and min(rates.values()) < WEAK_SHARE * best:
            preferred.add(cohort)
    return preferred, deferred


def model_results(store, slug, now, names):
    """Completed, tested hard claims grouped by their starting candidate and function size."""
    from .network import model_of

    rows = store.all(
        'SELECT r.function_id,r.profile,r.band,r.won,r.agent_id FROM claim_routing_samples r '
        'JOIN projects p ON p.id=r.project_id '
        'WHERE p.slug=? AND r.claimed_at>=? AND r.tested=1 AND NOT EXISTS ('
        'SELECT 1 FROM claims c WHERE c.function_id=r.function_id AND c.agent_id=r.agent_id '
        'AND c.claimed_at=r.claimed_at) ORDER BY r.claimed_at DESC LIMIT ?',
        slug, now - HISTORY_SECONDS, HISTORY_LIMIT)
    counts, matches, seen = Counter(), Counter(), set()
    for row in rows:
        name = names.get(row['agent_id'])
        if name is None:
            continue
        key = model_of(name), row['band']
        sample = (*key, row['profile'], row['function_id'])
        if sample in seen:
            continue
        seen.add(sample)
        counts[key] += 1
        matches[key] += row['won']
    results = defaultdict(list)
    for (model, band), count in sorted(counts.items()):
        results[model].append({'band': band, 'claims': count, 'matches': matches[model, band]})
    return results


def routes(store, proj, agent, now, names):
    from .network import ESCALATE_AFTER, NON_MODEL_AGENTS, model_of

    if agent.name in NON_MODEL_AGENTS:
        return [], []
    rows = store.all(
        'SELECT r.function_id,r.agent_id,r.profile,r.band,r.won FROM claim_routing_samples r '
        'WHERE r.project_id=? AND r.claimed_at>=? AND r.tested=1 AND NOT EXISTS ('
        'SELECT 1 FROM claims c WHERE c.function_id=r.function_id AND c.agent_id=r.agent_id '
        'AND c.claimed_at=r.claimed_at) ORDER BY r.claimed_at DESC LIMIT ?',
        proj['id'], now - HISTORY_SECONDS, HISTORY_LIMIT)
    if len(rows) < 2 * MIN_CLAIMS:
        return [], []
    busy = {model_of(names[row['agent_id']]) for row in store.all(
        "SELECT agent_id FROM events WHERE project_id=? AND kind='claim' GROUP BY agent_id HAVING MAX(created_at)>=?",
        proj['id'], now - ACTIVE_SECONDS) if row['agent_id'] in names and names[row['agent_id']] not in NON_MODEL_AGENTS}
    model = model_of(agent.name)
    busy.add(model)
    active = {i: model_of(name) for i, name in names.items() if name not in NON_MODEL_AGENTS and model_of(name) in busy}
    preferred, deferred = preferences(rows, active, model)
    if not preferred and not deferred:
        return [], []
    # When the entire queue has stalled, reserve only its more-released half.
    floor = ESCALATE_AFTER
    if not store.one("SELECT 1 FROM functions WHERE project_id=? AND status='open' AND abandons<? LIMIT 1",
                     proj['id'], floor):
        median = store.one(
            "SELECT abandons FROM functions WHERE project_id=? AND status='open' ORDER BY abandons LIMIT 1 "
            "OFFSET (SELECT MAX(0,(COUNT(*)-1)/2) FROM functions WHERE project_id=? AND status='open')",
            proj['id'], proj['id'])
        if median:
            floor = median['abandons'] + 1
    profile = build_profiles(proj)
    picks, waits = [], []
    matching_bands = {band for _, band in preferred | deferred}
    for row in store.all(
            'SELECT id,name,unit,rom_offset,vram,n_insns,best_score,'
            "(NULLIF(seed,'') IS NOT NULL OR NULLIF(best_source,'') IS NOT NULL) AS has_draft "
            "FROM functions WHERE project_id=? AND status='open' AND abandons>=?",
            proj['id'], floor):
        band = candidate_band(row)
        if band not in matching_bands:
            continue
        cohort = profile(row), band
        if cohort in preferred:
            picks.append(row['id'])
        elif cohort in deferred:
            waits.append(row['id'])
    return picks, waits
