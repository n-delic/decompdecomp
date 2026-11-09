"""Reuse better unfinished C on exact instruction twins; the receiving judge owns every verdict.

Partial donors are separate from the verified library: they never supply names or SDK/version evidence.
Imported candidates are saved as best attempts, not installed over the project's source files.
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path

from .. import consoles
from ..forks.project import ForkError
from ..network import NetworkError
from .twins import ACROSS_MIN_BYTES, _declared, apply, renames, rom_renames

log = logging.getLogger('decompdecomp')
MAX_TRIALS = 64


def machine(net, proj):
    if net.fork_slug(proj['slug']):
        _, _, cfg = net.forks.get(proj['slug'])
        console = consoles.of(cfg)
        return console.arch, bool(console.big_endian)
    return proj['arch'], bool(proj['big_endian'])


def run(twins, proj, wanted, mine, job=None, *, projects=None, budget_state=None):
    """Automatic passes try at most 64 donors; manual passes check every eligible donor until stopped."""
    from .shared_reuse import compatible
    net = twins.net
    if proj['slug'] in net.links:
        return 0
    agent, _ = net.local_agent('autopilot')
    target_machine = machine(net, proj)
    matched = 0
    budget_state = {'tried':0} if budget_state is None else budget_state
    projects = compatible(net, proj) if projects is None else projects
    for other in projects:
        from .free_candidates import exhausted
        if budget_state['tried'] >= getattr(job,'partial_limit',MAX_TRIALS) or (job and (job.stop.is_set() or
                (hasattr(job,'reuse_limit') and exhausted(job)))):
            return matched
        if other['slug'] in net.links:
            continue
        try:
            if machine(net, other) != target_machine:
                continue
            donors = net.project_store(other['id']).all("SELECT * FROM functions WHERE project_id=? AND status='open' "
                                   'AND best_source IS NOT NULL AND best_score IS NOT NULL ORDER BY best_score,id', other['id'])
            if not donors:
                continue
            prints = (twins._fork_prints(other,function_ids=[row['id'] for row in donors],job=job)
                      if net.fork_slug(other['slug']) else twins.prints(other))
            for a in donors:
                pa = prints.get(a['id'])
                if not pa or pa.asm or pa.size < ACROSS_MIN_BYTES:
                    continue
                # A live donor may have changed since selection; target code, never candidate output, is fingerprinted.
                for bid in wanted.get(pa.key, []):
                    from .free_candidates import exhausted
                    if budget_state['tried'] >= getattr(job, 'partial_limit', MAX_TRIALS) or (job and (job.stop.is_set() or
                            (hasattr(job, 'reuse_limit') and exhausted(job)))):
                        return matched
                    b = net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=? AND status='open'", bid)
                    if not b or net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?', bid):
                        continue
                    if b['best_score'] is not None and a['best_score'] / max(1, a['n_insns']) >= b['best_score'] / max(1, b['n_insns']):
                        continue
                    source, _ = adapt(twins, proj, other, a, b, pa, mine[bid])
                    if not source:
                        continue
                    signature = scope(net, proj, b, source, a)
                    if net.project_store(proj['id']).one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?', bid, signature):
                        continue
                    if job:
                        job.cross_candidates += 1
                        job.cross_tried += 1
                        if hasattr(job, 'reuse_limit'):
                            job.reuse_attempts += 1
                    budget_state['tried'] += 1
                    note = f"/* Partial candidate from function #{a['id']} in project #{other['id']}; rechecked locally. */\n"
                    # Never label this donor matched or use it to infer a name. Ordinary attempt recording
                    # atomically retains only improvements; the target's own exact judge alone can mark a match.
                    from .cross_game_progress import report
                    report(job, 'Checking better unfinished C', game=other['name'], function=b['name'], donor=a['name'])
                    if net.fork_slug(proj['slug']):
                        result = net.forks.attempt(agent, proj['slug'], b['name'], note + source, keep=False)
                    else:
                        result = net.try_quietly(agent, proj, b, note + source)
                    if result.get('score') is None:
                        outcome = 'miss'
                    elif result.get('matched'):
                        outcome = 'match'
                    else:
                        current = net.project_store(proj['id']).one('SELECT best_score,best_agent_id FROM functions WHERE id=?', bid)
                        outcome = 'improved' if current['best_agent_id'] == agent.id and current['best_score'] == result['score'] and (
                            b['best_score'] is None or current['best_score'] < b['best_score']) else 'miss'
                    reason = f"Partial donor; locally checked score {result.get('score')}; previous score {b['best_score']}"
                    with net.project_store(proj['id']).tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                                   (bid, signature, outcome, reason))
                        db.execute('INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,'
                                   'source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                                   (proj['id'], bid, other['id'], a['id'], outcome, reason, time.time()))
                    if outcome == 'match':
                        matched += 1
                        if job:
                            job.cross_matched += 1
                            if hasattr(job, 'reuse_limit'):
                                job.reuse_matched += 1
                                job.reuse_bytes += b['size']
        except (NetworkError, ForkError, OSError, ValueError):
            log.exception('partial reuse: unreadable project or candidate in %s', other['slug'])
    return matched


def adapt(twins, proj, other, a, b, pa, pb):
    net = twins.net
    # Remove only our own leading provenance notes, so a returned candidate cannot accumulate notes forever.
    source = a['best_source']
    import re
    source = re.sub(r'\A(?:/\* Partial candidate from function #\d+ in project #\d+; rechecked locally\. \*/\n)+', '', source)
    if net.fork_slug(other['slug']) or net.fork_slug(proj['slug']):
        names = renames(pa, pb, pa.name or a['name'].split('@')[0], pb.name or b['name'].split('@')[0])
    else:
        names = rom_renames(net.symbols(other['id']), source, pa, pb, a['name'], b['name'], net.symbols(proj['id']))
    if names is None:
        return None, None
    if not net.fork_slug(other['slug']) and not net.fork_slug(proj['slug']):
        source = _declared(net._context(other['id'], a['unit'])['header'],
                           net._context(proj['id'], b['unit'])['header'], names, a['name']) + source
    return apply(source, names), names


def scope(net, proj, fn, source, donor):
    """Persist retries by source, target, settings and context rather than a time-based miss cache."""
    from .similar_matches import trial_key
    context = net._context(proj['id'], fn['unit'])['header']
    # Imported build dependencies are external: use their content stamp, not just the in-app header.
    if net.fork_slug(proj['slug']):
        _, fp, _ = net.forks.get(proj['slug'])
        context += input_stamp(fp.root)
    return 'partial-v1:' + hashlib.sha256(json.dumps([donor['best_score'],
        trial_key(source, context, net.cfg(proj), fn, [tuple(r) for r in net.project_store(proj['id']).all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name', proj['id'])])], sort_keys=True).encode()).hexdigest()


def input_stamp(root):
    """Bounded content snapshot of imported source/build inputs; oversized trees fail closed."""
    digest = hashlib.sha256()
    count = size = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ('.git', '.decomp-wtf', '.venv', 'node_modules'))
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix.lower() not in ('.c', '.cc', '.cpp', '.h', '.hpp', '.s', '.py', '.json', '.yaml', '.yml', '.mk', '.ninja', '.ld') and name != 'Makefile':
                continue
            count += 1
            size += path.stat().st_size
            if count > 10000 or size > 32 * 1024 * 1024:
                raise ValueError('Imported input snapshot is too large for partial reuse')
            digest.update(str(path.relative_to(root)).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
