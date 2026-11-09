"""A bounded, token-free search using verified C from similar functions in other ROM projects.

Mnemonic similarity is only a shortlist. Names come from aligned relocations or verified regional pairs; every
candidate goes through the ordinary compiler/link judge. Small immediate differences can suggest literal
edits; they never change the target or count as evidence of a match on their own.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time

from .. import permuter, similarity, toolchains
from ..network import NetworkError
from ..project import compiler_for

MIN_BYTES, MAX_BYTES = 32, 3200
MIN_SIMILARITY = .9
MAX_CANDIDATES = 3
MAX_POOL = 400
MAX_ATTEMPTS = 128
MAX_SECONDS = 120
MAX_REPAIRS = 8
REPAIR_PERCENT = 94
MUTATION_DRAWS = 48
# Consume comments, strings and identifiers whole, so edits never touch a symbol's address or documentation.
TOKENS = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                    r'[A-Za-z_]\w*|(?<![\w.])(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:[uUlL]*)(?![\w.])', re.S)
INTEGER = re.compile(r'(0[xX][0-9a-fA-F]+|[0-9]+)([uUlL]*)\Z')
# Arithmetic/logical immediates and explicit load/store offsets; never branches.
IMMEDIATE_OPS = {8, 9, 10, 11, 12, 13, 14, 32, 33, 35, 36, 37, 40, 41, 43}
UNSIGNED_OPS = {12, 13, 14}


def relocations(fn) -> list[tuple[int, int, int]]:
    return sorted(tuple(int(v) for v in r[:3]) for r in json.loads(fn['relocs'] or '[]') if len(r) >= 3)


def _table_macro_edits(source, changes):
    """Recognise one-use IDs in explicitly declared 32-bit tables, never general scaled arithmetic."""
    from ..forks.csource import _blank

    if not changes:
        return {}
    clean = _blank(source)
    tables = set(re.findall(
        r"\bextern\s+(?:char\s*\*\s*\*\s*|(?:int|unsigned\s+int|u32|s32|uint32_t|int32_t)\s+\*\s*)(\w+)\s*;",
        clean))
    tables.update(re.findall(
        r"\bextern\s+(?:int|unsigned\s+int|u32|s32|uint32_t|int32_t)\s+(\w+)\s*\[\s*\]\s*;",
        clean))
    definitions = list(re.finditer(
        r"^[ \t]*#define[ \t]+([A-Za-z_]\w*)[ \t]+(0[xX][0-9a-fA-F]+|[0-9]+)([uUlL]*)[ \t]*$",
        clean, re.M))
    result = {}
    for old, new in changes.items():
        if old % 4 or new % 4:
            continue
        candidates = [m for m in definitions if int(m[2], 0 if m[2].lower().startswith('0x') else 10) == old // 4]
        if len(candidates) != 1:
            continue
        definition = candidates[0]
        name = definition[1]
        # Redefinitions and uses in control flow or other expressions make the scale ambiguous.
        uses = [m for m in re.finditer(r'\b' + re.escape(name) + r'\b', clean)
                if not definition.start() <= m.start() < definition.end()]
        if len(uses) != 1:
            continue
        indexed = any(re.search(r'\b' + re.escape(table) + r'\s*\[\s*' + re.escape(name) + r'\s*\]', clean)
                      for table in tables)
        if indexed:
            result[definition.start(2)] = (definition.end(2), str(new // 4))
    return result


def literal_variants(source: str, a, b, big: bool) -> list[str]:
    """Try at most two unambiguous literal substitutions supported by aligned immediate instructions.

    Only singleton, nonnegative C literals or one-use IDs in explicit 32-bit tables are changed.
    Repeated numbers, signed expressions, split address constants and changed instruction layouts need
    more context than this small search can establish.
    """
    left, right = a['target'] or b'', b['target'] or b''
    if len(left) != len(right) or len(left) % 4:
        return []
    relocated = {off for off, _, _ in relocations(a)} | {off for off, _, _ in relocations(b)}
    changes, offset_ops = {}, {}
    order = 'big' if big else 'little'
    for off in range(0, len(left), 4):
        x, y = int.from_bytes(left[off:off + 4], order), int.from_bytes(right[off:off + 4], order)
        if x == y or off in relocated:
            continue
        op = x >> 26
        if x >> 16 != y >> 16 or op not in IMMEDIATE_OPS:
            return []
        old, new = x & 0xffff, y & 0xffff
        if op not in UNSIGNED_OPS and (old >= 0x8000 or new >= 0x8000):
            return []
        if old in changes and changes[old] != new:
            return []
        changes[old] = new
        offset_ops.setdefault(old, set()).add(op)
    if not changes or len(changes) > 2:
        return []
    occurrences = {}
    for token in TOKENS.finditer(source):
        if m := INTEGER.fullmatch(token.group()):
            value = int(m[1], 16 if m[1].lower().startswith('0x') else 10)
            occurrences.setdefault(value, []).append((token, m[2]))
    replacements = {}
    for old, new in changes.items():
        hits = occurrences.get(old, [])
        if len(hits) != 1:
            continue
        token, suffix = hits[0]
        if source[:token.start()].rstrip().endswith('-'):
            continue
        replacements[token.start()] = (token.end(), str(new) + suffix)
    scaled = {old: new for old, new in changes.items()
              if offset_ops[old] <= {35, 43} and not occurrences.get(old)}
    replacements.update(_table_macro_edits(source, scaled))
    if not replacements:
        return []
    result = source
    for start, (end, value) in sorted(replacements.items(), reverse=True):
        result = result[:start] + value + result[end:]
    return [result]


def _mnemonics(net, proj, fn) -> list[str]:
    # App bundles built before processor variants use the three-argument reader. Keep testing those installs
    # possible without replacing their other local fixes with an entire newer runtime.
    cpu = getattr(net.console(proj), 'cpu_variant', None)
    args = (fn['target'], bool(proj['big_endian']), fn['vram'] or 0)
    return similarity.mips_mnemonics(*args) if cpu is None else similarity.mips_mnemonics(*args, cpu)


def repair_variants(source: str, feedback: dict | None = None) -> list[str]:
    """A reproducible small sample of the existing local permuter; never a new AI request."""
    rng = random.Random(hashlib.sha256(source.encode()).digest())
    seen, result = {source}, []
    def protected(text):
        return [m.group() for m in TOKENS.finditer(text) if m.group().startswith(('/*', '//', '"', "'"))]
    original = protected(source)
    markers = {row[0] for row in (feedback or {}).get('rows', []) if row[0] != ' '}
    operations = []
    if markers & {'r', 's'}:
        operations += [permuter._shuffle_declarations, permuter._toggle_register, permuter._retype_local]
    if markers & {'i', '|'}:
        operations += [permuter._add_cast, permuter._rewrite, permuter._retype_local]
    if markers & {'o', '<', '>'}:
        operations += [permuter._swap_statements, permuter._shuffle_declarations, permuter._rewrite]
    for _ in range(MUTATION_DRAWS):
        if operations:
            lines = rng.choice(operations)(source.split('\n'), rng)
            variant = '\n'.join(lines) if lines else None
        else:
            variant = permuter.mutate(source, rng)
        if variant and variant not in seen and protected(variant) == original:
            seen.add(variant)
            result.append(variant)
            if len(result) == MAX_REPAIRS:
                break
    return result


def deeper_variants(source, header, settings, function, deadline):
    """Use the already-installed full permuter for a small batch, inside this pass's time budget."""
    from .. import seeds
    from .permute import GENERATOR_SECONDS, Generator, available, weight_family

    if not available() or deadline - time.monotonic() < GENERATOR_SECONDS:
        return []
    generator = None
    try:
        generator = Generator()
        return generator.batch({'header': seeds.c_only(header), 'source': source, 'function': function,
                                'family': weight_family({'compiler': settings}, None, False),
                                'seed': int(hashlib.sha256(source.encode()).hexdigest()[:7], 16),
                                'count': MAX_REPAIRS // 2}) or []
    except OSError:
        return []  # the small built-in rewrites remain available when the optional tool cannot start
    finally:
        if generator:
            generator.close()


def regional_names(twins, proj, other) -> dict[str, str]:
    """Learn names only from accepted transfers still matched on both sides; discard conflicting evidence.

    A function pair proves its own names. Aligned relocation layouts additionally suggest data/callee names.
    These remain proposals: even a learned name must survive the destination's ordinary byte judge.
    """
    from . import repair_mappings
    from .twins import rom_renames

    net = twins.net
    left, right = twins.prints(other), twins.prints(proj)
    syms_a, syms_b = net.symbols(other['id']), net.symbols(proj['id'])
    votes = {}
    rows = net.project_store(proj['id']).all("SELECT DISTINCT source_function_id,function_id FROM cross_game_attempts "
                         "WHERE project_id=? AND source_project_id=? AND outcome='match'", proj['id'], other['id'])
    old_pairs = {(r['source_function_id'], r['function_id']) for r in rows}
    rows = [*rows, *repair_mappings.pairs(net, proj, other)]
    support = {}
    functions = set()
    for row in rows:
        a = net.project_store(other['id']).one("SELECT * FROM functions WHERE id=? AND status='matched'", row['source_function_id'])
        b = net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=? AND status='matched'", row['function_id'])
        if not a or not b or a['project_id'] != other['id'] or b['project_id'] != proj['id']:
            continue
        functions.add((a['name'], b['name']))
        names = {a['name']: b['name']}
        if a['id'] in left and b['id'] in right and (
                [(o, t) for o, t, _ in relocations(a)] == [(o, t) for o, t, _ in relocations(b)]):
            source_symbols = syms_a if (a['id'], b['id']) in old_pairs else repair_mappings.source_symbols(syms_a, a['matched_source'] or '')
            names.update(rom_renames(source_symbols, a['matched_source'] or '', left[a['id']], right[b['id']],
                                     a['name'], b['name'], syms_b) or {})
        for old, new in names.items():
            votes.setdefault(old, set()).add(new)
            support.setdefault((old,new), set()).add((a['id'],b['id']))
    def supported(old, new):
        evidence = support[old,new]
        return ((old,new) in functions or bool(evidence & old_pairs) or
                (len({a for a,b in evidence}) >= 2 and len({b for a,b in evidence}) >= 2))
    names = {old: next(iter(values)) for old, values in votes.items() if len(values) == 1
             and supported(old, next(iter(values)))}
    # Many-to-one evidence is ambiguous too (identical wrappers are common).
    return {old: new for old, new in names.items() if list(names.values()).count(new) == 1}


def trial_key(source, header, compiler, fn, names) -> str:
    """Header/type, symbol, target and compiler changes invalidate failed work immediately."""
    payload = [source, header, compiler, (fn['target'] or b'').hex(), fn['relocs'], fn['vram'], names]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def compiler_trial(net, agent, proj, fn, candidate, settings):
    """Test donor flags without changing build settings or misreporting them as accepted project progress."""
    now = time.time()
    from .local_claims import expiry_ids
    store = net.project_store(proj['id'])
    identities = expiry_ids(net, store)
    with store.tx() as db:
        net._expire_claims(db, **identities)
        current = db.execute('SELECT status FROM functions WHERE id=?', (fn['id'],)).fetchone()
        if not current or current['status'] != 'open' or db.execute(
                'SELECT 1 FROM claims WHERE function_id=?', (fn['id'],)).fetchone():
            return 'skip', 'function is busy or no longer open'
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fn['id'], agent.id, now, now + 300))
    try:
        ok, diff, problem = net._judge_with(proj, fn, settings['toolchain'], settings.get('flags', []), candidate)
        label = settings['toolchain'] + ' ' + ' '.join(settings.get('flags', []))
        if ok:
            reason = 'byte match with ' + label + '; review compiler settings before accepting'
            net.add_note(agent, proj['slug'], reason + '\nCandidate C:\n' + candidate, fn['name'])
            return 'compiler', reason
        return 'miss', label + ': ' + (f'score {diff.score}' if diff else (problem or 'did not build')[-160:])
    except (NetworkError, toolchains.ToolchainError) as error:
        return 'skip', str(error)[:160]
    finally:
        with net.project_store(proj['id']).tx() as db:
            db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (fn['id'], agent.id))


def run(twins, slug: str, job) -> int:
    """Try a few close candidates per function after exact transfers, with a pass-wide time/compile budget."""
    from .twins import _declared, apply, rom_renames

    net = twins.net
    proj = net.project_row(slug)
    # Project mode needs alignment of object relocations and file-context adaptation, not ROM addresses.
    # Other processors need their own instruction reader before mnemonic ranking is meaningful.
    if net.fork_slug(slug) or not net.is_rom(proj) or proj['arch'] != 'mips' or slug in net.links:
        return 0
    cfg = net.cfg(proj)
    from .shared_reuse import compatible
    others = {p['id']:p for p in compatible(net, proj) if net.is_rom(p)}
    others[proj['id']] = proj  # Matched siblings are donors too, without another imported region.
    from . import free_candidates
    free_candidates.budget(job, MAX_SECONDS, MAX_ATTEMPTS)
    deadline = job.reuse_deadline
    for other in others.values():
        if job.stop.is_set() or time.monotonic() >= deadline:
            return 0
        twins.index_project(other)
    agent, _ = net.local_agent('autopilot')
    mine = twins.prints(proj)
    syms_b = net.symbols(proj['id'])
    symbol_state = [(r['name'], r['address']) for r in net.project_store(proj['id']).all(
        'SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    source_cache, ops_cache = {}, {}
    from . import address_repairs, parameter_transfers, regional_layouts, reuse_donors
    donors = reuse_donors.Donors(net, proj, include_self=True)
    layouts = {pid: regional_layouts.learn(net, proj, other) for pid, other in others.items()}
    maps = {pid: regional_names(twins, proj, other) for pid, other in others.items()}
    saved = net.project_store(proj['id']).one('SELECT function_id FROM reuse_cursors WHERE project_id=?', proj['id'])
    cursor = saved['function_id'] if saved else 0
    def resume(fid):
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_cursors VALUES(?,?)', (proj['id'], fid))
    attempted = matched = 0
    repairs = {}

    def exhausted():
        return job.stop.is_set() or job.reuse_attempts >= job.reuse_limit or time.monotonic() >= deadline

    feedback = {}

    def attempt(a, b, other, score, candidate, settings, kind, names, header):
        nonlocal attempted, matched
        feedback.clear()
        current = net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?', b['id'])
        if not current or current['status'] != 'open':
            return 'skip', ''
        from .repair_preflight import invalid_call
        if invalid_call(candidate, b['name'], header):
            return 'skip', 'Generated call disagrees with a fixed prototype'
        signature = trial_key(candidate, header, settings, b, [names, symbol_state])
        previous = net.project_store(proj['id']).one("SELECT * FROM reuse_trials WHERE function_id=? AND signature=? AND outcome!='match'",
                                 b['id'], signature)
        if previous:
            feedback.update(json.loads(previous['feedback']))
            return previous['outcome'], previous['reason']
        if exhausted():
            return 'skip', ''
        job.cross_candidates += 1
        job.cross_tried += 1
        attempted += 1
        job.reuse_attempts += 1
        if kind == 'compiler':
            outcome, why = compiler_trial(net, agent, proj, b, candidate, settings)
        else:
            outcome, why = twins._attempt_twin(agent, proj, a, b, candidate,
                origin=f" in {other['name']}", similar=True, feedback=feedback, mapping=names)
        donor_hash = hashlib.sha256(a['matched_source'].encode()).hexdigest()
        reason = f'similar code ({score:.0%}); {kind}; donor source {donor_hash}; ' + (why or outcome)
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,'
                       'source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                       (proj['id'], b['id'], other['id'], a['id'], outcome, reason, time.time()))
            if outcome != 'skip':
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                           (b['id'], signature, outcome, why or '', json.dumps(feedback)))
        if outcome == 'match':
            matched += 1
            job.reuse_matched += 1
            job.reuse_bytes += b['size']
            job.cross_matched += 1
            maps[other['id']] = regional_names(twins, proj, other)
        return outcome, why

    # Keep a quarter of the remaining budget for close misses. Previously a large transfer queue could
    # consume every pass, leaving hundreds of eligible cached misses with no repair attempts at all.
    transfer_deadline = time.monotonic() + max(0, deadline - time.monotonic()) * .75
    transfer_limit = job.reuse_attempts + max(1, int((job.reuse_limit - job.reuse_attempts) * .75))
    incomplete = False

    def transfers_exhausted():
        return bool(repairs) and (job.reuse_attempts >= transfer_limit or time.monotonic() >= transfer_deadline)

    targets = net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY id", proj['id'])
    # Preserve the cheap aligned-transfer search before widening to learned mappings or different flags.
    # A signed cursor encodes which phase to resume without storing any game source in the queue.
    work = [(phase, b) for phase in (0, 1) for b in targets]
    position = (int(cursor < 0), abs(cursor))
    work.sort(key=lambda item: ((item[0], item[1]['id']) < position, item[0], item[1]['id']))
    from .cross_game_progress import report as publish_progress
    for index, (phase, b) in enumerate(work):
        publish_progress(job, 'Checking similar matched functions', function=b['name'], done=index, total=len(work))
        if job.stop.is_set() or job.reuse_attempts >= job.reuse_limit or time.monotonic() >= deadline:
            return matched
        if transfers_exhausted():
            incomplete = True
            break
        resume(-b['id'] if phase else b['id'])
        pb = mine.get(b['id'])
        if pb is None or pb.asm or not MIN_BYTES <= pb.size <= MAX_BYTES:
            continue
        if net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?', b['id']):
            continue
        rows = donors.candidates(pb.size, max(MIN_BYTES, int(pb.size * .9)), min(MAX_BYTES, int(pb.size / .9)),
                                 pb.key, twins._compiler_key(compiler_for(cfg, b['vram'], b)), MAX_POOL, near=b['vram'])
        layout = [(o, t) for o, t, _ in relocations(b)]
        rows = [a for a in rows if a['project_id'] in others and a['matched_source']
                and ([(o, t) for o, t, _ in relocations(a)] == layout or maps[a['project_id']])
                and json.loads(a['compiler']).get('toolchain') == compiler_for(cfg, b['vram'], b).get('toolchain')]
        own_key = twins._compiler_key(compiler_for(cfg, b['vram'], b))
        rows = [a for a in rows if ([(o, t) for o, t, _ in relocations(a)] != layout or a['compiler'] != own_key) == bool(phase)]
        if not rows:
            continue
        ops = _mnemonics(net, proj, b)
        for a in rows:
            if a['id'] not in ops_cache:
                ops_cache[a['id']] = _mnemonics(net, others[a['project_id']], a)
        by_id = {str(a['id']): a for a in rows}
        ranked = similarity.rank(ops, {key: ops_cache[a['id']] for key, a in by_id.items()},
                                 top=MAX_POOL, floor=MIN_SIMILARITY)
        ranked.sort(key=lambda item: (not parameter_transfers.scale_changes(by_id[item[0]], b, bool(proj['big_endian'])),
                                      [(o, t) for o, t, _ in relocations(by_id[item[0]])] != layout, -item[1]))
        ranked = ranked[:MAX_CANDIDATES]
        for key, score in ranked:
            a = by_id[key]
            # Re-read authoritative source after ranking: donors can be polished during a pass.
            fresh = net.project_store(a['project_id']).one(
                "SELECT * FROM functions WHERE id=? AND status='matched'", a['id'])
            if fresh is None or not fresh['matched_source'] or fresh['target'] != a['target']:
                continue
            a = {**a, **dict(fresh)}
            other = others[a['project_id']]
            if other['id'] not in source_cache:
                source_cache[other['id']] = (twins.prints(other), net.symbols(other['id']),
                                              net._context(other['id'])['header'])
            prints, syms_a, header_a = source_cache[other['id']]
            pa = prints.get(a['id'])
            if pa is None:
                continue
            learned = maps[other['id']]
            aligned = [(o, t) for o, t, _ in relocations(a)] == layout
            local = rom_renames(syms_a, a['matched_source'], pa, pb, a['name'], b['name'], syms_b) if aligned else {}
            names = None if local is None else {**learned, **local, a['name']: b['name']}
            if names is None:
                continue
            words = set(re.findall(r'\b[A-Za-z_]\w*\b', a['matched_source']))
            names = {old: new for old, new in names.items() if old in words}
            header_b = net._context(proj['id'], b['unit'])['header']
            prelude = _declared(header_a, header_b, names, a['name'])
            source = apply(a['matched_source'], names)
            parameter_sources = parameter_transfers.variants(source, a, b, bool(proj['big_endian']))
            destination = b['best_source'] or b['seed'] or ''
            parameter_sources = list(dict.fromkeys([parameter_transfers.destination_signature(v, b['name'], destination)
                                                    for v in parameter_sources] + parameter_sources))
            own = compiler_for(cfg, b['vram'], b)
            donor = json.loads(a['compiler'])
            variants = [(v, own, 'parameter transfer') for v in parameter_sources]
            variants += [(v, own, 'transfer') for v in
                        [source, *literal_variants(source, a, b, bool(proj['big_endian'])),
                         *regional_layouts.variants(source, a, b, layouts[other['id']], bool(proj['big_endian']))]]
            # Only a compiler already installed and proven on the donor; no downloads or flag brute force.
            if (donor.get('flags', []) != own.get('flags', []) and donor.get('toolchain')
                    and not donor.get('cmd') and not own.get('cmd') and toolchains.installed(donor['toolchain'])):
                variants.append((source, donor, 'compiler'))
            for variant, settings, kind in variants:
                if exhausted():
                    return matched
                if transfers_exhausted():
                    incomplete = True
                    break
                candidate = prelude + variant
                outcome, why = attempt(a, b, other, score, candidate, settings, kind, names, header_b)
                # Easy transfers get the first pass through the budget. Then repair the closest compiled misses.
                close = re.fullmatch(r'score (\d+)', why or '')
                percent = feedback.get('percent', 100 * (1 - int(close[1]) / max(1, (b['n_insns'] or 0) * 100)) if close else 0)
                if kind in ('transfer', 'parameter transfer') and outcome == 'miss' and close and percent >= REPAIR_PERCENT:
                    pair = (a['id'], b['id'])
                    if pair not in repairs or int(close[1]) < repairs[pair][0]:
                        repairs[pair] = (int(close[1]), a, b, other, score, prelude, variant, settings, names, header_b, dict(feedback))
                if outcome == 'match':
                    break
            if incomplete:
                break
            if net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?', b['id'])['status'] == 'matched':
                break
        if incomplete:
            break
    # Cached trials regenerate this deterministic queue, so a later pass resumes even after an app restart.
    # Concrete address evidence goes before speculative cast/order mutations within the repair allowance.
    known = {name for name, _ in symbol_state}
    prepared = [(address_repairs.variants(r[5] + r[6], r[10], r[2], bool(proj['big_endian']), known), r)
                for r in repairs.values()]
    prepared.sort(key=lambda item: (not item[0], item[1][0]))
    for address_variants, (_, a, b, other, score, prelude, source, settings, names, header, report) in prepared:
        if exhausted():
            if not incomplete:
                resume(0)
            return matched
        if net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?', b['id'])['status'] != 'open':
            continue
        guided = list(address_variants)
        if report.get('candidate_target'):
            compiled = {'target': bytes.fromhex(report['candidate_target']), 'relocs': report.get('candidate_relocs', '[]')}
            guided.extend(literal_variants(prelude + source, compiled, b, bool(proj['big_endian'])))
        simple = repair_variants(source, report)
        deep = deeper_variants(source, header + '\n' + prelude, settings, b['name'], deadline)
        variants = [*guided, *(prelude + v for v in (simple[:MAX_REPAIRS // 2] if deep else simple)),
                    *(prelude + v for v in deep)]
        for variant in list(dict.fromkeys(variants))[:MAX_REPAIRS]:
            if exhausted():
                if not incomplete:
                    resume(0)
                return matched
            outcome, _ = attempt(a, b, other, score, variant, settings, 'repair', names, header)
            if outcome == 'match':
                break
    if not incomplete:
        resume(0)
    return matched
