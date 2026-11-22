"""Find and verify close imported functions whose instruction layouts have changed."""

import time
from collections import Counter, defaultdict
from dataclasses import replace
from difflib import SequenceMatcher
from itertools import chain

from .. import similarity, toolchains
from ..elfobj import Reloc, mask_relocs
from ..network import NetworkError
from ..project import compiler_for
from . import donor_build, donor_relocations, free_candidates
from .cross_game_progress import report
from .twins import Print, renames

MIN_SCORE = .84
MIN_ORDER_WITH_OPERANDS = .74
MIN_OPERAND_AGREEMENT = .60
MAX_SHORTLIST = 24
MAX_TRIALS = 3
MAX_OPERAND_SHORTLIST = 40
MAX_OPERAND_TRIALS = 2


def _grams(ops):
    return set(zip(ops, ops[1:], ops[2:]))


def _rank_pair(ops, words, donor_ops, donor_words):
    """Keep the order score and measure operand agreement within the aligned runs."""
    matcher = SequenceMatcher(None, ops, donor_ops, autojunk=False)
    order = matcher.ratio()
    equal = sum(words[run.a + i] == donor_words[run.b + i]
                for run in matcher.get_matching_blocks() for i in range(run.size))
    return order, 2 * equal / (len(words) + len(donor_words))


def _words(code, relocs, big):
    masked = mask_relocs(code, relocs, 'mips', big)
    return tuple(masked[i:i + 4] for i in range(0, len(masked) - 3, 4))


class Index:
    def __init__(self, entries, big, cpu, stop):
        self.big = big
        self.entries = {}
        self.words = {}
        self.shingles = defaultdict(set)
        for donor, fingerprint, code in entries:
            if stop.is_set():
                break
            ops = similarity.mips_mnemonics(code['target'], big, cpu=cpu)
            fid = donor['id']
            grams = _grams(ops)
            self.entries[fid] = donor, fingerprint, code['target'], ops, len(grams)
            self.words[fid] = _words(code['target'], [Reloc(at, kind, '', 0) for at, kind in fingerprint.layout], big)
            for gram in grams:
                self.shingles[gram].add(fid)

    def shortlist(self, code, ops, excluded=(), *, relocs=()):
        grams = _grams(ops)
        counts = Counter(chain.from_iterable(self.shingles.get(gram, ()) for gram in grams))
        rough = []
        for fid, shared in counts.items():
            _, _, donor_code, donor_ops, total = self.entries[fid]
            ratio = len(donor_ops) / len(ops)
            if fid in excluded or donor_code == code or not .65 <= ratio <= 1 / .65:
                continue
            rough.append((2 * shared / (len(grams) + total), fid, ratio))
        ordered = sorted(rough, reverse=True)
        primary = [fid for _, fid, ratio in ordered if .75 <= ratio <= 1 / .75][:MAX_SHORTLIST]
        extra = [fid for _, fid, _ in ordered[:MAX_OPERAND_SHORTLIST]]
        words = _words(code, relocs, self.big)
        scores = {fid: _rank_pair(ops, words, self.entries[fid][3], self.words[fid])
                  for fid in dict.fromkeys(primary + extra)}
        normal = sorted(((scores[fid][0], fid) for fid in primary if scores[fid][0] >= MIN_SCORE),
                        reverse=True)[:MAX_TRIALS]
        selected = {fid for _, fid in normal}
        # Keep the original mnemonic-only candidates. Operand agreement can add
        # plausible shifted bodies without hiding donors that are valid today.
        fallback = []
        for fid in extra:
            order, operands = scores[fid]
            if fid not in selected and order >= MIN_ORDER_WITH_OPERANDS and operands >= MIN_OPERAND_AGREEMENT:
                fallback.append((order + operands / 5, fid))
        return [self.entries[fid] for _, fid in normal + sorted(fallback, reverse=True)[:MAX_OPERAND_TRIALS]]


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

    rows = store.all("SELECT id,name,target,relocs,vram,meta,unit,size,rom_offset FROM functions "
                     "WHERE project_id=? AND status='open' "
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
        target = net._target_obj(row, proj)
        candidates = index.shortlist(code, ops, checked.get(row['id'], ()), relocs=target.relocs)
        for donor, fingerprint, _, donor_ops, _ in candidates:
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
            names = aliases(fingerprint, donor_ops, target, ops, name, fn['name'])
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
                                                     settings.headers, replacement=replacement, compiled_literals=True)
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
