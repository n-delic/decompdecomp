"""The deep pass detects code built with other compiler settings (compiler_fingerprints) and climbs under the profile
that fits (profile_climb), instead of only probing other profiles for an exact match."""
import struct
from types import SimpleNamespace

import pytest

from decompnet.app import compiler_fingerprints as cf
from decompnet.app import profile_climb


def code(*words):
    return struct.pack(f">{len(words)}I", *words)


ADDIU_SP = 0x27BDFFE8  # addiu $sp, $sp, -0x18
SW_RA = 0xAFBF0010  # sw $ra, 0x10($sp)
SW_S0 = 0xAFB00014  # sw $s0, 0x14($sp)
JR_RA = 0x03E00008
NOP = 0
MUL_S = 0x46000002  # mul.s (float work)
OR_MOVE = 0x00802025  # or $a0, $a0, $zero
ADDU_MOVE = 0x00802021  # addu $a0, $a0, $zero


def test_a_frame_made_after_the_arithmetic_is_read_as_a_late_prologue():
    late = cf.traits(code(MUL_S, MUL_S, ADDIU_SP, SW_RA, JR_RA, NOP))
    early = cf.traits(code(ADDIU_SP, SW_RA, MUL_S, JR_RA, NOP))
    assert late['frame first'] is False and early['frame first'] is True
    assert late['16-byte frames'] is False  # 0x18 isn't a multiple of 16


def test_save_order_and_moves_are_read():
    t = cf.traits(code(ADDIU_SP, SW_S0, SW_RA, OR_MOVE, JR_RA, NOP))
    assert t['return address saved last'] is True and t['moves with or'] is True
    assert cf.traits(code(ADDU_MOVE, JR_RA, NOP))['moves with or'] is False


def test_agreement_counts_shared_habits_and_ignores_unknown_ones():
    target = {'frame first': False, 'moves with or': False, 'branch-likely': True}
    assert cf.agreement(target, {'frame first': False, 'moves with or': False}) == 2
    assert cf.agreement(target, {'frame first': True, 'moves with or': False}) == 0
    assert cf.agreement(target, None) == 0


def test_profiles_rank_by_agreement(monkeypatch):
    late = {'toolchain': 'late-cc', 'flags': ['-O2']}
    other = {'toolchain': 'other-cc', 'flags': ['-O2']}
    monkeypatch.setattr(cf, 'candidates', lambda *a: [other, late])
    habits = {'own-cc': {'frame first': True}, 'late-cc': {'frame first': False, '16-byte frames': False},
              'other-cc': {'frame first': True}}
    monkeypatch.setattr(cf, 'profile_traits', lambda tc, flags, be=True: habits[tc])
    fn = {'target': code(MUL_S, MUL_S, ADDIU_SP, SW_RA, JR_RA, NOP)}
    proj = {'big_endian': 1}
    ranked = cf.ranked(None, proj, {}, fn, {'toolchain': 'own-cc', 'flags': ['-O2']})
    assert ranked[0][1] == late and ranked[0][0] == 2


class Search:
    def __init__(self, scores, limit=100):
        self.tested, self.limit, self.logs, self.profile_bests = 0, limit, [], {}
        self.scores = scores
        self.net = SimpleNamespace(cfg=lambda proj: {}, _context=lambda pid, unit: {'header': ''},
                                   _judge_with=self.judge)

    def judge(self, proj, fn, tc, flags, source):
        score = self.scores.get((tc, source), 500)
        return score == 0, SimpleNamespace(score=score), ''

    def check(self):
        pass

    def report(self):
        pass

    def log(self, line):
        self.logs.append(line)


FN = {'id': 1, 'name': 'f', 'vram': 0, 'unit': '', 'asm': '', 'best_source': 'int f(void) { return 1; }'}
PROJ = {'id': 1, 'arch': 'mips', 'slug': 'game'}
LATE = {'toolchain': 'late-cc', 'flags': ['-O2']}


def test_lead_is_the_profile_scoring_the_current_c_better(monkeypatch):
    monkeypatch.setattr('decompnet.project.compiler_for', lambda cfg, vram, fn: {'toolchain': 'own-cc', 'flags': ['-O2']})
    monkeypatch.setattr(cf, 'ranked', lambda *a, **k: [(2, LATE)])
    search = Search({('late-cc', FN['best_source']): 10})
    assert profile_climb.lead(search, None, PROJ, FN, FN['best_source'], 155) == (LATE, 10)
    assert profile_climb.lead(search, None, PROJ, FN, FN['best_source'], 5) is None  # not better than its own


def test_the_climb_hands_an_exact_pair_to_the_profile_recheck(monkeypatch):
    better = 'int f(void) { return 2; }'
    monkeypatch.setattr('decompnet.app.draft_repairs.variants', lambda *a, **k: [better])
    monkeypatch.setattr('decomprepair.permuter.mutate', lambda *a, **k: None)
    probed = []
    monkeypatch.setattr('decompnet.app.deep_profiles.probe', lambda search, agent, proj, fn, source, profile:
                        probed.append((source, profile)))
    search = Search({('late-cc', FN['best_source']): 10, ('late-cc', better): 0})
    assert profile_climb.climb(search, None, PROJ, FN, FN['best_source'], LATE, 10) == 0
    assert probed == [(better, LATE)]


def test_the_climb_keeps_its_own_best_and_stops_when_nothing_helps(monkeypatch):
    closer = 'int f(void) { return 3; }'
    monkeypatch.setattr('decompnet.app.draft_repairs.variants', lambda *a, **k: [closer])
    monkeypatch.setattr('decomprepair.permuter.mutate', lambda *a, **k: None)
    monkeypatch.setattr('decompnet.app.deep_profiles.probe', lambda *a: (_ for _ in ()).throw(AssertionError('not exact')))
    search = Search({('late-cc', FN['best_source']): 10, ('late-cc', closer): 4})
    assert profile_climb.climb(search, None, PROJ, FN, FN['best_source'], LATE, 10) == 4
    assert search.profile_bests['late-cc -O2'] == (4, closer)


def test_a_profile_that_already_matches_goes_straight_to_the_recheck(monkeypatch):
    probed = []
    monkeypatch.setattr('decompnet.app.deep_profiles.probe', lambda search, agent, proj, fn, source, profile:
                        probed.append(profile))
    search = Search({})
    assert profile_climb.climb(search, None, PROJ, FN, FN['best_source'], LATE, 0) == 0
    assert probed == [LATE] and search.tested == 0


# A byte-compare loop: lbu $v1,($a0); addiu $a0,1; lbu $v0,($a1); subu $a2,$v1,$v0. Scheduling off keeps the C's
# order, so the second load's result is waited for while the addiu before it could have filled the gap.
LBU_A0, ADDIU_A0, LBU_A1, SUBU = 0x90830000, 0x24840001, 0x90A20000, 0x00623023


def test_a_load_left_waiting_is_read_as_scheduling_off():
    assert cf.traits(code(LBU_A0, ADDIU_A0, LBU_A1, SUBU, JR_RA, NOP))['unscheduled loads'] is True
    # (scheduled: the two loads first, the addiu between them and the use)
    assert cf.traits(code(LBU_A0, LBU_A1, ADDIU_A0, SUBU, LBU_A0, ADDIU_A0, JR_RA, NOP)) \
        .get('unscheduled loads') is False


def test_a_profile_its_code_points_to_is_climbed_even_when_the_current_c_scores_worse(monkeypatch):
    """A library strcmp built with scheduling off: its saved C, written in the scheduler's order, scored 130 under
    the game's settings and 890 with scheduling off, where plain C was exact. Only ever climbing toward better
    scores never went there."""
    off = {'toolchain': 'own-cc', 'flags': ['-O2', '-fno-schedule-insns', '-fno-schedule-insns2']}
    monkeypatch.setattr('decompnet.project.compiler_for', lambda cfg, vram, fn: {'toolchain': 'own-cc', 'flags': ['-O2']})
    monkeypatch.setattr(cf, 'ranked', lambda *a, **k: [(2, off), (0, LATE)])
    monkeypatch.setattr(cf, 'own_agreement', lambda *a: 1)
    search = Search({('own-cc', FN['best_source']): 890, ('late-cc', FN['best_source']): 2000})
    assert profile_climb.lead(search, None, PROJ, FN, FN['best_source'], 130) == (off, 890)
    monkeypatch.setattr(cf, 'own_agreement', lambda *a: 2)  # (agreeing no more than its own: no reason to go there)
    assert profile_climb.lead(search, None, PROJ, FN, FN['best_source'], 130) is None


def test_a_much_closer_profile_is_told_once_in_words_and_never_applied():
    from decompnet.app.matching_preflight import closer
    said = []
    cfg = {'suggestions': []}

    def suggest(agent, slug, text, **kw):
        assert not kw  # (words only: no compiler, so nothing can apply)
        said.append(text)
        cfg['suggestions'].append({'status': 'open', 'text': text})
        return 'Suggested'
    net = SimpleNamespace(suggest=suggest, cfg=lambda proj: cfg, project_row=lambda slug: {})
    profile = {'toolchain': 'kmc', 'flags': ['-O2', '-fno-schedule-insns2']}
    assert closer(net, None, 'game', 'f', profile, 130, 10)
    assert said == ['f comes closer under kmc -O2 -fno-schedule-insns2: 130 → 10, not exact yet. '
                    'Its C still needs work under those settings before they can apply to it.']
    assert closer(net, None, 'game', 'f', profile, 130, 10) is None and len(said) == 1  # already on the page
    assert closer(net, None, 'game', 'f', profile, 130, 80) is None  # not much closer
    assert closer(net, None, 'game', 'f', profile, 130, 0) is None  # exact goes through the recheck instead


def test_profile_proposal_generation_observes_the_search_deadline():
    import pytest

    from decompnet.app.deep_permuter import SearchLimit

    search = Search({})
    def expired():
        raise SearchLimit('Time limit reached')
    def generate(args, options, check):
        assert args[-1] == LATE['flags']
        check()
    search.check = expired
    search.compile_pool = SimpleNamespace(proposal_pool=lambda: SimpleNamespace(generate=generate))
    with pytest.raises(SearchLimit, match='Time limit reached'):
        profile_climb.climb(search, None, PROJ, FN, FN['best_source'], LATE, 10)
    assert search.tested == 0


def test_nearby_exact_profiles_supply_one_exact_only_trial(monkeypatch):
    own = {'toolchain': 'own-cc', 'flags': ['-O2']}
    alternate = {'toolchain': 'neighbor-cc', 'flags': ['-O1']}
    neighbours = [
        {'name': 'first', 'rom_offset': 10, 'vram': 20},
        {'name': 'second', 'rom_offset': 12, 'vram': 22},
    ]
    cfg = {'compiler': own, 'function_compilers': {
        f"{row['rom_offset']}:{row['vram']}": {**alternate, 'vram': row['vram']}
        for row in neighbours
    }}
    monkeypatch.setattr(cf, 'ranked', lambda *a, **k: [(0, LATE)])
    tried = []

    def search_with_score(value, limit=100):
        search = Search({}, limit)
        search.net.cfg = lambda p: cfg
        search.net.project_store = lambda ident: SimpleNamespace(all=lambda *a: neighbours)
        def judge(proj, fn, toolchain, flags, source):
            tried.append(toolchain)
            score = value if toolchain == 'neighbor-cc' else 500
            return score == 0, SimpleNamespace(score=score), ''

        search.net._judge_with = judge
        return search

    assert profile_climb.lead(search_with_score(0), None, PROJ, FN, FN['best_source'], 155) == (alternate, 0)
    assert tried == ['late-cc', 'neighbor-cc']
    tried.clear()
    assert profile_climb.lead(search_with_score(5), None, PROJ, FN, FN['best_source'], 155) is None
    assert tried == ['late-cc', 'neighbor-cc']
    tried.clear()
    assert profile_climb.lead(search_with_score(0, limit=1), None, PROJ, FN, FN['best_source'], 155) is None
    assert tried == ['late-cc']
    tried.clear()
    neighbours.pop()
    assert profile_climb.lead(search_with_score(0), None, PROJ, FN, FN['best_source'], 155) is None
    assert tried == ['late-cc']


def test_deep_climb_tries_disable_cross_block_cse_for_repeated_addresses(monkeypatch):
    from decompnet.app.profile_hypotheses import kmc_cse_address_probe

    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    high, low = 'lui $a1, %hi(table)', 'addiu $a1, $a1, %lo(table)'
    repeat = [(' ', high, high), (' ', low, low)]
    rows = repeat + [('<', high, ''), ('<', low, '')] * 2
    assert kmc_cse_address_probe(own, repeat) is None
    assert kmc_cse_address_probe(own, rows[:-2]) is None
    flags = ['-O2', '-fno-cse-skip-blocks']
    assert kmc_cse_address_probe(own, rows) == flags
    assert kmc_cse_address_probe(dict(own, flags=flags), rows) is None

    monkeypatch.setattr('decompnet.project.compiler_for', lambda *args: own)
    monkeypatch.setattr(cf, 'ranked', lambda *args, **kwargs: [(2, LATE)])
    search = Search({})
    search.net._last_diffs = {(None, FN['id']): (rows, '')}
    measured = []

    def judge(project, function, toolchain, flags, source):
        measured.append(flags)
        return False, SimpleNamespace(score=1160 if '-fno-cse-skip-blocks' in flags else 2391), ''

    search.net._judge_with = judge
    assert profile_climb.lead(search, None, PROJ, FN, FN['best_source'], 2391) == (dict(own, flags=flags), 1160)
    assert measured[0] == flags


def test_profile_phase_shares_remaining_time_with_local_repairs(monkeypatch):
    clock = [20.0]
    monkeypatch.setattr(profile_climb.time, 'monotonic', lambda: clock[0])
    logs = []
    search = SimpleNamespace(deadline=60.0, check=lambda: None, log=logs.append)
    visited = []

    def lead(search, agent, proj, fn, source, baseline, *, check):
        check()
        clock[0] = 28.0
        return LATE, 10

    def climb(search, agent, proj, fn, source, profile, score, *, require_clean, check):
        check()
        visited.append((profile, score))
        clock[0] = 30.0
        check()
        pytest.fail('Alternate profile ran through its deadline')

    monkeypatch.setattr(profile_climb, 'lead', lead)
    monkeypatch.setattr(profile_climb, 'climb', climb)
    assert not profile_climb.explore(search, None, PROJ, FN, 155)
    assert visited == [(LATE, 10)]
    assert clock[0] < search.deadline
    assert any('continuing with the current settings' in line for line in logs)


def test_profile_phase_propagates_user_cancellation(monkeypatch):
    def cancelled():
        raise RuntimeError('cancelled')

    search = SimpleNamespace(deadline=profile_climb.time.monotonic() + 100, check=cancelled)
    with pytest.raises(RuntimeError, match='cancelled'):
        profile_climb.explore(search, None, PROJ, FN, 155)


def test_kmc_cse_probe_recognizes_repeated_relocated_store_addresses():
    from decompnet.app.profile_hypotheses import kmc_cse_address_probe

    profile = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    high = 'lui $at, %hi(cursor)'
    store = 'sw $v0, %lo(cursor)($at)'
    shared = [(' ', high, high), (' ', store, store)]
    reloads = [('<', high, ''), (' ', store, store)] * 2
    assert kmc_cse_address_probe(profile, shared + reloads) == ['-O2', '-fno-cse-skip-blocks']
    assert kmc_cse_address_probe(profile, shared + reloads[:2]) is None
    for unrelated in ('sw $v0, %lo(other)($at)', 'sw $v0, %lo(cursor)($v1)'):
        assert kmc_cse_address_probe(profile, shared + [('<', high, ''), ('<', unrelated, '')] * 2) is None


def test_kmc_expensive_probe_recognizes_a_missing_zero_extension():
    from decompnet.app.profile_hypotheses import kmc_expensive_probe

    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-G0', '-O2']}
    shared = [(' ', 'lhu $a3, 0($a1)', 'lhu $a3, 0($a1)'), (' ', 'ori $v0, $zero, 0xffff', 'ori $v0, $zero, 0xffff')]
    missing = [('<', 'andi $v1, $a3, 0xffff', ''), ('r', 'beq $v1, $v0, 0xc8', 'beq $a3, $v0, 0xc4')]
    flags = ['-G0', '-O2', '-fno-expensive-optimizations']
    assert kmc_expensive_probe(own, shared + missing) == flags
    assert kmc_expensive_probe(dict(own, flags=flags), shared + missing) is None
    assert kmc_expensive_probe(dict(own, flags=['-O2', '-fexpensive-optimizations']), shared + missing) is None
    assert kmc_expensive_probe(dict(own, toolchain='ido-7.1'), shared + missing) is None
    assert kmc_expensive_probe(dict(own, flags=['-O1']), shared + missing) is None
    assert kmc_expensive_probe(own, shared) is None
    # a different andi (a mask that is not a zero extension), or one the candidate has too, is not the symptom
    assert kmc_expensive_probe(own, shared + [('<', 'andi $v1, $a3, 0xfff', '')]) is None
    assert kmc_expensive_probe(own, shared + [('o', 'andi $v1, $a3, 0xffff', 'andi $v1, $a3, 0xffff')]) is None


def test_flag_search_tries_expensive_optimizations_off_for_kmc_gcc():
    from decompnet.app.engine.compilers import CompilerMixin

    base = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
    variants = CompilerMixin.flag_variants(object.__new__(CompilerMixin), 'gcc-2.7.2-kmc', base)
    assert [*base, '-fno-expensive-optimizations'] in variants


def test_kmc_cse_skip_probe_drops_the_option_when_the_candidate_reloads_a_field():
    from decompnet.app.profile_hypotheses import kmc_cse_skip_probe

    flags = ['-O2', '-fno-cse-skip-blocks']
    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': flags}
    first = 'lhu $a0, 0x24($t4)'
    again = ['lhu $v0, 0x24($t5)', 'lhu $a0, 0x24($t5)']
    rows = [(' ', first, first.replace('$t4', '$t5')), ('r', 'srl $a0, $a1, 3', 'srl $a0, $a0, 3'),
            ('>', '', again[0]), ('>', '', again[1])]
    assert kmc_cse_skip_probe(own, rows) == ['-O2']
    # a different field, a store, or a load the first read never made is not a reload
    assert kmc_cse_skip_probe(own, rows[:2] + [('>', '', 'lhu $v0, 0x26($t5)')]) is None
    assert kmc_cse_skip_probe(own, rows[:2] + [('>', '', 'sh $v0, 0x24($t5)')]) is None
    assert kmc_cse_skip_probe(own, [('>', '', again[0]), ('>', '', again[1])]) is None
    # only the option this probe removes, only for KMC
    assert kmc_cse_skip_probe(dict(own, flags=['-O2']), rows) is None
    assert kmc_cse_skip_probe(dict(own, toolchain='ido-7.1'), rows) is None


def test_kmc_priorities_offers_the_cse_skip_variant():
    from decompnet.app.profile_hypotheses import kmc_priorities

    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2', '-fno-cse-skip-blocks']}
    load = 'lhu $a0, 0x24($t4)'
    rows = [(' ', load, load), ('>', '', load), ('>', '', load)]
    assert ('-O2',) in kmc_priorities(own, '', rows)


def test_kmc_cse_hoist_probe_turns_both_block_options_off_for_a_hoisted_address():
    from decompnet.app.profile_hypotheses import kmc_cse_hoist_probe

    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    rows = [('r', 'lui $a0, %hi(D_800DE058)', 'lui $s2, %hi(D_800DE058)'),
            ('>', '', 'addiu $s2, $s2, %lo(D_800DE058)'),
            ('|', 'addiu $a0, $a0, %lo(D_800DE058)', 'move $a0, $s2'),
            ('<', 'lui $a0, %hi(D_800DE058)', ''),
            ('|', 'addiu $a0, $a0, %lo(D_800DE058)', 'move $a0, $s2')]
    assert kmc_cse_hoist_probe(own, rows) == ['-O2', '-fno-cse-follow-jumps', '-fno-cse-skip-blocks']
    # one copy, a target that builds it once too, or another compiler is no evidence
    assert kmc_cse_hoist_probe(own, rows[:3]) is None
    assert kmc_cse_hoist_probe(own, [rows[1], rows[2], ('|', 'move $a0, $v1', 'move $a0, $s2')]) is None  # built once
    assert kmc_cse_hoist_probe({'toolchain': 'ido-7.1', 'flags': ['-O2']}, rows) is None
    assert kmc_cse_hoist_probe({'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2', '-fno-cse-skip-blocks']}, rows) is None
