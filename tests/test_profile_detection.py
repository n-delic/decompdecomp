"""Compiler detection for a near-matched function: its saved C scored under every installed profile of its console,
its matched neighbours' profiles and its code's habits (profile_detection, deep_profile_sweep, compiler_fingerprints).

An N64 game built with KMC GCC 2.7.2 had eight near-matches stuck at score 10 that were SN GCC 2.8.1 code."""
import json
import struct
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import compiler_fingerprints as cf
from decompnet.app import deep_permuter, deep_profile_sweep, deep_profiles, profile_detection
from tests.test_deep_permuter import Log, setup

SWEEP = deep_profile_sweep.run  # (the deep permuter's tests stub it out: setup)
from tests.test_permute import AGENT

KMC = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
SN = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips2', '-O2']}
IDO = {'toolchain': 'ido-7.1', 'flags': ['-O2']}


def words(*w):
    return list(w)


# what the two compilers made of the probes, read from their objects (tests/test_profile_detection: the real ones)
KMC_ZERO = words(0x27BDFFE8, 0xAFBF0010, 0x00002821, 0x00003021, 0x0C000000, 0x00003821)  # move $a1..$a3,$zero
SN_ZERO = words(0x27BDFFE8, 0x00002821, 0x00A03021, 0xAFBF0010, 0x0C000000, 0x00A03821)  # move $a2,$a1; move $a3,$a1
KMC_LITERAL = words(0x3C013FC0, 0x44810000, 0x00000000, 0x46006302)  # lui $at; mtc1 $at,$f0
SN_LITERAL = words(0x3C010000, 0xC4200000, 0x46006302)  # lui $at; lwc1 $f0,0($at)
KMC_GLOBAL = words(0x3C030000, 0x8C630004, 0x3C020000, 0x8C420000, 0x03E00008, 0x00621021)  # lui $v1; lw $v1,($v1)
SN_GLOBAL = words(0x3C020000, 0x3C030000, 0x8C440004, 0x8C620000, 0x03E00008, 0x00821021)  # lui $v0; lw $a0,($v0)


def test_the_three_gcc_habits_are_read_from_the_code():
    assert cf.gcc_signals(KMC_ZERO) == {'zero copied between registers': False}
    assert cf.gcc_signals(SN_ZERO) == {'zero copied between registers': True}
    assert cf.gcc_signals(KMC_LITERAL) == {'float literals loaded from memory': False}
    assert cf.gcc_signals(SN_LITERAL) == {'float literals loaded from memory': True}
    assert cf.gcc_signals(KMC_GLOBAL) == {'address register kept for the load': True}
    assert cf.gcc_signals(SN_GLOBAL) == {'address register kept for the load': False}
    # one load through its own register says nothing (SN does that too when nothing else is loaded)
    assert cf.gcc_signals(words(0x3C020000, 0x8C420000, 0x03E00008, 0)) == {}
    # and the whole reading carries them
    code = struct.pack('>6I', *SN_ZERO)
    assert cf.traits(code)['zero copied between registers'] is True


@pytest.mark.skipif(not (toolchains.installed('gcc-2.7.2-kmc') and toolchains.installed('sn-n64-281')),
                    reason='needs KMC GCC 2.7.2 and SN GCC 2.8.1 installed')
def test_the_real_compilers_show_the_habits_that_tell_them_apart():
    kmc = cf.profile_traits('gcc-2.7.2-kmc', ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2'])
    sn = cf.profile_traits('sn-n64-281', ['-G0', '-mips2', '-O2'])
    assert kmc and sn
    for habit, value in {'zero copied between registers': True, 'float literals loaded from memory': True,
                         'address register kept for the load': False}.items():
        assert sn[habit] is value and kmc[habit] is (not value), habit
    # an SN-built target agrees with SN better than with KMC
    target = cf.traits(struct.pack('>6I', *SN_ZERO)) | cf.gcc_signals(SN_LITERAL) | cf.gcc_signals(SN_GLOBAL)
    assert cf.agreement(target, sn) > cf.agreement(target, kmc)


def test_a_clearly_better_profile_or_one_its_neighbours_back_is_a_finding():
    reasons = {profile_detection._key(SN): ['3 of 3 matched neighbours were judged with it']}
    assert profile_detection.conclude(10, [(IDO, 40), (SN, 4)], {}, {}, 0)['profile'] == SN
    assert profile_detection.conclude(10, [(SN, 7)], {}, {}, 0) is None  # better, but not clearly
    votes = {profile_detection._key(SN): 3}
    found = profile_detection.conclude(10, [(SN, 7)], reasons, votes, 3)
    assert found['score'] == 7 and found['own_score'] == 10 and found['reasons'] == reasons[profile_detection._key(SN)]
    assert profile_detection.conclude(10, [(SN, 12)], reasons, votes, 3) is None  # worse: neighbours alone don't do
    assert profile_detection.conclude(10, [(SN, None)], {}, {}, 0) is None  # doesn't compile


def _game(monkeypatch, best=10, ranges=()):
    net = setup(monkeypatch)
    cfg = {'platform': 'n64', 'kind': 'rom', 'compiler': KMC, 'compiler_ranges': list(ranges)}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
        db.execute('UPDATE functions SET best_score=?, rom_offset=0', (best,))
    # (a project whose functions use two KMC profiles: the old sweep tried only those, never SN GCC)
    net.compiler_profile_usage = lambda proj: [{**KMC, 'matched': 9}, {**KMC, 'flags': ['-O1'], 'matched': 2}]
    net.notes, net.suggestions = [], []
    net.add_note = lambda agent, slug, text, function=None, **kw: net.notes.append((function, text, kw))
    net.suggest = lambda agent, slug, text, *a, **kw: net.suggestions.append(text) or 'Suggested.'
    installed = {'gcc-2.7.2-kmc', 'ido-7.1', 'sn-n64-281'}
    monkeypatch.setattr(deep_profile_sweep, '_ready', lambda tid: tid in installed)
    monkeypatch.setattr('decompnet.consoles.default_flags',
                        lambda cfg, tid: SN['flags'] if tid == 'sn-n64-281' else ['-O2'])
    return net


def _sweep(net, scores):
    judged = []

    def judge(proj, fn, toolchain, flags, source):
        judged.append(toolchain)
        score = scores.get(toolchain)
        return score == 0, (SimpleNamespace(score=score, rows=[]) if score is not None else None), ''
    net._judge_with = judge
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 50, Log(), fn)
    search.compiler_profiles = [KMC]
    return search, judged, SWEEP(search, AGENT, proj, fn, fn['best_source'])


def test_a_near_match_is_scored_under_every_installed_profile_and_the_better_one_is_told_and_tried_first(monkeypatch):
    net = _game(monkeypatch)
    search, judged, matched = _sweep(net, {'ido-7.1': 900, 'sn-n64-281': 3})
    # every installed profile, not only the ones the project's functions use
    assert not matched and {'sn-n64-281', 'ido-7.1', 'gcc-2.7.2-kmc'} <= set(judged)
    fn, proj = net.function(), net.project_row('game')
    found = profile_detection.detected(net, proj, fn)
    assert found['profile'] == SN and found['score'] == 3 and found['own_score'] == 10
    # told once: the function's notes, a words-only suggestion (no compiler: nothing applies), the run log
    assert len(net.notes) == 1 and net.notes[0][0] == 'f' and 'sn-n64-281' in net.notes[0][1]
    assert net.notes[0][2] == {'score_before': 10, 'score_after': 3}
    assert len(net.suggestions) == 1 and net.suggestions[0].startswith('f looks built with sn-n64-281')
    assert any(line.startswith('Compiler detection:') for line in search.log.lines)
    # this pass's candidates and the next pass's are probed under it first; the function's compiler is unchanged
    assert search.compiler_profiles[0] == SN
    assert deep_profiles.selected(net, proj, 1, fn)[0] == SN
    assert net.cfg(proj)['compiler'] == KMC
    # a second pass over the same C tells nobody again
    _sweep(net, {'ido-7.1': 900, 'sn-n64-281': 3})
    assert len(net.notes) == 1
    # a new saved C is a new question
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(void) { return 2; }'")
    assert profile_detection.detected(net, proj, net.function()) is None


def test_matched_neighbours_judged_with_one_profile_back_a_smaller_improvement(monkeypatch):
    base = 0x80000000
    net = _game(monkeypatch, ranges=[{'start': base + 0x100, 'end': base + 0x400, **SN}])
    with net.store.tx() as db:
        for n in range(3):
            db.execute("INSERT INTO functions(project_id, name, n_insns, best_score, status, vram, rom_offset) "
                       "VALUES(1, ?, 10, 0, 'matched', ?, ?)", (f'g{n}', base + 0x100 * (n + 1), 0x100 * (n + 1)))
    search, judged, _ = _sweep(net, {'ido-7.1': 900, 'sn-n64-281': 7})
    assert judged[0] == 'sn-n64-281'  # the evidence's profile is scored first
    found = profile_detection.detected(net, net.project_row('game'), net.function())
    assert found['profile'] == SN and found['neighbours'] == 3
    assert any('matched neighbours' in reason for reason in found['reasons'])


def test_nothing_clearly_better_is_said_in_the_log_and_recorded_nowhere(monkeypatch):
    net = _game(monkeypatch)
    search, judged, _ = _sweep(net, {'ido-7.1': 900, 'sn-n64-281': 8})
    assert profile_detection.detected(net, net.project_row('game'), net.function()) is None
    assert not net.notes and not net.suggestions and search.compiler_profiles == [KMC]
    assert any('no profile scores the saved C clearly better' in line for line in search.log.lines)


def test_a_far_off_function_keeps_the_old_sweep(monkeypatch):
    # (detection is for near-matches: a function 2,000 off is still the C's problem)
    net = _game(monkeypatch, best=2000)
    _, judged, _ = _sweep(net, {'ido-7.1': 900, 'sn-n64-281': 3})
    assert judged == ['gcc-2.7.2-kmc'] and not net.notes  # (the -O1 profile its project uses)


def test_an_exact_profile_goes_through_the_profile_recheck(monkeypatch):
    net = _game(monkeypatch)
    seen = []
    monkeypatch.setattr(deep_profiles, 'probe', lambda search, agent, proj, fn, source, profile, **kw:
                        seen.append(profile['toolchain']) or (0 if profile['toolchain'] == 'sn-n64-281' else 50))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 50, Log(), fn)
    assert SWEEP(search, AGENT, proj, fn, fn['best_source']) is True
    assert 'sn-n64-281' in seen and not net.notes  # (probe suggested the exact pair itself)


def test_the_climb_goes_under_the_detected_profile(monkeypatch):
    from decompnet.app import profile_climb
    net = _game(monkeypatch)
    _sweep(net, {'ido-7.1': 900, 'sn-n64-281': 3})
    fn, proj = net.function(), net.project_row('game')
    climbed = []
    monkeypatch.setattr(profile_climb, 'lead', lambda *a, **k: pytest.fail('measured again'))
    monkeypatch.setattr(profile_climb, 'climb', lambda search, agent, proj, fn, source, profile, score, **k:
                        climbed.append((profile, score)) or score)
    search = deep_permuter.Search(net, 1, 50, Log(), fn)
    profile_climb.explore(search, AGENT, proj, fn, 10)
    assert climbed == [(SN, 3)]


def test_the_best_other_toolchain_is_tried_with_the_32_bit_isa_too(monkeypatch):
    # SN GCC's own default is -mips3 ... -mfp64; the game's SN code was -G0 -mips2 -O2
    net = _game(monkeypatch)
    sn_default = ['-G0', '-mips3', '-O2', '-mgas', '-meb', '-mfp64']
    monkeypatch.setattr('decompnet.consoles.default_flags',
                        lambda cfg, tid: sn_default if tid == 'sn-n64-281' else ['-O2'])
    mips2 = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips2', '-O2', '-mgas', '-meb']}
    seen = []
    monkeypatch.setattr(deep_profiles, 'probe', lambda search, agent, proj, fn, source, profile, **kw:
                        seen.append(profile) or (0 if profile == mips2 else
                                                 8 if profile['toolchain'] == 'sn-n64-281' else 50))
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 50, Log(), fn)
    assert SWEEP(search, AGENT, proj, fn, fn['best_source']) is True
    assert seen[-1] == mips2
