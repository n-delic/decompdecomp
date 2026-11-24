"""Scheduling flags: the Deep Permuter's compiler detection tries the profile's own compiler with another scheduling
setting when the saved best's diff is only the order of the same instructions and the registers that follow
(scheduler_fingerprints).

Three N64 vector-math near-matches built with SN GCC 2.8.1 sat at 50: their range's profile said
`-fno-schedule-insns2`, and without it the saved C was exact."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_profile_sweep, deep_profiles, profile_detection, scheduler_fingerprints
from tests.test_deep_permuter import Log
from tests.test_permute import AGENT
from tests.test_profile_detection import SWEEP, _game

SN_OFF = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips2', '-O2', '-fno-schedule-insns2']}
SN_ON = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips2', '-O2']}

ORDER = [(' ', 'addiu $sp, $sp, -8', 'addiu $sp, $sp, -8'),
         ('o', 'lwc1 $f4, 0x4($a0)', 'lwc1 $f2, 0x0($a0)'),
         ('o', 'lwc1 $f2, 0x0($a0)', 'lwc1 $f4, 0x4($a0)'),
         ('r', 'mul.s $f6, $f2, $f4', 'mul.s $f6, $f4, $f2'),
         ('|', 'beqz $v0, 0x20', 'nop'),
         ('|', 'nop', 'beqz $v0, 0x24')]


def test_variants_turn_the_scheduling_passes_the_other_way():
    v = scheduler_fingerprints.variants
    assert v(SN_OFF['flags']) == [SN_ON['flags']]
    assert v(['-O2', '-fno-schedule-insns', '-fno-schedule-insns2']) == [
        ['-O2', '-fno-schedule-insns'], ['-O2']]
    assert v(['-O2', '-fno-schedule-insns']) == [['-O2']]
    assert v(['-O2']) == [['-O2', '-fno-schedule-insns2'], ['-O2', '-fno-schedule-insns']]
    assert v(['-O1']) == [['-O1', '-fschedule-insns2'], ['-O1', '-fschedule-insns', '-fschedule-insns2']]
    assert v(['-O2', '-fschedule-insns2']) == []  # (a pass the project turns on by name: nothing to say)
    assert all(len(v(f)) <= scheduler_fingerprints.MAX_VARIANTS for f in (['-O2'], ['-O1'], SN_OFF['flags']))


def test_only_order_and_register_rows_are_the_signature():
    ok = scheduler_fingerprints.order_rows_only
    assert ok(ORDER)
    assert not ok(ORDER[:1])  # nothing differs
    assert not ok([])
    assert not ok(ORDER + [('i', 'lw $v0, 0x10($a0)', 'lw $v0, 0x14($a0)')])  # another offset
    assert not ok(ORDER + [('|', 'add.s $f2, $f2, $f4', 'sub.s $f2, $f2, $f4')])  # another opcode
    assert not ok(ORDER + [('<', 'addu $v0, $a0, $a1', '')])  # a missing instruction
    assert not ok([('i', 'lw $v0, 0x10($sp)', 'lw $v0, 0x14($sp)')])  # (a stack offset is a meaning)
    assert ok([('r', 'addu $v0, $a0, $a1', 'addu $v1, $a0, $a1')])  # a register alone is allowed


def test_probes_need_the_rows_and_a_gcc():
    p = scheduler_fingerprints.probes
    assert p(dict(SN_OFF), ORDER) == [SN_ON['flags']]
    assert p(dict(SN_OFF), []) == []
    assert p(dict(SN_OFF), [('i', 'lw $v0, 0x10($a0)', 'lw $v0, 0x14($a0)')]) == []
    assert p({'toolchain': 'ido-7.1', 'flags': ['-O2']}, ORDER) == []
    assert p(dict(SN_OFF, cmd=['cc']), ORDER) == []


def test_a_scheduling_trial_is_a_variant_only_when_nothing_else_changes():
    is_variant = scheduler_fingerprints.is_scheduler_variant
    assert is_variant(SN_OFF['flags'], SN_ON['flags'])
    assert is_variant(['-O2'], ['-O2', '-fno-schedule-insns2'])
    assert not is_variant(SN_OFF['flags'], SN_OFF['flags'])
    assert not is_variant(SN_OFF['flags'], ['-G0', '-mips3', '-O2'])  # (another ISA too)


def _sched_game(monkeypatch, rows=ORDER, best=50):
    net = _game(monkeypatch, best=best)
    cfg = {'platform': 'n64', 'kind': 'rom', 'compiler': SN_OFF, 'compiler_ranges': []}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?, big_endian=1', (json.dumps(cfg),))
    monkeypatch.setattr(deep_profile_sweep, '_ready', lambda tid: tid == 'sn-n64-281')
    net.compiler_profile_usage = lambda proj: [{**SN_OFF, 'matched': 9}]
    net._last_diffs = {(None, net.function()['id']): (rows, '')}
    profile_detection._ROWS.clear()
    return net


def _sweep(net, scores):
    judged = []

    def judge(proj, fn, toolchain, flags, source):
        judged.append(list(flags))
        score = scores['off' if '-fno-schedule-insns2' in flags else 'on']
        return score == 0, SimpleNamespace(score=score, rows=[]), ''
    net._judge_with = judge
    suggested = net.suggested = []
    net.suggest = lambda agent, slug, text, *a, **kw: suggested.append(kw) or 'Suggested.'
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 50, Log(), fn)
    search.compiler_profiles = [SN_OFF]
    return search, judged, suggested, SWEEP(search, AGENT, proj, fn, fn['best_source'])


def test_detection_scores_the_saved_c_without_the_flag_when_only_order_differs(monkeypatch):
    net = _sched_game(monkeypatch)
    proj, fn = net.project_row('game'), net.function()
    ordered, reasons, _, _ = profile_detection.evidence(net, proj, net.cfg(proj), fn, SN_OFF)
    assert ordered == [SN_ON]
    assert 'order of its instructions' in reasons[profile_detection._key(SN_ON)][0]
    # an operand difference is not the scheduler's
    quiet = _sched_game(monkeypatch, rows=[('i', 'lw $v0, 0x10($a0)', 'lw $v0, 0x14($a0)')])
    proj, fn = quiet.project_row('game'), quiet.function()
    assert profile_detection.evidence(quiet, proj, quiet.cfg(proj), fn, SN_OFF)[0] == []


def test_the_rows_come_from_one_judge_compile_when_none_were_kept(monkeypatch):
    net = _sched_game(monkeypatch, rows=[])
    judged = []

    def judge(proj, fn, toolchain, flags, source):
        judged.append(list(flags))
        return False, SimpleNamespace(score=50, rows=ORDER), ''
    net._judge_with = judge
    proj, fn = net.project_row('game'), net.function()
    ordered = profile_detection.evidence(net, proj, net.cfg(proj), fn, SN_OFF)[0]
    profile_detection.evidence(net, proj, net.cfg(proj), fn, SN_OFF)
    assert ordered == [SN_ON] and judged == [SN_OFF['flags']]  # (once: cached per source and settings)


def test_a_better_score_without_the_flag_is_a_finding_and_is_tried_first(monkeypatch):
    net = _sched_game(monkeypatch)
    search, judged, suggested, matched = _sweep(net, {'on': 10, 'off': 50})
    assert not matched and judged and '-fno-schedule-insns2' not in judged[0]
    found = profile_detection.detected(net, net.project_row('game'), net.function())
    assert found['profile'] == SN_ON and found['score'] == 10 and found['own_score'] == 50
    assert deep_profiles.selected(net, net.project_row('game'), 1, net.function())[0] == SN_ON
    assert net.cfg(net.project_row('game'))['compiler'] == SN_OFF  # nothing applied


def test_an_exact_scheduling_trial_is_only_ever_suggested_for_review(monkeypatch):
    net = _sched_game(monkeypatch)
    net.auto_adjusts = lambda proj: True  # (the user lets proven compiler changes apply themselves)
    with pytest.raises(deep_permuter.SearchLimit, match='waits for your approval'):
        _sweep(net, {'on': 0, 'off': 50})
    assert net.suggested and all(kw.get('review_only') is True for kw in net.suggested)
    assert net.suggested[0]['flags'] == ' '.join(SN_ON['flags'])
