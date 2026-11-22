"""Find and verify close imported functions whose instruction layouts have changed."""

import time
from collections import Counter, defaultdict
from dataclasses import replace
from difflib import SequenceMatcher
from itertools import chain

from .. import similarity, toolchains
from ..network import NetworkError
from ..project import compiler_for
from . import donor_build, donor_relocations, free_candidates
from .cross_game_progress import report
from .twins import Print, renames

MIN_SCORE = .84
MAX_SHORTLIST = 24
MAX_TRIALS = 3


def _grams(ops):
    return set(zip(ops, ops[1:], ops[2:]))


class Index:
    def __init__(self, entries, big, cpu, stop):
        self.entries = {}
        self.shingles = defaultdict(set)
        for donor, fingerprint, code in entries:
            if stop.is_set():
                break
            ops = similarity.mips_mnemonics(code['target'], big, cpu=cpu)
            fid = donor['id']
            grams = _grams(ops)
            self.entries[fid] = donor, fingerprint, code['target'], ops, len(grams)
            for gram in grams:
                self.shingles[gram].add(fid)

    def shortlist(self, code, ops, excluded=()):
        grams = _grams(ops)
        counts = Counter(chain.from_iterable(self.shingles.get(gram, ()) for gram in grams))
        rough = []
        for fid, shared in counts.items():
            _, _, donor_code, donor_ops, total = self.entries[fid]
            if (fid in excluded or donor_code == code
                    or not .75 <= len(donor_ops) / len(ops) <= 1 / .75):
                continue
            rough.append((2 * shared / (len(grams) + total), fid))
        candidates = [fid for _, fid in sorted(rough, reverse=True)[:MAX_SHORTLIST]]
        ranked = [(similarity.score(ops, self.entries[fid][3]), fid) for fid in candidates]
        return [self.entries[fid] for score, fid in sorted(ranked, reverse=True)[:MAX_TRIALS]
                if score >= MIN_SCORE]


def aliases(fingerprint, donor_ops, target, target_ops, donor_name, target_name):
    """Align references only inside contiguous runs of the same kind of instructions."""
    target_sites = {(r.offset, r.type): r.symbol for r in target.relocs}
    left, right = [], []
    for run in SequenceMatcher(None, donor_ops, target_ops, autojunk=False).get_matching_blocks():
        if run.size < 3:
            continue
        for (offset, kind), symbol in zip(fingerprint.layout, fingerprint.refs, strict=True):
            word = offset // 4
            site = offset + 4 * (run.b - run.a), kind
            if run.a <= word < run.a + run.size and site in target_sites:
                left.append(symbol)
                right.append(target_sites[site])
    return renames(replace(fingerprint, refs=left), Print('', right, 0), donor_name, target_name) or {}


def run(twins, agent, proj, other, hits, units, settings, job, checked):
    net = twins.net
    console = net.console(proj)
    big = bool(proj['big_endian'])
    cpu = console.cpu_variant
    index = Index(chain.from_iterable(hits.values()), big, cpu, job.stop)
    if not index.entries:
        return 0
    store = net.project_store(proj['id'])
    from .similar_matches import MAX_BYTES, MIN_BYTES

    rows = store.all("SELECT id,target FROM functions WHERE project_id=? AND status='open' "
                     "AND size BETWEEN ? AND ? ORDER BY id", proj['id'], MIN_BYTES, MAX_BYTES)
    def exhausted():
        return job.stop.is_set() or (hasattr(job, 'reuse_limit') and free_candidates.exhausted(job))

    matched = 0
    for index_in_project, row in enumerate(rows):
        if exhausted():
            break
        code = row['target']
        if not code:
            continue
        ops = similarity.mips_mnemonics(code, big, cpu=cpu)
        for donor, fingerprint, _, donor_ops, _ in index.shortlist(code, ops, checked.get(row['id'], ())):
            if exhausted():
                break
            fn = store.one("SELECT * FROM functions WHERE id=? AND status='open'", row['id'])
            if fn is None or net.forks.is_asm(other, donor):
                continue
            unit = units[donor['unit']]
            profile = settings.for_unit(unit)
            own = compiler_for(net.cfg(proj), fn['vram'], fn)
            name = fingerprint.name or donor['name'].split('@')[0]
            replacement = settings.replacement_source(unit, name)
            profiles = [p for p in (profile, own) if p and p.get('toolchain') and toolchains.installed(p['toolchain'])]
            if len(profiles) == 2 and profiles[0] == profiles[1]:
                profiles.pop()
            if not profiles and not replacement:
                continue
            names = aliases(fingerprint, donor_ops, net._target_obj(fn, proj), ops, name, fn['name'])
            if console.export_overlay(net._rom_target(fn)) is not None:
                names = donor_relocations.separate_overlay_references(console, names)
            report(job, 'Checking similar imported source', game=other['name'], function=fn['name'],
                   done=index_in_project, total=len(rows))
            accepted = False
            for trial in profiles or [None]:
                if exhausted():
                    break
                job.cross_candidates += 1
                job.cross_tried += 1
                if hasattr(job, 'reuse_limit'):
                    job.reuse_attempts += 1
                try:
                    outcome, why = donor_build.verify(net, proj, fn, other, donor, names, trial, agent, job,
                                                     settings.headers, replacement=replacement)
                except (NetworkError, OSError, ValueError) as error:
                    outcome, why = 'skip', str(error)
                with store.tx() as conn:
                    conn.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,"
                                 "source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                                 (proj['id'], fn['id'], other['id'], donor['id'], outcome,
                                  ('Similar imported source: ' + why)[:240], time.time()))
                if outcome == 'match':
                    matched += 1
                    job.cross_matched += 1
                    accepted = True
                    if hasattr(job, 'reuse_limit'):
                        job.reuse_matched += 1
                        job.reuse_bytes += fn['size']
                    break
            if accepted:
                break
    return matched
