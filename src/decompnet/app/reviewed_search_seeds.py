"""Explicitly reviewed diagnostic roots; signatures and compiler inputs never change implicitly."""
import hashlib
import json
import math
import time

from .. import candidate_scope, toolchains
from ..network import NetworkError
from ..project import compiler_for
from .source_next_steps import signatures

LIMIT = 3


def supported(net, proj):
    return bool(getattr(net, 'is_rom', lambda _: False)(proj) and net.cfg(proj).get('compiler') and not net.fork_slug(proj['slug'])
                and proj['slug'] not in net.links)


def scope(net, proj, fn, source):
    """Include selected callers as well as the candidate's compiler, target and dependencies."""
    callers = net.store.all("SELECT id,matched_source,best_source FROM functions WHERE project_id=? AND id!=? "
                            "AND (instr(matched_source,?)>0 OR instr(best_source,?)>0) ORDER BY id",
                            proj['id'], fn['id'], fn['name'], fn['name'])
    evidence = [(r['id'], hashlib.sha256((r['matched_source'] or r['best_source'] or '').encode()).hexdigest())
                for r in callers]
    fn = dict(fn) | {'best_source':source, 'seed':None}
    profile = compiler_for(net.cfg(proj),fn['vram'],fn)
    return hashlib.sha256(repr((candidate_scope.key(net, proj, fn), evidence,
                               toolchains.installed(profile.get('toolchain','')))).encode()).hexdigest()


def ensure(net):
    with net.store.tx() as db:
        db.execute('CREATE TABLE IF NOT EXISTS reviewed_search_seeds('
                   'trial_id TEXT PRIMARY KEY,function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,'
                   'scope TEXT NOT NULL,reviewer INTEGER,review_note TEXT NOT NULL,enabled INTEGER NOT NULL,created_at REAL NOT NULL)')


def record(net, fn, trial_id):
    if not net.store.one("SELECT 1 FROM sqlite_master WHERE name='compiler_trial_evidence'"):
        raise NetworkError('Run compiler_experiments first and retain its trial ID.')
    row = net.store.one('SELECT record FROM compiler_trial_evidence WHERE id=? AND function_id=? AND project_id=?',
                        trial_id, fn['id'], fn['project_id'])
    if not row:
        raise NetworkError('Choose a diagnostic trial from this project and function.')
    try:
        trial = json.loads(row['record'])
    except (ValueError,TypeError) as error:
        raise NetworkError('Diagnostic record is unreadable; run a fresh compiler_experiments trial.') from error
    if not isinstance(trial,dict):raise NetworkError('Run a fresh compiler_experiments trial.')
    return trial


def eligible(net, proj, fn, trial):
    if not supported(net, proj):
        return 'Reviewed diagnostic search currently needs a local ROM project; imported builds need an immutable per-file diagnostic snapshot.'
    if (any(not isinstance(trial.get(key),str) for key in ('source','header','compiler','flags'))
            or len(trial['source']) > 128*1024):
        return 'Use a fresh diagnostic source/header/profile snapshot under 128 KiB.'
    if not trial.get('seed_scope') or trial['seed_scope'] != scope(net, proj, fn, trial['source']):
        return 'Diagnostic inputs or caller evidence changed. Rerun compiler_experiments and review the new trial.'
    own = compiler_for(net.cfg(proj), fn['vram'], fn)
    flags = own.get('flags', [])
    if isinstance(flags, str):flags = flags.split()
    if trial.get('compiler') != own.get('toolchain') or trial.get('flags', '').split() != flags:
        return 'Use a diagnostic with the current compiler and flags before reviewing a search seed.'
    result = trial.get('result')
    if (not isinstance(result, dict) or type(result.get('score')) not in (int, float)
            or not math.isfinite(result['score']) or result['score'] < 0):
        return 'The diagnostic must compile and receive an exact-judge score first.'
    if len(trial.get('source', '')) > 128*1024:
        return 'This diagnostic source is too large for the bounded alternative-seed lane.'
    return ''


def contract_changed(fn, source):
    before = signatures(fn['best_source'] or '', {fn['name']}).get(fn['name'])
    after = signatures(source, {fn['name']}).get(fn['name'])
    # Unknown/K&R/complex declarations cannot authorize ordinary adoption.
    return not before or not after or before != after


def review(net, agent, slug, function, trial_id, review_note='', enabled=True):
    proj, fn = net.project_row(slug), net._function(function, slug)
    if type(enabled) is not bool:
        raise NetworkError('Choose whether this reviewed seed is enabled with a boolean.')
    ensure(net)
    if not enabled:
        with net.store.tx() as db:
            db.execute('UPDATE reviewed_search_seeds SET enabled=0 WHERE trial_id=? AND function_id=?', (trial_id, fn['id']))
        return 'Diagnostic seed disabled. Saved C and compiler settings are unchanged.'
    if not isinstance(review_note, str) or not 12 <= len(review_note.strip()) <= 1000:
        raise NetworkError('Add a short review note explaining the source change and caller/interface checks (12–1,000 characters).')
    trial = record(net, fn, trial_id)
    if reason := eligible(net, proj, fn, trial):raise NetworkError(reason)
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO reviewed_search_seeds VALUES(?,?,?,?,?,?,?)',
                   (trial_id, fn['id'], trial['seed_scope'], agent.id, review_note.strip(), 1, time.time()))
    mode = ('Signature differs or is uncertain: search stays diagnostic-only, even if exact; reconcile callers and submit normally.'
            if contract_changed(fn, trial['source']) else 'Only an exact candidate with this unchanged signature may be rejudged through the ordinary save path.')
    return 'Seed reviewed for the next deep pass. Saved C and compiler settings are unchanged. '+mode


def selected(net, proj, fn):
    if not supported(net, proj) or not net.store.one("SELECT 1 FROM sqlite_master WHERE name='reviewed_search_seeds'"):
        return []
    rows = net.store.all('SELECT * FROM reviewed_search_seeds WHERE function_id=? AND enabled=1 ORDER BY created_at DESC LIMIT ?',
                         fn['id'], LIMIT)
    result = []
    for row in rows:
        try:trial = record(net, fn, row['trial_id'])
        except NetworkError:continue
        if row['scope'] != trial.get('seed_scope') or eligible(net, proj, fn, trial):continue
        result.append(dict(trial, trial_id=row['trial_id'], review_note=row['review_note'],
                           diagnostic_only=contract_changed(fn, trial['source'])))
    return result
