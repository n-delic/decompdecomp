"""Try small literal differences in already verified imported functions."""

import json
import time
from collections import defaultdict
from functools import partial
from io import BytesIO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .. import toolchains
from ..arch.mips_immediates import shape
from ..elfobj import _function_symbols
from ..forks import csource
from ..network import NetworkError
from ..project import compiler_for
from . import donor_build, donor_relocations, free_candidates, similar_matches
from .cross_game_progress import report


def index(units, donors, prints, big, job):
    by_unit = defaultdict(list)
    for donor in donors:
        fingerprint = prints.get(donor['id'])
        if (donor['unit'] in units and fingerprint and not fingerprint.asm
                and fingerprint.layout is not None and 32 <= fingerprint.size <= similar_matches.MAX_BYTES
                and fingerprint.size % 4 == 0):
            by_unit[donor['unit']].append((donor, fingerprint))
    hits = defaultdict(list)
    for name, entries in by_unit.items():
        if job.stop.is_set():
            break
        path = units[name].target
        if path is None:
            continue
        try:
            obj = ELFFile(BytesIO(path.read_bytes()))
            symbols = _function_symbols(obj)
            sections = {}
            for donor, fingerprint in entries:
                symbol = json.loads(donor['meta'] or '{}').get('symbol') or donor['name'].split('@')[0]
                if symbol not in symbols:
                    continue
                section, start, size = symbols[symbol]
                if section not in sections:
                    sections[section] = obj.get_section(section).data()
                code = sections[section][start:start + size]
                if len(code) == fingerprint.size:
                    hits[shape(code, big)].append((donor, fingerprint, {
                        'target': code, 'relocs': json.dumps([(*site, 0) for site in fingerprint.layout])}))
        except (ELFError, OSError, ValueError, IndexError):
            continue
    return hits


def _adapt(body, name, donor, target, big):
    span = csource.find(body, name)
    if span is None:
        return None
    start, end = span
    variants = similar_matches.literal_variants(body[start:end], donor, target, big)
    return body[:start] + variants[0] + body[end:] if variants else None


def run(twins, agent, proj, other, donors, prints, units, settings, job):
    """Build and judge changed literals with the destination's symbol and byte checks."""
    from .twins import WHY_CHARS, renames

    net = twins.net
    big = bool(proj['big_endian'])
    report(job, 'Looking for literal changes in imported source', game=other['name'])
    hits = index(units, donors, prints, big, job)
    if not hits or job.stop.is_set():
        return 0
    store = net.project_store(proj['id'])
    labels = net.symbols(proj['id']).label
    matched = 0
    rows = store.all("SELECT id,target FROM functions WHERE project_id=? AND status='open' "
                     "AND size BETWEEN 32 AND ? AND size % 4=0 ORDER BY id",
                     proj['id'], similar_matches.MAX_BYTES)
    for row in rows:
        if job.stop.is_set() or (hasattr(job, 'reuse_limit') and free_candidates.exhausted(job)):
            break
        if not row['target'] or not (options := hits.get(shape(row['target'], big))):
            continue
        row_full = store.one("SELECT * FROM functions WHERE id=? AND status='open'", row['id'])
        if row_full is None or store.one('SELECT 1 FROM claims WHERE function_id=?', row['id']):
            continue
        target_obj = net._target_obj(row_full, proj)
        target = {**dict(row_full), 'relocs': json.dumps([
            *similar_matches.relocations(row_full), *((r.offset, r.type, 0) for r in target_obj.relocs)])}
        ranked = []
        for donor, fingerprint, code in options:
            changes, _ = similar_matches.literal_changes(code, target, big)
            if changes:
                ranked.append((len(changes), donor['id'], donor, fingerprint, code))
        for _, _, donor, fingerprint, code in sorted(ranked)[:similar_matches.MANUAL_CANDIDATES]:
            if job.stop.is_set() or (hasattr(job, 'reuse_limit') and free_candidates.exhausted(job)):
                break
            unit = units[donor['unit']]
            profile = settings.for_unit(unit)
            if profile is None or toolchains.installed(profile['toolchain']) is None:
                own = compiler_for(net.cfg(proj), row_full['vram'], row_full)
                profile = own if own.get('toolchain') and not own.get('cmd') else None
            name = fingerprint.name or donor['name'].split('@')[0]
            replacement = settings.replacement_source(unit, name)
            if not (profile or replacement) or net.forks.is_asm(other, donor):
                continue
            refs = donor_relocations.donor_refs(fingerprint, row_full, labels)
            names = donor_relocations.overlay_names(net, proj, row_full, fingerprint, name)
            if names is None:
                names = renames(fingerprint, refs, name, row_full['name']) or {}
            report(job, 'Verifying imported literal change', game=other['name'], function=row_full['name'])
            job.cross_candidates += 1
            job.cross_tried += 1
            if hasattr(job, 'reuse_limit'):
                job.reuse_attempts += 1
            try:
                outcome, why = donor_build.verify(
                    net, proj, row_full, other, donor, names, profile, agent, job, settings.headers,
                    replacement=replacement,
                    adapt=partial(_adapt, name=row_full['name'], donor=code, target=target, big=big))
            except (NetworkError, OSError, ValueError) as error:
                outcome, why = 'skip', str(error)
            with store.tx() as db:
                db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,"
                           "source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                           (proj['id'], row_full['id'], other['id'], donor['id'], outcome,
                            ('Imported literal change: ' + why)[:WHY_CHARS], time.time()))
            if outcome == 'match':
                matched += 1
                job.cross_matched += 1
                if hasattr(job, 'reuse_limit'):
                    job.reuse_matched += 1
                    job.reuse_bytes += row_full['size']
                break
    return matched
