"""Ranked profile selection, profile-scoped caching and guarded exact promotion."""
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_profiles
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT


def test_default_first_then_matched_usage_unique_and_stable():
    net = SimpleNamespace(cfg=lambda p:{'compiler':{'toolchain':'default','flags':['-O2']}},
        compiler_profile_usage=lambda p:[
            {'toolchain':'default','flags':['-O2'],'matched':10},
            {'toolchain':'b','flags':['-O2'],'matched':50},
            {'toolchain':'a','flags':['-O1'],'matched':2},
            {'toolchain':'unused','flags':[],'matched':0},
            {'toolchain':'b','flags':['-O2'],'matched':50}])
    assert deep_profiles.selected(net, {}, 1) == [{'toolchain':'default','flags':['-O2']}]
    assert [p['toolchain'] for p in deep_profiles.selected(net, {}, 16)] == ['default','b','a']
    assert [p['toolchain'] for p in deep_profiles.selected(net, {}, 2)] == ['default','b']


@pytest.mark.parametrize('value', [True, 0, -1, 17, '2', 2.5])
def test_invalid_counts(value):
    with pytest.raises(NetworkError):deep_profiles.validate(value)


def test_probe_cache_is_profile_specific_and_near_miss_does_not_replace_best(monkeypatch):
    net = setup(monkeypatch)
    calls = []
    net._judge_with = lambda *args:(calls.append(args[2:4]) or (False, SimpleNamespace(score=1), ''))
    fn = net.function(); proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    profile = {'toolchain':'one','flags':['-O2']}
    original = (fn['best_score'], fn['best_source'])
    assert deep_profiles.probe(search, AGENT, proj, fn, 'candidate', profile) is None
    assert deep_profiles.probe(search, AGENT, proj, fn, 'candidate', profile) is None
    deep_profiles.probe(search, AGENT, proj, fn, 'candidate', {**profile,'flags':['-O1']})
    assert len(calls) == 2 and search.tested == 2 and search.skipped == 1
    current = net.function()
    assert (current['best_score'], current['best_source']) == original


def test_exact_uses_guarded_suggestion_and_reserves_verification_budget(monkeypatch):
    net = setup(monkeypatch)
    net._judge_with = lambda *args:(True, SimpleNamespace(score=0), '')
    suggestions = []
    def suggest(*args, **kwargs):
        suggestions.append(kwargs)
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched'")
    net.suggest = suggest
    fn = net.function(); proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 3, Log(), fn)
    profile = {'toolchain':'one','flags':['-O2']}
    assert deep_profiles.probe(search, AGENT, proj, fn, 'exact C', profile) == 0
    assert search.tested == 3
    assert suggestions[0]['source'] == 'exact C' and suggestions[0]['compiler'] == 'one'


def test_insufficient_budget_retains_exact_evidence_without_applying(monkeypatch):
    net = setup(monkeypatch)
    net._judge_with = lambda *args:(True, SimpleNamespace(score=0), '')
    net.suggest = lambda *args, **kwargs:pytest.fail('no verification budget')
    fn = net.function(); proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 1, Log(), fn)
    assert deep_profiles.probe(search, AGENT, proj, fn, 'exact C', {'toolchain':'one','flags':['-O2']}) is None
    row = net.store.one("SELECT feedback FROM reuse_trials WHERE signature LIKE 'deep-profile-v1:%'")
    assert 'exact C' in row['feedback']


def test_each_selected_profile_visited_in_order_and_stops_at_exact(monkeypatch):
    net = setup(monkeypatch); fn = net.function(); proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    search.compiler_profiles = [{'toolchain':'other','flags':[]}, {'toolchain':'third','flags':[]}]
    calls = []
    def probe(search, agent, proj, fn, source, profile):
        calls.append(profile['toolchain']);return 0 if profile['toolchain']=='third' else None
    monkeypatch.setattr(deep_profiles, 'probe', probe)
    assert search._score(AGENT, proj, fn, 'candidate', 40, None) == 0
    assert calls == ['other','third']


def test_extra_profiles_cannot_overrun_compile_limit(monkeypatch):
    net = setup(monkeypatch)
    calls = []
    net._judge_with = lambda *args:(calls.append(args[2]) or (False, SimpleNamespace(score=2), ''))
    fn = net.function(); proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 1, Log(), fn)
    search.compiler_profiles = [{'toolchain':'one','flags':[]}, {'toolchain':'two','flags':[]}]
    with pytest.raises(deep_permuter.SearchLimit):
        search._score(AGENT, proj, fn, 'candidate', 40, None)
    assert calls == ['one'] and search.tested == 1
