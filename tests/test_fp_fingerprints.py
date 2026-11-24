"""-mfp32 versus -mfp64: the Deep Permuter's compiler detection tries the same compiler with the other floating-point
register model when the target's single-precision registers or the diff's rows say so (fp_fingerprints).

Three N64 near-matches built with SN GCC 2.8.1 were stuck at 25, 10 and a few rows because the project said -mfp64;
under -mfp32 they were exact. The diff of the best C was only FP register rows."""
import json
import struct
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_profile_sweep, deep_profiles, fp_fingerprints, profile_detection
from tests.test_deep_permuter import Log
from tests.test_permute import AGENT
from tests.test_profile_detection import SWEEP, _game

SN64 = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips3', '-O2', '-mgas', '-meb', '-mfp64']}
SN32 = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips3', '-O2', '-mgas', '-meb', '-mfp32']}


def lwc1(ft, base=4):
    return 0xC4000000 | base << 21 | ft << 16


def swc1(ft, base=4):
    return 0xE4000000 | base << 21 | ft << 16


def fop(fn, fd, fs, ft=0, fmt=16):
    return 0x44000000 | fmt << 21 | ft << 16 | fs << 11 | fd << 6 | fn


def code(*words):
    return struct.pack(f'>{len(words)}I', *words)


# a scale-and-store: lwc1 $f2; lwc1 $f4; mul.s $f2,$f2,$f4; swc1 $f2 (what the -mfp32 compile wrote)
EVEN = code(lwc1(2), lwc1(4, 5), fop(2, 2, 2, 4), swc1(2))
# the -mfp64 compile of the same C: odd registers
ODD = code(lwc1(1), lwc1(3, 5), fop(2, 1, 1, 3), swc1(1))
# one single in one register only says nothing
LONE = code(lwc1(2), swc1(2))
# a double's two halves loaded with lwc1 are not an operation on odd singles
DOUBLE_HALVES = code(lwc1(2), lwc1(3, 5), fop(0, 4, 2, 6, fmt=17))


def test_the_registers_of_single_precision_code_are_read():
    assert fp_fingerprints.registers(EVEN) == ({2, 4}, {2, 4}, set())
    assert fp_fingerprints.parity(EVEN) == 'even'
    assert fp_fingerprints.parity(ODD) == 'odd'
    assert fp_fingerprints.parity(LONE) is None
    assert fp_fingerprints.parity(DOUBLE_HALVES) is None
    assert fp_fingerprints.parity(code(0x03E00008, 0)) is None  # no floating point at all
    little = struct.pack('<4I', lwc1(2), lwc1(4, 5), fop(2, 2, 2, 4), swc1(2))
    assert fp_fingerprints.parity(little, big_endian=False) == 'even'


def test_the_flag_is_flipped_where_it_stands():
    assert fp_fingerprints.mode(SN64['flags']) == '64' and fp_fingerprints.mode(SN32['flags']) == '32'
    assert fp_fingerprints.mode(['-O2']) == '32'
    assert fp_fingerprints.flipped(SN64['flags']) == SN32['flags']
    assert fp_fingerprints.flipped(SN32['flags']) == SN64['flags']
    assert fp_fingerprints.flipped(['-O2']) == ['-O2', '-mfp64']
    assert fp_fingerprints.is_fp_variant(SN64['flags'], SN32['flags'])
    assert not fp_fingerprints.is_fp_variant(SN64['flags'], SN64['flags'])
    assert not fp_fingerprints.is_fp_variant(SN64['flags'], ['-G0', '-mips2', '-O2', '-mfp32'])  # (a different ISA too)


def test_a_diff_of_only_fp_register_rows_is_the_signature():
    only = [(' ', 'addiu $sp, $sp, -8', 'addiu $sp, $sp, -8'),
            ('r', 'lwc1 $f2, 0x10($a0)', 'lwc1 $f1, 0x10($a0)'),
            ('r', 'mul.s $f2, $f2, $f4', 'mul.s $f1, $f1, $f3'),
            ('r', 'ldc1 $f2, 0($v0)', 'ldc1 $f0, 0($v0)')]
    assert fp_fingerprints.register_rows_only(only)
    assert not fp_fingerprints.register_rows_only(only[:1])  # nothing differs
    assert not fp_fingerprints.register_rows_only(only + [('r', 'addu $v0, $a0, $a1', 'addu $v1, $a0, $a1')])  # a GPR row
    assert not fp_fingerprints.register_rows_only(only + [('<', 'nop', '')])  # a missing instruction
    assert not fp_fingerprints.register_rows_only(only + [('|', 'add.s $f2, $f2, $f4', 'sub.s $f1, $f1, $f3')])


def test_probe_needs_the_evidence_and_a_gcc():
    own = dict(SN64)
    assert fp_fingerprints.probe(own, EVEN) == SN32['flags']
    assert fp_fingerprints.probe(own, ODD) is None  # it already agrees with -mfp64
    assert fp_fingerprints.probe(own, LONE) is None
    assert fp_fingerprints.probe(dict(SN32), ODD) == SN64['flags']  # the reverse: -mfp32 project, odd arithmetic
    assert fp_fingerprints.probe(dict(SN32), EVEN) is None
    rows = [('r', 'lwc1 $f2, 0($a0)', 'lwc1 $f1, 0($a0)')]
    assert fp_fingerprints.probe(own, LONE, rows) == SN32['flags']  # the rows alone say it too
    assert fp_fingerprints.probe(own, ODD, rows) is None  # (the target's registers agree with -mfp64: not the cause)
    assert fp_fingerprints.probe({'toolchain': 'ido-7.1', 'flags': ['-O2']}, EVEN) is None


def _fp_game(monkeypatch, target=EVEN, best=25):
    net = _game(monkeypatch, best=best)
    cfg = {'platform': 'n64', 'kind': 'rom', 'compiler': SN64, 'compiler_ranges': []}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?, big_endian=1', (json.dumps(cfg),))
        db.execute('UPDATE functions SET target=?', (target,))
    installed = {'sn-n64-281'}
    monkeypatch.setattr(deep_profile_sweep, '_ready', lambda tid: tid in installed)
    net.compiler_profile_usage = lambda proj: [{**SN64, 'matched': 9}]
    return net


def _sweep(net, scores):
    judged = []

    def judge(proj, fn, toolchain, flags, source):
        judged.append(list(flags))
        score = scores.get(fp_fingerprints.mode(flags))
        return score == 0, SimpleNamespace(score=score, rows=[]), ''
    net._judge_with = judge
    suggested = net.suggested = []
    net.suggest = lambda agent, slug, text, *a, **kw: suggested.append(kw) or 'Suggested.'
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 50, Log(), fn)
    search.compiler_profiles = [SN64]
    return search, judged, suggested, SWEEP(search, AGENT, proj, fn, fn['best_source'])


def test_detection_scores_the_saved_c_under_the_other_fp_model_first(monkeypatch):
    net = _fp_game(monkeypatch)
    proj, fn = net.project_row('game'), net.function()
    ordered, reasons, _, _ = profile_detection.evidence(net, proj, net.cfg(proj), fn, SN64)
    assert ordered == [SN32]
    assert 'even FP registers' in reasons[profile_detection._key(SN32)][0]
    # a target whose registers say nothing is not asked
    quiet = _fp_game(monkeypatch, target=LONE)
    proj, fn = quiet.project_row('game'), quiet.function()
    assert profile_detection.evidence(quiet, proj, quiet.cfg(proj), fn, SN64)[0] == []


def test_a_better_fp32_score_is_a_finding_and_is_tried_first(monkeypatch):
    net = _fp_game(monkeypatch)
    # (the sweep stops at the profiles the evidence names: SN is the only installed compiler)
    search, judged, suggested, matched = _sweep(net, {'32': 5, '64': 25})
    assert not matched and judged and fp_fingerprints.mode(judged[0]) == '32'
    found = profile_detection.detected(net, net.project_row('game'), net.function())
    assert found['profile'] == SN32 and found['score'] == 5 and found['own_score'] == 25
    assert deep_profiles.selected(net, net.project_row('game'), 1, net.function())[0] == SN32
    assert net.cfg(net.project_row('game'))['compiler'] == SN64  # nothing applied


def test_an_exact_fp32_trial_is_only_ever_suggested_for_review(monkeypatch):
    net = _fp_game(monkeypatch)
    net.auto_adjusts = lambda proj: True  # (the user lets proven compiler changes apply themselves)
    with pytest.raises(deep_permuter.SearchLimit, match='waits for your approval'):
        _sweep(net, {'32': 0, '64': 25})
    suggested = net.suggested
    assert suggested and all(kw.get('review_only') is True for kw in suggested)
    assert suggested[0]['flags'] == ' '.join(SN32['flags'])
    # another compiler's exact pair keeps the ordinary route
    other = {'toolchain': 'ido-7.1', 'flags': ['-O2']}
    assert not fp_fingerprints.is_fp_variant(SN64['flags'], other['flags'])


def test_a_kmc_game_whose_sn_default_is_fp64_is_asked_for_sn_with_fp32(monkeypatch):
    # (the real case: the project builds with KMC GCC; SN GCC's own default is -mfp64, and the target's singles are even)
    net = _game(monkeypatch)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET big_endian=1')
        db.execute('UPDATE functions SET target=?', (EVEN,))
    monkeypatch.setattr('decompnet.consoles.default_flags',
                        lambda cfg, tid: SN64['flags'] if tid == 'sn-n64-281' else ['-O2'])
    proj, fn = net.project_row('game'), net.function()
    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    ordered, reasons, _, _ = profile_detection.evidence(net, proj, net.cfg(proj), fn, own)
    assert SN32 in ordered and SN64 not in ordered
    assert 'even FP registers' in reasons[profile_detection._key(SN32)][0]
    assert profile_detection.candidates(net, proj, fn, [])[0][0] == SN32
    # odd arithmetic in the target: nothing to flip on the -mfp64 default, the -mfp32 KMC game is asked for -mfp64
    with net.store.tx() as db:
        db.execute('UPDATE functions SET target=?', (ODD,))
    ordered = profile_detection.evidence(net, proj, net.cfg(proj), net.function(), own)[0]
    assert ordered == [{'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2', '-mfp64']}]


def test_the_deep_climb_measures_the_other_fp_model_first_when_only_fp_rows_differ(monkeypatch):
    from decompnet.app import compiler_fingerprints as cf
    from decompnet.app import profile_climb
    from tests.test_compiler_detection import FN, PROJ, Search
    monkeypatch.setattr('decompnet.project.compiler_for', lambda *args: dict(SN64))
    monkeypatch.setattr(cf, 'ranked', lambda *args, **kwargs: [])
    rows = [('r', 'lwc1 $f2, 0($a0)', 'lwc1 $f1, 0($a0)'), ('r', 'ldc1 $f2, 8($a0)', 'ldc1 $f0, 8($a0)')]
    search = Search({})
    search.net._last_diffs = {(None, FN['id']): (rows, '')}
    measured = []

    def judge(project, function, toolchain, flags, source):
        measured.append(flags)
        return '-mfp32' in flags, SimpleNamespace(score=0 if '-mfp32' in flags else 25), ''
    search.net._judge_with = judge
    function = dict(FN, target=LONE)  # (its registers say nothing: the rows do)
    found = profile_climb.lead(search, None, PROJ, function, FN['best_source'], 25)
    assert found == (SN32, 0) and measured[0] == SN32['flags']


def test_a_double_constant_in_an_even_register_is_read_too():
    # lwc1 $f0; ldc1 $f2; cvt.d.s $f0,$f0; c.lt.d $f0,$f2 (a single compared with a double constant)
    even = code(lwc1(0), 0xD4820000 | 4 << 21, fop(0x21, 0, 0), fop(0x3C, 0, 0, 2, fmt=17))
    assert fp_fingerprints.parity(even) == 'even'
    assert fp_fingerprints.probe(dict(SN64), even) == SN32['flags']
    odd = code(lwc1(0), 0xD4010000 | 4 << 21, fop(0x21, 0, 0), fop(0x3C, 0, 0, 1, fmt=17))  # ldc1 $f1: only -mfp64
    assert fp_fingerprints.parity(odd) == 'odd'
    assert fp_fingerprints.probe(dict(SN64), odd) is None and fp_fingerprints.probe(dict(SN32), odd) == SN64['flags']
