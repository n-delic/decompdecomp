"""Drafts under other profiles (app/profile_drafts.py): which profiles an open function's drafts and saved C are judged
under, the bounded and durable pass over one function, the drafter running it over every open function, and the N64
demo game with its real compiler."""
import json
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_profiles, drafts, profile_attempts, profile_drafts

SN = {'toolchain': 'sn-n64-281', 'flags': '-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64'.split()}
KMC = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-G0', '-mips3', '-O2']}
LWC1 = lambda reg: (0x31 << 26) | (1 << 21) | (reg << 16)  # noqa: E731  lwc1 $fN, 0($at)
ADD_S = lambda fd, fs, ft: (0x11 << 26) | (16 << 21) | (ft << 16) | (fs << 11) | (fd << 6)  # noqa: E731
EVEN_FLOATS = struct.pack('>4I', LWC1(2), LWC1(4), ADD_S(0, 2, 4), 0x03E00008)  # a target that only uses even floats


@pytest.fixture(autouse=True)
def installed(monkeypatch):
    monkeypatch.setattr(profile_drafts, '_ready', lambda toolchain: True)


def used(*profiles):
    return [{**p, 'matched': n} for p, n in profiles]


def keys(plan):
    return [(p['toolchain'], ' '.join(p['flags'])) for p in plan]


# --- the plan ---------------------------------------------------------------------------------------------------------

def test_the_profiles_matched_functions_use_are_planned_most_used_first_and_never_the_functions_own():
    other = {'toolchain': 'sn-n64-281', 'flags': ['-O1']}
    plan = profile_drafts.plan('mips', KMC, used((KMC, 300), (SN, 40), (other, 2)), b'')
    assert keys(plan) == [('sn-n64-281', ' '.join(SN['flags'])), ('sn-n64-281', '-O1')]


def test_a_target_with_only_even_float_registers_adds_mfp32_to_a_used_mfp64_profile():
    plan = profile_drafts.plan('mips', KMC, used((SN, 40)), EVEN_FLOATS)
    flipped = [f if f != '-mfp64' else '-mfp32' for f in SN['flags']]
    assert keys(plan) == [('sn-n64-281', ' '.join(SN['flags'])), ('sn-n64-281', ' '.join(flipped))]


def test_the_functions_own_mfp64_profile_gets_its_mfp32_variant_too():
    plan = profile_drafts.plan('mips', SN, [], EVEN_FLOATS)
    assert keys(plan) == [('sn-n64-281', ' '.join(f if f != '-mfp64' else '-mfp32' for f in SN['flags']))]


@pytest.mark.parametrize('arch,profile', [('mips', KMC), ('mips', {'toolchain': 'ido-7.1', 'flags': ['-mfp64']}),
                                          ('x86', SN)])
def test_no_float_variant_without_an_mfp_flag_on_mips_gcc(arch, profile):
    assert profile_drafts.plan(arch, profile, [], EVEN_FLOATS) == []


def test_a_target_without_float_code_gets_no_float_variant():
    assert keys(profile_drafts.plan('mips', KMC, used((SN, 40)), struct.pack('>I', 0x03E00008))) == [
        ('sn-n64-281', ' '.join(SN['flags']))]


def test_the_plan_is_bounded():
    many = used(*(({'toolchain': 't', 'flags': [f'-O{n}']}, 10 - n) for n in range(8)))
    assert len(profile_drafts.plan('mips', KMC, many, b'')) == profile_drafts.MAX_USED


def test_a_toolchain_that_is_not_installed_is_not_used(monkeypatch):
    net = SimpleNamespace(compiler_profile_usage=lambda proj: [{**SN, 'functions': 5, 'matched': 3},
                                                              {**KMC, 'functions': 9, 'matched': 0}])
    monkeypatch.setattr(profile_drafts, '_ready', lambda toolchain: toolchain != 'sn-n64-281')
    assert profile_drafts.used_profiles(net, {}) == []


# --- one function's pass ----------------------------------------------------------------------------------------------

class Store:
    def __init__(self):
        self.trials = {}

    def one(self, sql, *args):
        return {'x': 1} if (args[0], args[1]) in self.trials else None

    def tx(self):
        trials = self.trials

        class Db:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params=()):
                trials[params[0], params[1]] = (params[2], json.loads(params[4]))
        return Db()


FN = {'id': 7, 'name': 'func_80001000', 'status': 'open', 'vram': 0x80001000, 'size': 16, 'target': b'\0' * 16,
      'seed': '/* draft */ int f(void) { return 1; }', 'seed_key': 'k', 'best_source': 'int f(void) { return 2; }',
      'best_score': 300, 'n_insns': 4, 'unit': 'u', 'meta': None, 'project_id': 1}
PROJ = {'id': 1, 'slug': 'p', 'arch': 'mips', 'big_endian': 1}


def arrange(monkeypatch, scores, *, hold=True):
    """A function under KMC with SN used by matched functions; `scores[(flags, text)]` is each pair's verdict."""
    store, held, judged, kept = Store(), [], [], []
    net = SimpleNamespace(project_store=lambda pid: store, cfg=lambda proj: {'compiler': KMC},
                          permuter=SimpleNamespace(_hold=lambda agent, fn, **kw: held.append('hold') or hold,
                                                   _let_go=lambda agent, fn: held.append('let go')))
    monkeypatch.setattr(profile_drafts, 'supported', lambda n, p: True)
    monkeypatch.setattr('decompnet.project.compiler_for', lambda cfg, vram, fn=None: KMC)
    monkeypatch.setattr(profile_drafts, 'texts', lambda n, p, fn: [('stored draft', 'D'), ('fresh draft', 'F'),
                                                                   ('saved C', 'S')])

    def probe(search, agent, proj, fn, source, profile, **kw):
        assert kw == {'scored': True, 'review_variants': False}
        search.check()
        search.tested += 1
        judged.append((profile['flags'][-1], source))
        verdict = scores.get((profile['flags'][-1], source), 500)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict
    monkeypatch.setattr(deep_profiles, 'probe', probe)
    monkeypatch.setattr(profile_attempts, 'keep', lambda n, p, fn, tc, flags, score, source: kept.append((score, source)))
    return net, store, held, judged, kept


def test_an_exact_draft_is_applied_through_the_probe_and_the_pass_stops(monkeypatch):
    net, store, held, judged, kept = arrange(monkeypatch, {('-mfp64', 'F'): 0})
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40))) == 'matched'
    assert judged == [('-mfp64', 'D'), ('-mfp64', 'F')] and held == ['hold', 'let go'] and not kept
    assert [outcome for outcome, _ in store.trials.values()] == ['matched']


def test_a_near_miss_is_kept_as_the_closest_attempt_and_the_pass_is_not_repeated(monkeypatch):
    net, store, held, judged, kept = arrange(monkeypatch, {('-mfp64', 'S'): 40, ('-mfp64', 'D'): 90})
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40))) == 'tried'
    assert len(judged) == 3 and kept == [(40, 'S')]
    (outcome, feedback), = store.trials.values()
    assert outcome == 'tried' and feedback['best']['score'] == 40 and feedback['best']['text'] == 'saved C'
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40))) == 'done' and len(judged) == 3


def test_a_new_profile_or_a_new_draft_makes_the_function_worth_another_pass(monkeypatch):
    net, store, held, judged, kept = arrange(monkeypatch, {})
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40))) == 'tried'
    other = {'toolchain': 'sn-n64-281', 'flags': ['-O1']}
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40), (other, 1))) == 'tried'
    assert profile_drafts.run_one(net, None, PROJ, {**FN, 'seed': 'new'}, used((SN, 40), (other, 1))) == 'tried'
    assert len(store.trials) == 3


def test_an_exact_pair_waiting_for_approval_is_recorded_and_not_judged_again(monkeypatch):
    waiting = deep_permuter.SearchLimit('Exact match found with sn-n64-281 ...; it waits for your approval')
    net, store, held, judged, kept = arrange(monkeypatch, {('-mfp64', 'D'): waiting})
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40))) == 'waiting'
    assert [outcome for outcome, _ in store.trials.values()] == ['waiting'] and held[-1] == 'let go'


def test_a_stop_leaves_the_function_unfinished(monkeypatch):
    net, store, held, judged, kept = arrange(monkeypatch, {})
    stop = threading.Event()
    stop.set()
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40)), stop.is_set) == 'stopped'
    assert not store.trials and not judged and held == ['hold', 'let go']


def test_a_function_another_worker_holds_is_left_for_later(monkeypatch):
    net, store, held, judged, kept = arrange(monkeypatch, {}, hold=False)
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40))) == 'busy'
    assert not judged and not store.trials


def test_a_project_mode_build_or_a_room_is_left_alone(monkeypatch):
    net, store, held, judged, kept = arrange(monkeypatch, {})
    monkeypatch.setattr(profile_drafts, 'supported', lambda n, p: False)
    assert profile_drafts.run_one(net, None, PROJ, FN, used((SN, 40))) == 'skip' and not held


def test_the_texts_are_the_stored_draft_a_fresh_draft_and_the_saved_c_each_once(monkeypatch):
    from decompnet.app import match_brief
    monkeypatch.setattr(match_brief, 'draft', lambda net, proj, fn: fn['seed'])
    assert profile_drafts.texts(None, PROJ, FN) == [('stored draft', FN['seed']), ('saved C', FN['best_source'])]
    monkeypatch.setattr(match_brief, 'draft', lambda net, proj, fn: 'int f(void) { return 3; }')
    assert [which for which, _ in profile_drafts.texts(None, PROJ, FN)] == ['stored draft', 'fresh draft', 'saved C']


# --- the drafter runs it ----------------------------------------------------------------------------------------------

def test_the_drafter_runs_every_open_function_once_on_its_own_stoppable_worker_group(monkeypatch):
    from decompnet.app import project_activity, purpose_work
    seen, groups, look = [], [], [('a',)]
    monkeypatch.setattr(project_activity, 'active', lambda net, slug: True)
    monkeypatch.setattr(profile_drafts, 'supported', lambda net, proj: True)
    monkeypatch.setattr(profile_drafts, 'look', lambda net, proj: tuple(look))
    monkeypatch.setattr(profile_drafts, 'used_profiles', lambda net, proj: [])
    monkeypatch.setattr(profile_drafts, 'open_functions', lambda net, proj: list(range(1, 60)))
    busy = {5}

    def run_one(net, agent, proj, fn, used, cancelled):
        groups.extend(row['title'] for row in purpose_work.snapshot())
        seen.append(fn['id'])
        return 'busy' if fn['id'] in busy else 'matched' if fn['id'] == 9 else 'tried'
    monkeypatch.setattr(profile_drafts, 'run_one', run_one)
    store = SimpleNamespace(one=lambda sql, fid: {'id': fid})
    net = SimpleNamespace(project_row=lambda slug: {'id': 1, 'slug': slug}, project_store=lambda pid: store,
                          local_agent=lambda name: (SimpleNamespace(name=name, id=1), ''))
    drafter, st = drafts.Drafter(net), drafts.State()
    last = drafter._other_profiles('p', st, 'mark', None)
    assert sorted(seen) == list(range(1, 60)), 'every open function in one pass, no batch the user clicks through'
    assert 'Drafts under other profiles' in groups and st.profiles_matched == 1 and st.current == ''
    seen.clear()
    busy.clear()
    assert drafter._other_profiles('p', st, 'mark', last) == last and seen == [5], 'only the held one again'
    seen.clear()
    assert drafter._other_profiles('p', st, 'mark', last) == last and seen == [], 'nothing changed: no pass'
    look.append(('b',))  # (a profile got its first match)
    last = drafter._other_profiles('p', st, 'mark', last)
    assert len(seen) == 59
    seen.clear()
    st.profiles_stop.set()
    look.append(('c',))
    assert drafter._other_profiles('p', st, 'mark', last) == last and seen == [], 'its Stop holds'
    drafter.jobs['p'] = st
    assert 'profiles_stop' not in drafter.status('p') and json.dumps(drafter.status('p'))


def test_an_idle_project_is_left_for_when_it_is_worked_on_again(monkeypatch):
    from decompnet.app import project_activity
    monkeypatch.setattr(project_activity, 'active', lambda net, slug: False)
    monkeypatch.setattr(profile_drafts, 'supported', lambda net, proj: True)
    monkeypatch.setattr(profile_drafts, 'look', lambda net, proj: ())
    monkeypatch.setattr(profile_drafts, 'used_profiles', lambda net, proj: [])
    monkeypatch.setattr(profile_drafts, 'open_functions', lambda net, proj: [1, 2])
    monkeypatch.setattr(profile_drafts, 'run_one', lambda *a: pytest.fail('an idle project was judged'))
    net = SimpleNamespace(project_row=lambda slug: {'id': 1, 'slug': slug},
                          local_agent=lambda name: (SimpleNamespace(name=name, id=1), ''))
    assert drafts.Drafter(net)._other_profiles('p', drafts.State(), 'mark', None) is None


# --- on the N64 demo game, with the real compiler ---------------------------------------------------------------------

@pytest.fixture(scope='module')
def demo(tmp_path_factory):
    import shutil
    import subprocess
    import sys

    from decompnet import consoles, toolchains
    from decompnet.app.engine import RomNetwork
    from tests.test_consoles import ROOT
    n64 = consoles.get('n64')
    for tool in n64.demo_tools:
        try:
            ok = toolchains.get(tool).supported and toolchains.installed(tool) is not None
        except toolchains.ToolchainError:
            ok = shutil.which(tool) is not None
        if not ok:
            pytest.skip(f'the N64 demo needs {tool}')
    folder = ROOT / 'projects' / n64.demo
    key = folder / 'build' / 'demo.json'
    if not key.exists():
        subprocess.run([sys.executable, str(folder / 'build.py')], check=True)
    built = json.loads(key.read_text())
    work = tmp_path_factory.mktemp('profile-drafts')
    net = RomNetwork(work / 'app.db', work)
    slug = net.import_rom(folder / 'build' / built['game'], confirmed=True, log=lambda *_: None)
    return net, slug, built['symbols']


def test_an_exact_draft_under_a_profile_other_matches_use_is_applied_to_that_function_only(demo, monkeypatch):
    """A function sits under a wrong per-function setting (-O0 here); another function matched under the project's
    profile. The pass judges the drafts and the saved C under that profile, and `suggest` applies the exact one for
    this function alone: the project's default and its ranges stay as they were."""
    from decompnet.app import project_activity
    from tests.test_consoles import _function
    net, slug, symbols = demo
    monkeypatch.setattr(project_activity, 'active', lambda n, s: True)
    proj = net.project_row(slug)
    default = net.cfg(proj)['compiler']
    agent, _ = net.local_agent('drafts-agent')
    donor, donor_source = _function(net, slug, symbols, 'abs_s32')
    net.claim(agent, slug, donor)
    assert net.attempt(agent, donor, donor_source, slug)['matched']
    name, source = _function(net, slug, symbols, 'checksum_save')
    fn = net._function(name, slug)
    wrong = {'toolchain': default['toolchain'], 'flags': [f if f != '-O2' else '-O0' for f in default['flags']]}
    net._update_cfg(slug, lambda cfg: cfg.setdefault('compiler_ranges', []).append(
        {'start': fn['vram'], 'end': fn['vram'] + fn['size'], **wrong}))
    net.claim(agent, slug, name)
    assert not net.attempt(agent, name, source, slug)['matched'], 'the wrong profile must not match'
    net.release(agent, name, '', slug)
    proj, fn = net.project_row(slug), net._function(name, slug)
    used = profile_drafts.used_profiles(net, proj)
    assert (default['toolchain'], tuple(default['flags'])) in {profile_drafts.key(p) for p in used}
    runner, _ = net.local_agent('autopilot')
    started = time.monotonic()
    assert profile_drafts.run_one(net, runner, proj, fn, used) == 'matched'
    assert time.monotonic() - started < 120
    fresh = net._function(name, slug)
    assert fresh['status'] == 'matched' and fresh['best_score'] == 0
    cfg = net.cfg(net.project_row(slug))
    assert cfg['compiler'] == default and len(cfg.get('compiler_ranges', [])) == 1
