"""The profile probe (app/profile_probe.py): which per-function compiler profiles an open function's target points to,
and the bounded, durable judging of them. Rules first (no compiler), then the runner, then the N64 demo game."""
import json
import struct
from collections import Counter
from types import SimpleNamespace

import pytest

from decompnet import consoles
from decompnet.app import deep_permuter, profile_probe
from decompnet.arch import mips_profile_evidence as evidence
from decompnet.consoles.n64 import profile_probes as n64_probes

SN = {'toolchain': 'sn-n64-281', 'flags': '-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64'.split()}
JR, NOP = 0x03E00008, 0
ADDIU_SP = 0x27BD0018
LUI_AT = 0x3C010000 | 0x8001
LWC1_AT = (0x31 << 26) | (1 << 21) | (2 << 16)  # lwc1 $f2, 0($at)
LDC1 = (0x35 << 26) | (4 << 21) | (2 << 16)
N64 = consoles.get('n64')


def facts(own=SN, **kwargs):
    return profile_probe.Facts(own=own, **kwargs)


def keys(plan):
    return [(p['toolchain'], ' '.join(p['flags']), p['family']) for p in plan]


# --- the target's bytes -------------------------------------------------------------------------------------------

def test_an_epilogue_with_a_fillable_slot_is_what_sn_2_8_1_never_leaves():
    assert evidence.ends_jr_ra_nop([0x8FBF0014, ADDIU_SP, JR, NOP]) is True
    assert evidence.ends_jr_ra_nop([0x8FBF0014, ADDIU_SP, JR, ADDIU_SP]) is False  # slot filled
    assert evidence.ends_jr_ra_nop([JR, NOP]) is False  # nothing a scheduler could move
    assert evidence.ends_jr_ra_nop([1, 2, 0x0C000000, JR, NOP]) is False  # the call's own slot is not a candidate


def test_a_float_pool_read_through_at_and_an_ldc1_are_found_in_the_words():
    assert evidence.float_pool_through_at([LUI_AT, LWC1_AT]) is True
    assert evidence.float_pool_through_at([0x3C020000 | 0x8001, LWC1_AT]) is False  # not $at
    assert evidence.uses_ldc1([1, LDC1]) and not evidence.uses_ldc1([1, 2])
    assert evidence.words_of(struct.pack('>2I', 1, 2)) == [1, 2] and evidence.words_of(b'') == []


# --- the decision -----------------------------------------------------------------------------------------------------

def test_nothing_in_the_target_means_no_probe_and_no_compile():
    assert profile_probe.plan(N64, facts(words=[0x8FBF0014, ADDIU_SP, JR, ADDIU_SP])) == []


def test_fp_rows_only_point_to_the_other_register_file():
    rows = [('>', 'lwc1 $f2, 0x0($a0)', 'lwc1 $f1, 0x0($a0)'), (' ', 'jr $ra', 'jr $ra')]
    plan = profile_probe.plan(N64, facts(words=[1, 2, 3, 4], rows=rows))
    assert keys(plan)[0][2:] == ('fp',) and '-mfp32' in plan[0]['flags'] and '-mfp64' not in plan[0]['flags']


def test_a_float_pool_through_at_with_fp_evidence_adds_the_pair_with_g8():
    rows = [('>', 'lwc1 $f2, 0x0($a0)', 'lwc1 $f1, 0x0($a0)')]
    plan = profile_probe.plan(N64, facts(words=[LUI_AT, LWC1_AT, 1, 2], rows=rows))
    pair = next(p for p in plan if p['family'] == 'small_data_fp')
    assert '-G8' in pair['flags'] and '-mfp32' in pair['flags'] and '-G0' not in pair['flags']
    assert [p['family'] for p in plan].index('fp') < [p['family'] for p in plan].index('small_data_fp')


def test_the_pair_needs_the_float_pool_evidence():
    rows = [('>', 'lwc1 $f2, 0x0($a0)', 'lwc1 $f1, 0x0($a0)')]
    assert all(p['family'] != 'small_data_fp' for p in profile_probe.plan(N64, facts(words=[1, 2, 3, 4], rows=rows)))


def test_a_fillable_nop_epilogue_points_to_sn_2_7_2_builds_in_both_flag_sets():
    plan = profile_probe.plan(N64, facts(words=[0x8FBF0014, ADDIU_SP, JR, NOP]))
    assert keys(plan) == [(t, ' '.join(f), 'sn272') for t in ('sn-n64-272-0001', 'sn-n64-272-0004', 'sn-n64-272-0006')
                          for f in n64_probes.SN_272_FLAGS]


PACK = 'sn-n64-272-0001-as0004'
PACK_FLAGS = [' '.join(f) for f in n64_probes.AS0004_FLAGS]


def pack_keys(plan):
    return [(p['toolchain'], ' '.join(p['flags'])) for p in plan if p['toolchain'] == PACK]


def test_an_ldc1_target_tries_the_0004_assembler_pack_first():
    plan = profile_probe.plan(N64, facts(words=[LDC1, ADDIU_SP, JR, NOP]))
    assert [(p['toolchain'], ' '.join(p['flags'])) for p in plan][:3] == [(PACK, f) for f in PACK_FLAGS]
    assert PACK_FLAGS[0] == '-G0 -mips3 -O2 -mgas -meb -mhard-float'


def test_the_pack_leads_the_flag_variants_of_the_functions_own_compiler():
    rows = [('>', 'lwc1 $f2, 0x0($a0)', 'lwc1 $f1, 0x0($a0)')]  # FP register rows: `-mfp32` would be planned
    plan = profile_probe.plan(N64, facts(words=[LDC1, ADDIU_SP, JR, ADDIU_SP], rows=rows))
    assert [p['toolchain'] for p in plan][:3] == [PACK] * 3
    assert 'fp' in {p['family'] for p in plan[3:]}  # the flag variants still follow


def test_the_pack_is_offered_for_an_ldc1_target_whatever_the_profile_or_epilogue():
    filled = [LDC1, 0x8FBF0014, ADDIU_SP, JR, ADDIU_SP]  # the delay slot is filled: no `jr ra; nop` tell
    on_281 = profile_probe.plan(N64, facts(words=filled))
    assert [k for _, k in pack_keys(on_281)] == PACK_FLAGS
    for own in ({'toolchain': 'sn-n64-272-0004', 'flags': ['-G0', '-mips3', '-O2']},
                {'toolchain': 'sn-n64-272-0001', 'flags': ['-G8', '-mips2', '-O2']},
                {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}):
        plan = profile_probe.plan(N64, facts(own=own, words=filled))
        assert [k for _, k in pack_keys(plan)] == PACK_FLAGS, own
    # the nop epilogue on 2.8.1 still adds its own 2.7.2 probes, and the pack is not planned twice
    both = profile_probe.plan(N64, facts(words=[LDC1, ADDIU_SP, JR, NOP]))
    assert len(pack_keys(both)) == len(set(pack_keys(both))) == 3
    assert any(p['family'] == 'sn272' for p in both)


def test_the_pack_on_its_own_profile_is_not_judged_under_those_same_settings_again():
    own = {'toolchain': PACK, 'flags': PACK_FLAGS[1].split()}
    plan = profile_probe.plan(N64, facts(own=own, words=[LDC1, JR, NOP]))
    assert [k for _, k in pack_keys(plan)] == [PACK_FLAGS[0], PACK_FLAGS[2]]


def test_no_ldc1_means_the_pack_is_not_offered():
    assert pack_keys(profile_probe.plan(N64, facts(words=[0x8FBF0014, ADDIU_SP, JR, NOP]))) == []
    assert pack_keys(profile_probe.plan(N64, facts(words=[0x8FBF0014, ADDIU_SP, JR, ADDIU_SP]))) == []


def test_a_pack_that_is_not_installed_is_not_offered():
    plan = profile_probe.plan(N64, facts(words=[LDC1, ADDIU_SP, JR, NOP], ready=lambda t: t != PACK))
    assert pack_keys(plan) == [] and plan  # the 2.7.2 builds that are installed are still planned


def test_only_installed_toolchains_are_planned():
    plan = profile_probe.plan(N64, facts(words=[0x8FBF0014, ADDIU_SP, JR, NOP], ready=lambda t: t == 'sn-n64-272-0004'))
    assert {p['toolchain'] for p in plan} == {'sn-n64-272-0004'}


def test_272_builds_are_for_an_sn_2_8_1_function_only():
    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    assert profile_probe.plan(N64, facts(own=own, words=[0x8FBF0014, ADDIU_SP, JR, NOP])) == []


def test_library_code_points_to_kmc_with_its_own_flags():
    plan = profile_probe.plan(N64, facts(words=[1, 2, 3, 4], sdk=True))
    assert keys(plan) == [('gcc-2.7.2-kmc', '-G0 -mips3 -O2 -mgp32 -mfp32', 'kmc_library')]


def test_a_function_already_built_with_kmc_has_no_kmc_probe():
    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    assert profile_probe.plan(N64, facts(own=own, sdk=True)) == []


@pytest.mark.parametrize('name,found', [('osSetIntMask', True), ('__osViInit', True), ('__rmonSendReply', True),
                                        ('__scHandleRetrace', True), ('osprint', False), ('func_80001234', False)])
def test_libultra_names(name, found):
    assert bool(profile_probe.LIBRARY_NAME.match(name)) is found


def test_the_profile_most_matched_neighbours_use_comes_first():
    other = ('sn-n64-272-0001', ('-G0', '-mips3', '-O2', '-mgas', '-meb', '-mhard-float'))
    mine = (SN['toolchain'], tuple(SN['flags']))
    plan = profile_probe.plan(N64, facts(words=[0x8FBF0014, ADDIU_SP, JR, NOP], votes=Counter({other: 8, mine: 2}), read=10))
    assert keys(plan)[0] == ('sn-n64-272-0001', '-G0 -mips3 -O2 -mgas -meb -mhard-float', 'neighbours')
    assert len(plan) == len(set(keys(plan))) and keys(plan).count(keys(plan)[0]) == 1  # no profile twice


def test_a_weak_neighbour_vote_is_not_evidence():
    other = ('sn-n64-272-0001', ('-O2',))
    assert profile_probe.plan(N64, facts(votes=Counter({other: 2}), read=2)) == []
    assert profile_probe.plan(N64, facts(votes=Counter({other: 3}), read=9)) == []


def test_the_plan_is_bounded():
    assert len(profile_probe.plan(N64, facts(words=[LDC1, ADDIU_SP, JR, NOP], sdk=True))) <= profile_probe.MAX_PROBES


def test_another_console_without_hints_still_gets_the_flag_rules_and_no_more():
    ps2 = consoles.get('ps2')
    own = {'toolchain': 'ee-gcc-2.95.3', 'flags': ['-O2', '-G0']}
    assert profile_probe.plan(ps2, facts(own=own, words=[0x8FBF0014, ADDIU_SP, JR, NOP], sdk=True)) == []


# --- running the plan -------------------------------------------------------------------------------------------------

class Log(list):
    def __call__(self, line):
        self.append(line)


class FakeSearch:
    def __init__(self, limit=20):
        self.log, self.limit, self.tested, self.phase = Log(), limit, 0, 'x'
        self.net = SimpleNamespace(project_store=lambda pid: FakeStore(), add_note=self._note)
        self.notes, self.stored = [], []
        FakeStore.rows = self.stored

    def check(self):
        pass

    def _note(self, agent, slug, text, name, **kwargs):
        self.notes.append((text, kwargs))


class FakeStore:
    rows = []

    def tx(self):
        rows = self.rows

        class Db:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params=()):
                rows.append(params)
        return Db()

    def one(self, *a):
        return None


FN = {'id': 1, 'name': 'f', 'status': 'open', 'best_score': 380, 'unit': 'u'}
PROJ = {'slug': 'p', 'id': 1}


def arrange(monkeypatch, scores, plan=None, limit=20):
    seen = []
    items = plan or [{'toolchain': 't', 'flags': ['-a'], 'family': 'fp', 'why': 'w'},
                     {'toolchain': 't', 'flags': ['-b'], 'family': 'sn272', 'why': 'w'}]
    search = FakeSearch(limit)
    monkeypatch.setattr(profile_probe, 'supported', lambda net, proj: True)
    monkeypatch.setattr(profile_probe, 'gather', lambda *a: facts(own={'toolchain': 'o', 'flags': ['-o']}))
    monkeypatch.setattr(profile_probe, 'plan', lambda console, f: items)
    monkeypatch.setattr(profile_probe, 'finished', lambda *a: None)
    monkeypatch.setattr(profile_probe, 'marker_key', lambda *a: 'marker')
    monkeypatch.setattr(consoles, 'of', lambda cfg: N64)
    search.net.cfg = lambda proj: {}
    from decompnet.app import deep_profiles, project_activity
    monkeypatch.setattr(project_activity, 'active', lambda net, slug: True)

    def probe(search_, agent, proj, fn, source, profile, **kw):
        seen.append((profile['flags'][0], kw))
        search_.tested += 1
        return scores[profile['flags'][0]]
    monkeypatch.setattr(deep_profiles, 'probe', probe)
    return search, seen


def test_an_exact_probe_stops_the_pass_and_is_applied_for_the_function_only(monkeypatch):
    search, seen = arrange(monkeypatch, {'-a': 0, '-b': 5})
    assert profile_probe.run(search, None, PROJ, FN, 'C') is True
    assert [name for name, _ in seen] == ['-a'] and seen[0][1]['review_variants'] is False
    assert search.phase == 'x' and not search.notes


def test_a_non_exact_best_is_a_note_and_a_durable_record_and_changes_nothing(monkeypatch):
    search, seen = arrange(monkeypatch, {'-a': 20, '-b': None})
    assert profile_probe.run(search, None, PROJ, FN, 'C') is False
    assert len(search.notes) == 1 and 'scores 20' in search.notes[0][0] and '380' in search.notes[0][0]
    assert search.notes[0][1] == {'score_before': 380, 'score_after': 20}
    record = json.loads(search.stored[-1][4])
    assert search.stored[-1][2] == 'probed' and record['complete'] and record['best']['score'] == 20


@pytest.mark.parametrize('score', [400, 350])  # no closer, or closer by less than a tenth: logged, never a note
def test_a_profile_that_is_no_closer_says_nothing(monkeypatch, score):
    search, _ = arrange(monkeypatch, {'-a': score, '-b': None})
    assert profile_probe.run(search, None, PROJ, FN, 'C') is False
    assert not search.notes and search.stored  # recorded as finished, silently


def test_a_spent_allowance_stops_the_pass_and_leaves_it_unfinished(monkeypatch):
    search, seen = arrange(monkeypatch, {'-a': 20, '-b': 10}, limit=profile_probe.RESERVE)
    assert profile_probe.run(search, None, PROJ, FN, 'C') is False
    assert [n for n, _ in seen] == ['-a'] and not search.notes
    assert not search.stored  # a pass cut short is not finished: the next one resumes with the cached profile
    assert any('allowance is spent' in line for line in search.log)


def test_a_stop_in_the_middle_is_not_recorded_as_finished(monkeypatch):
    search, _ = arrange(monkeypatch, {'-a': 20, '-b': 10})

    def stop():
        raise deep_permuter.SearchLimit('Stopped')
    search.check = stop
    with pytest.raises(deep_permuter.SearchLimit):
        profile_probe.run(search, None, PROJ, FN, 'C')
    assert not search.stored and search.phase == 'x'


def test_a_finished_pass_is_not_planned_again(monkeypatch):
    search, seen = arrange(monkeypatch, {'-a': 20, '-b': 10})
    monkeypatch.setattr(profile_probe, 'finished', lambda *a: {'complete': True})
    assert profile_probe.run(search, None, PROJ, FN, 'C') is False and not seen


def test_an_idle_project_is_left_alone(monkeypatch):
    from decompnet.app import project_activity
    search, seen = arrange(monkeypatch, {'-a': 0, '-b': 0})
    monkeypatch.setattr(project_activity, 'active', lambda net, slug: False)
    assert profile_probe.run(search, None, PROJ, FN, 'C') is False and not seen


def test_a_planning_fault_never_stops_the_search(monkeypatch):
    search, seen = arrange(monkeypatch, {})
    monkeypatch.setattr(profile_probe, 'plan', lambda *a: 1 / 0)
    assert profile_probe.run(search, None, PROJ, FN, 'C') is False and not seen


def test_the_widened_externs_are_tried_under_a_small_data_profile(monkeypatch):
    from decompnet.app import small_data_fingerprints
    plan = [{'toolchain': 't', 'flags': ['-G8'], 'family': 'small_data', 'why': 'w'}]
    search, seen = arrange(monkeypatch, {'-G8': 50}, plan)
    monkeypatch.setattr(small_data_fingerprints, 'widened', lambda *a: ['C with arrays'])
    assert profile_probe.run(search, None, PROJ, FN, 'C') is False
    assert len(seen) == 2  # the saved C, then its widened form under the same profile


# --- the app is wired to it -------------------------------------------------------------------------------------------

def test_the_deep_permuter_and_the_autopilots_saved_source_pass_call_it():
    import inspect

    from decompnet.app.engine import autopilot
    assert 'profile_probe.run(search, agent, proj' in inspect.getsource(deep_permuter._run)
    assert 'profile_probe_autopilot' in inspect.getsource(autopilot.AutopilotMixin._autopilot_one_work)


def test_consoles_without_hints_have_the_connector_default():
    assert consoles.get('ps1').profile_probes(facts()) == [] and consoles.get('n64').profile_probes(facts()) == []


# --- on the N64 demo game, with the real compiler ---------------------------------------------------------------------

@pytest.fixture(scope='module')
def demo(tmp_path_factory):
    import subprocess
    import sys

    from decompnet import toolchains
    from decompnet.app.engine import RomNetwork
    from tests.test_consoles import ROOT
    for tool in N64.demo_tools:
        try:
            ok = toolchains.get(tool).supported and toolchains.installed(tool) is not None
        except toolchains.ToolchainError:
            import shutil
            ok = shutil.which(tool) is not None
        if not ok:
            pytest.skip(f'the N64 demo needs {tool}')
    folder = ROOT / 'projects' / N64.demo
    key = folder / 'build' / 'demo.json'
    if not key.exists():
        subprocess.run([sys.executable, str(folder / 'build.py')], check=True)
    built = json.loads(key.read_text())
    work = tmp_path_factory.mktemp('probe')
    net = RomNetwork(work / 'app.db', work)
    slug = net.import_rom(folder / 'build' / built['game'], confirmed=True, log=lambda *_: None)
    return net, slug, built['symbols']


def test_the_probe_finds_the_profile_a_function_was_built_with_and_applies_it_to_that_function_only(demo, monkeypatch):
    """The function's own setting is wrong (-O0 here); the plan names the project's real profile: judged through the
    permuter's own call, rechecked, and applied by `suggest` for this function alone."""
    from decompnet.app import project_activity
    from tests.test_consoles import _function
    net, slug, symbols = demo
    name, source = _function(net, slug, symbols, 'checksum_save')
    proj = net.project_row(slug)
    default = net.cfg(proj)['compiler']
    wrong = {'toolchain': default['toolchain'], 'flags': [f if f != '-O2' else '-O0' for f in default['flags']]}
    fn = net._function(name, slug)
    net._update_cfg(slug, lambda cfg: cfg.setdefault('compiler_ranges', []).append(
        {'start': fn['vram'], 'end': fn['vram'] + fn['size'], **wrong}))
    agent, _ = net.local_agent('probe-agent')
    net.claim(agent, slug, name)
    attempt = net.attempt(agent, name, source, slug)
    net.release(agent, name, '', slug)
    assert not attempt['matched'], 'the wrong profile must not match'
    proj, fn = net.project_row(slug), net._function(name, slug)
    neighbours = [{'toolchain': default['toolchain'], 'flags': list(default['flags']), 'family': 'test', 'why': 'the project profile'}]
    monkeypatch.setattr(profile_probe, 'plan', lambda console, f: neighbours)
    monkeypatch.setattr(project_activity, 'active', lambda n, s: True)
    log = Log()
    log.check_cancel = lambda: None
    log.progress = lambda value: None
    search = deep_permuter.Search(net, 1, 12, log, fn)
    assert not net.cfg(proj).get('suggestions')
    probe_agent, _ = net.local_agent('probe-runner')
    assert profile_probe.run(search, probe_agent, proj, fn, fn['best_source']) is True, list(log)
    fresh = net._function(name, slug)
    assert fresh['status'] == 'matched' and fresh['best_score'] == 0
    cfg = net.cfg(net.project_row(slug))
    assert cfg['compiler'] == default  # the project's setting and every other function are unchanged
    assert len(cfg.get('compiler_ranges', [])) == 1  # (the one this test set: the probe added no range)


def test_a_probe_that_is_not_exact_applies_nothing_and_is_not_judged_twice(demo, monkeypatch):
    from decompnet.app import project_activity
    from tests.test_consoles import _function
    net, slug, symbols = demo
    name, source = _function(net, slug, symbols, 'abs_s32')
    agent, _ = net.local_agent('probe-agent-2')
    net.claim(agent, slug, name)
    net.attempt(agent, name, source.replace('-v : v', '-v : v + 1'), slug)
    net.release(agent, name, '', slug)
    proj, fn = net.project_row(slug), net._function(name, slug)
    default = net.cfg(proj)['compiler']
    other = {'toolchain': default['toolchain'], 'flags': [f if f != '-O2' else '-O1' for f in default['flags']],
             'family': 'test', 'why': 'a guess'}
    monkeypatch.setattr(profile_probe, 'plan', lambda console, f: [other])
    monkeypatch.setattr(project_activity, 'active', lambda n, s: True)
    log = Log()
    log.check_cancel = lambda: None
    log.progress = lambda value: None
    search = deep_permuter.Search(net, 1, 12, log, fn)
    probe_agent, _ = net.local_agent('probe-runner-2')
    assert profile_probe.run(search, probe_agent, proj, fn, fn['best_source']) is False
    first = search.tested
    assert first >= 1 and net._function(name, slug)['status'] == 'open'
    assert profile_probe.finished(net, proj, fn, fn['best_source'], {'toolchain': default['toolchain'], 'flags': default['flags']}) is not None
    assert profile_probe.run(search, probe_agent, proj, fn, fn['best_source']) is False
    assert search.tested == first  # recorded: not compiled again


def test_the_autopilot_pass_skips_a_function_without_saved_c_and_lets_its_stop_through(monkeypatch):
    from decompnet.app import profile_probe_autopilot as lane
    net = SimpleNamespace(autopilots={'p': {'stop': SimpleNamespace(is_set=lambda: True)}})
    monkeypatch.setattr(profile_probe, 'supported', lambda n, p: True)
    assert lane.run(net, None, PROJ, {'best_source': None}) is False

    class Stopped(deep_permuter.Search):
        def __init__(self, *a, **k):
            pass
    monkeypatch.setattr(deep_permuter, 'Search', Stopped)

    def stop(*a):
        raise deep_permuter.SearchLimit('Stopped')
    monkeypatch.setattr(profile_probe, 'run', stop)
    with pytest.raises(deep_permuter.SearchLimit):
        lane.run(net, None, PROJ, {'best_source': 'C'})
    net.autopilots['p']['stop'] = SimpleNamespace(is_set=lambda: False)
    assert lane.run(net, None, PROJ, {'best_source': 'C'}) is False  # (a limit that is not a Stop just ends this function's pass)


def test_passes_recorded_before_scores_were_kept_are_redone(monkeypatch):
    # v1 records from the Deep Permuter's batch have probes without scores (the sweep's queued misses lost them):
    # a pass is looked up by the current signature only, so each is redone once
    from decompnet.app import agent_tokens
    monkeypatch.setattr(agent_tokens, 'target_scope', lambda *a: 'scope')
    key = profile_probe.marker_key(None, {'id': 1}, 'int f(void) { return 0; }', SN)
    assert key.startswith(profile_probe.SIGNATURE) and not key.startswith('profile-probe-v1:')


def test_the_machine_draft_is_judged_under_each_planned_profile_too(monkeypatch):
    # one KMC game's sweep: 26 of the 64 open functions that score at most half as much under -O1 did so through their
    # machine draft, which the probe never judged
    search, seen = arrange(monkeypatch, {'-a': 300, '-b': None})
    sources = []
    from decompnet.app import deep_profiles
    probe = deep_profiles.probe

    def judged(search_, agent, proj, fn, source, profile, **kw):
        sources.append(source)
        return 40 if source == 'DRAFT' else probe(search_, agent, proj, fn, source, profile, **kw)
    monkeypatch.setattr(deep_profiles, 'probe', judged)
    assert profile_probe.run(search, None, PROJ, {**FN, 'seed': 'DRAFT'}, 'C') is False
    assert sources == ['C', 'DRAFT', 'C', 'DRAFT']
    assert len(search.notes) == 1 and 'machine draft' in search.notes[0][0] and 'scores 40' in search.notes[0][0]
    assert 'saved C scores 380' in search.notes[0][0]
    assert json.loads(search.stored[-1][4])['best'] == {'toolchain': 't', 'flags': ['-a'], 'family': 'fp', 'score': 40,
                                                         'draft': True}


def test_a_draft_that_is_the_saved_c_is_not_judged_twice(monkeypatch):
    search, seen = arrange(monkeypatch, {'-a': 300, '-b': None})
    assert profile_probe.run(search, None, PROJ, {**FN, 'seed': 'C\n'}, 'C') is False
    assert [n for n, _ in seen] == ['-a', '-b']
