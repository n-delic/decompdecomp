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
