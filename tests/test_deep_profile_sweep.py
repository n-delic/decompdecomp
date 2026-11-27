"""The deep permuter's first move: the saved C under the project's other compiler profiles."""
from types import SimpleNamespace

from decompnet.app import deep_profile_sweep as sweep


def _net(usage, cfg=None):
    cfg = cfg or {'compiler': {'toolchain': 'a', 'flags': ['-O2']}}
    return SimpleNamespace(cfg=lambda p: cfg, compiler_profile_usage=lambda p: usage)


def _patch(monkeypatch, families, compilers=()):
    monkeypatch.setattr(sweep, '_ready', lambda tid: True)
    monkeypatch.setattr(sweep, '_family', lambda tid: families.get(tid, tid))
    options = [SimpleNamespace(id=c, automatic=True) for c in compilers]
    monkeypatch.setattr(sweep.consoles, 'of', lambda cfg: SimpleNamespace(compilers=options, compilers_for=lambda cfg: options))
    monkeypatch.setattr(sweep.consoles, 'default_flags', lambda cfg, tid: ['-O1', tid])


FN = {'vram': 0x100, 'rom_offset': 1, 'name': 'f'}


def test_every_project_profile_but_the_functions_own_is_tried(monkeypatch):
    _patch(monkeypatch, {})
    usage = [{'toolchain': 'a', 'flags': ['-O2'], 'matched': 3}, {'toolchain': 'b', 'flags': ['-O1'], 'matched': 9},
             {'toolchain': 'a', 'flags': ['-O3'], 'matched': 1}]
    assert sweep.profiles(_net(usage), {}, FN) == [{'toolchain': 'b', 'flags': ['-O1']}, {'toolchain': 'a', 'flags': ['-O3']}]


def test_a_single_profile_project_tries_one_profile_per_other_family(monkeypatch):
    _patch(monkeypatch, {'a': 'gcc', 'a2': 'gcc', 'b': 'ido', 'b2': 'ido', 'c': 'mwcc'}, ['a2', 'b', 'b2', 'c'])
    usage = [{'toolchain': 'a', 'flags': ['-O2'], 'matched': 3}]
    assert [p['toolchain'] for p in sweep.profiles(_net(usage), {}, FN)] == ['b', 'c']


def test_probes_stop_at_the_first_exact_match(monkeypatch):
    _patch(monkeypatch, {})
    seen = []
    monkeypatch.setattr(sweep.deep_profiles, 'probe', lambda s, a, p, f, src, profile: seen.append(profile['toolchain']) or (0 if profile['toolchain'] == 'b' else None))
    usage = [{'toolchain': 'a', 'flags': ['-O2'], 'matched': 3}, {'toolchain': 'b', 'flags': [], 'matched': 2},
             {'toolchain': 'c', 'flags': [], 'matched': 1}]
    logs = []
    search = SimpleNamespace(net=_net(usage), phase='x', log=logs.append, check=lambda: None, limit=10, tested=0)
    assert sweep.run(search, None, {}, FN, 'int f(){}') is True
    assert seen == ['b'] and search.phase == 'x'


def test_a_bounded_run_leaves_its_reserve_for_the_targeted_repairs(monkeypatch):
    """A handoff's 6 compiles went wholly to other compilers, so a saved best of 15 never reached its one-step repair."""
    _patch(monkeypatch, {})
    seen = []
    def probe(s, a, p, f, src, profile):
        seen.append(profile['toolchain'])
        s.tested += 1
    monkeypatch.setattr(sweep.deep_profiles, 'probe', probe)
    usage = [{'toolchain': t, 'flags': [], 'matched': 1} for t in 'abcdefg']
    search = SimpleNamespace(net=_net(usage), phase='x', log=lambda text: None, check=lambda: None, limit=6, tested=0,
                             local_reserve=3)
    sweep.run(search, None, {}, FN, 'int f(){}')
    assert len(seen) == 3


def test_a_bounded_search_reserves_half_its_allowance_and_a_full_one_none():
    from decompnet.app import deep_permuter
    assert deep_permuter.Search(SimpleNamespace(), 1, 6, lambda text: None).local_reserve == 3
    assert deep_permuter.Search(SimpleNamespace(), 5, 750, lambda text: None).local_reserve == 0
