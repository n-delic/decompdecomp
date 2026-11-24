"""Small-data threshold (`-G`): the Deep Permuter's compiler detection tries the profile's own compiler with another `-G`,
alone and with the small externs declared as arrays, when the saved best's diff is only address-split rows
(small_data_fingerprints, decomp-repair `extern_size_widen`).

A small N64 function built with SN GCC 2.8.1 (`-G0 -mips2 -O2 -fno-schedule-insns`) sat at 100: it matched with `-G4`
and its integer global declared as a two-word array (`-G4` with the scalar scored 110)."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_profile_sweep, profile_detection, small_data_fingerprints
from tests.test_deep_permuter import Log
from tests.test_permute import AGENT
from tests.test_profile_detection import SWEEP, _game

SN_G0 = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips2', '-O2', '-fno-schedule-insns']}
SN_G4 = dict(SN_G0, flags=['-G4', '-mips2', '-O2', '-fno-schedule-insns'])
SN_G8 = dict(SN_G0, flags=['-G8', '-mips2', '-O2', '-fno-schedule-insns'])
SOURCE = '''extern f32 D_8000949C;
extern s32 D_800B9ECC;

f32 f(f32 arg0) {
    return (f32) D_800B9ECC * arg0 + D_8000949C;
}
'''
ASM = '''glabel f
/* 0 0 3C02800C */  lui        $v0, %hi(D_800B9ECC)
/* 4 4 8C439ECC */  lw         $v1, %lo(D_800B9ECC)($v0)
/* 8 8 3C018000 */  lui        $at, %hi(D_8000949C)
/* C C C4209 */  lwc1       $f2, %lo(D_8000949C)($at)
'''
# the diff at -G0 with the scalar: the float split in ours, `lui $at` in the target (the real one's rows, trimmed)
SPLIT_FLOAT = [(' ', 'lui $v0, %hi(D_800B9ECC)', 'lui $v0, %hi(D_800B9ECC)'),
               (' ', 'lw $v1, %lo(D_800B9ECC)($v0)', 'lw $v1, %lo(D_800B9ECC)($v0)'),
               ('>', '', 'lui $v0, %hi(D_8000949C)'),
               (' ', 'lwc1 $f0, 0x44($s0)', 'lwc1 $f0, 0x44($s0)'),
               ('<', 'lui $at, %hi(D_8000949C)', ''),
               ('r', 'lwc1 $f12, %lo(D_8000949C)($at)', 'lwc1 $f12, %lo(D_8000949C)($v0)')]
# at -G4 with the scalar: the integer's lui is the load's own register (the assembler's expansion), the target's a split
SPLIT_INT = [(' ', 'addiu $a1, $zero, 0x1f', 'addiu $a1, $zero, 0x1f'),
             ('r', 'lui $v0, %hi(D_800B9ECC)', 'lui $v1, %hi(D_800B9ECC)'),
             ('r', 'lw $v1, %lo(D_800B9ECC)($v0)', 'lw $v1, %lo(D_800B9ECC)($v1)'),
             ('>', '', 'nop')]


def test_the_threshold_is_read_from_and_set_in_the_flags():
    t, w = small_data_fingerprints.threshold, small_data_fingerprints.with_threshold
    assert t(SN_G0['flags']) == 0 and t(['-G', '8', '-O2']) == 8 and t(['-O2']) is None and t(['-G0', '-G4']) == 4
    assert w(SN_G0['flags'], 4) == SN_G4['flags']
    assert w(['-O2', '-G', '8', '-mips2'], 0) == ['-O2', '-G0', '-mips2']
    assert w(['-O2'], 4) == ['-O2', '-G4']


def test_a_symbol_built_in_at_on_one_side_and_split_on_the_other_names_the_direction():
    assert small_data_fingerprints.differences(SPLIT_FLOAT) == {'D_8000949C': 'raise'}
    assert small_data_fingerprints.differences(SPLIT_INT) == {'D_800B9ECC': 'lower'}
    assert small_data_fingerprints.differences([(' ', 'nop', 'nop')]) == {}
    assert small_data_fingerprints.differences([('r', 'lui $at, %hi(D_1)', 'lui $at, %hi(D_1)')]) == {}


def test_variants_are_the_other_thresholds_in_the_diffs_direction_and_need_only_address_rows():
    v = small_data_fingerprints.variants
    assert v(SN_G0['flags'], SPLIT_FLOAT) == [SN_G4['flags'], SN_G8['flags']]
    assert v(SN_G8['flags'], SPLIT_INT) == [SN_G4['flags'], SN_G0['flags']]
    assert v(SN_G4['flags'], SPLIT_FLOAT) == [SN_G8['flags'], SN_G0['flags']]
    assert len(v(SN_G0['flags'], SPLIT_FLOAT)) <= small_data_fingerprints.MAX_VARIANTS
    assert v(['-O2'], SPLIT_FLOAT) == []  # (no -G in the profile)
    assert v(SN_G0['flags'], []) == []
    assert v(SN_G0['flags'], SPLIT_FLOAT + [('i', 'lw $v0, 0x10($a0)', 'lw $v0, 0x14($a0)')]) == []  # another offset
    assert v(SN_G0['flags'], SPLIT_FLOAT + [('<', 'addu $v0, $a0, $a1', '')]) == []  # a missing instruction


def test_probes_need_a_gcc_and_why_says_it():
    p = small_data_fingerprints.probes
    assert p(dict(SN_G0), SPLIT_FLOAT) == [SN_G4['flags'], SN_G8['flags']]
    assert p({'toolchain': 'ido-7.1', 'flags': ['-G0', '-O2']}, SPLIT_FLOAT) == []
    assert p(dict(SN_G0, cmd=['cc']), SPLIT_FLOAT) == []
    assert 'builds it in `$at`' in small_data_fingerprints.why(SN_G0['flags'], SPLIT_FLOAT)
    assert small_data_fingerprints.why(SN_G0['flags'], []) is None


def test_a_threshold_trial_is_a_variant_only_when_nothing_else_changes():
    is_variant = small_data_fingerprints.is_small_data_variant
    assert is_variant(SN_G0['flags'], SN_G4['flags']) and is_variant(['-O2', '-G', '8'], ['-O2', '-G0'])
    assert not is_variant(SN_G0['flags'], SN_G0['flags'])
    assert not is_variant(SN_G0['flags'], ['-G4', '-mips3', '-O2', '-fno-schedule-insns'])  # (another ISA too)
    assert not is_variant(['-O2'], ['-O2', '-G4'])  # (no threshold to change)


def test_each_variant_is_judged_alone_then_with_the_small_externs_widened():
    pairs = small_data_fingerprints.pairs(dict(SN_G0), SPLIT_FLOAT, SOURCE, 'f', '', ASM)
    assert [flags for flags, _ in pairs] == [SN_G4['flags'], SN_G4['flags'], SN_G8['flags'], SN_G8['flags']]
    assert pairs[0][1] == SOURCE and pairs[2][1] == SOURCE
    assert 'extern s32 D_800B9ECC[2];' in pairs[1][1] and '(f32) D_800B9ECC[0]' in pairs[1][1]
    assert 'extern s32 D_800B9ECC[3];' in pairs[3][1]  # (a bigger one for -G8)
    assert 'extern f32 D_8000949C;' in pairs[1][1]  # (the float the target keeps in $at stays a scalar)
    assert small_data_fingerprints.pairs(dict(SN_G0), [], SOURCE, 'f', '', ASM) == []


def _game_g(monkeypatch, rows=SPLIT_FLOAT, best=100):
    net = _game(monkeypatch, best=best)
    cfg = {'platform': 'n64', 'kind': 'rom', 'compiler': SN_G0, 'compiler_ranges': []}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?, big_endian=1', (json.dumps(cfg),))
        db.execute('UPDATE functions SET name=?, best_source=?, asm=?', ('f', SOURCE, ASM))
    monkeypatch.setattr(deep_profile_sweep, '_ready', lambda tid: tid == 'sn-n64-281')
    net.compiler_profile_usage = lambda proj: [{**SN_G0, 'matched': 9}]
    net._last_diffs = {(None, net.function()['id']): (rows, '')}
    profile_detection._ROWS.clear()
    return net


def test_detection_proposes_the_other_thresholds_for_a_diff_of_only_address_rows(monkeypatch):
    net = _game_g(monkeypatch)
    proj, fn = net.project_row('game'), net.function()
    ordered, reasons, _, _ = profile_detection.evidence(net, proj, net.cfg(proj), fn, SN_G0)
    # (the same rows are also only a reordering of instructions, so the scheduling variant is proposed beside them)
    assert [p for p in ordered if small_data_fingerprints.is_small_data_variant(SN_G0['flags'], p['flags'])] == [SN_G4, SN_G8]
    assert 'builds it in `$at`' in reasons[profile_detection._key(SN_G4)][0]
    quiet = _game_g(monkeypatch, rows=[('i', 'lw $v0, 0x10($a0)', 'lw $v0, 0x14($a0)')])
    proj, fn = quiet.project_row('game'), quiet.function()
    assert not [p for p in profile_detection.evidence(quiet, proj, quiet.cfg(proj), fn, SN_G0)[0]
                if small_data_fingerprints.is_small_data_variant(SN_G0['flags'], p['flags'])]


def _sweep(net, exact_with):
    """Judge: the scalar C scores 110 under -G4 and 100 under -G0; `exact_with` names the source marker that matches under -G4."""
    judged = []

    def judge(proj, fn, toolchain, flags, source):
        judged.append((list(flags), source))
        exact = '-G4' in flags and exact_with in source
        score = 0 if exact else 110 if '-G4' in flags else 100
        return exact, SimpleNamespace(score=score, rows=[]), ''
    net._judge_with = judge
    suggested = net.suggested = []
    net.suggest = lambda agent, slug, text, *a, **kw: suggested.append(kw) or 'Suggested.'
    fn, proj = net.function(), net.project_row('game')
    search = deep_permuter.Search(net, 1, 50, Log(), fn)
    search.compiler_profiles = [SN_G0]
    return search, judged, suggested, proj, fn


def test_the_widened_source_under_the_other_threshold_is_judged_and_only_suggested_for_review(monkeypatch):
    net = _game_g(monkeypatch)
    net.auto_adjusts = lambda proj: True  # (the user lets proven compiler changes apply themselves)
    search, judged, suggested, proj, fn = _sweep(net, 'D_800B9ECC[2]')
    with pytest.raises(deep_permuter.SearchLimit, match='waits for your approval'):
        SWEEP(search, AGENT, proj, fn, fn['best_source'])
    assert (SN_G4['flags'], SOURCE) in judged  # (the saved C alone under -G4: 110, no finding)
    assert suggested and all(kw.get('review_only') is True for kw in suggested)
    assert suggested[0]['flags'] == ' '.join(SN_G4['flags']) and 'D_800B9ECC[2]' in suggested[0]['source']
    assert net.cfg(net.project_row('game'))['compiler'] == SN_G0  # nothing applied


def test_no_widened_trial_when_the_diff_is_not_address_split(monkeypatch):
    net = _game_g(monkeypatch, rows=[('i', 'lw $v0, 0x10($a0)', 'lw $v0, 0x14($a0)')])
    search, judged, suggested, proj, fn = _sweep(net, 'D_800B9ECC[2]')
    assert not SWEEP(search, AGENT, proj, fn, fn['best_source'])
    assert not any('[2]' in source or '[3]' in source for _, source in judged) and not suggested
