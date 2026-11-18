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


def test_own_settings_first_then_the_default_as_a_probe():
    # An N64 function with its own -fno-schedule-insns: with the default alone, nothing was ever scored.
    cfg = {'compiler':{'toolchain':'default','flags':['-O2']},
           'function_compilers':{'10:2147483904':{'vram':0x80000100,'function':'f','toolchain':'default',
                                                  'flags':['-O2','-fno-schedule-insns']}}}
    net = SimpleNamespace(cfg=lambda p:cfg, compiler_profile_usage=lambda p:[
        {'toolchain':'b','flags':['-O2'],'matched':50}])
    fn = {'name':'f','vram':0x80000100,'rom_offset':10}
    own = {'toolchain':'default','flags':['-O2','-fno-schedule-insns']}
    default = {'toolchain':'default','flags':['-O2']}
    assert deep_profiles.selected(net, {}, 1, fn) == [own, default]
    assert deep_profiles.selected(net, {}, 3, fn) == [own, default, {'toolchain':'b','flags':['-O2']}]
    # a function on the project's settings: as before
    assert deep_profiles.selected(net, {}, 1, {**fn, 'rom_offset':11, 'vram':0x80000200}) == [default]


def test_cached_compile_failure_is_not_compiled_again(monkeypatch):
    net = setup(monkeypatch)
    calls = []
    net._judge_with = lambda *args:(calls.append(1) or (False, None, 'parse error'))
    fn = net.function(); proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    profile = {'toolchain':'one','flags':['-O2']}
    assert deep_profiles.probe(search, AGENT, proj, fn, 'broken', profile) is None
    assert deep_profiles.probe(search, AGENT, proj, fn, 'broken', profile) is None
    assert len(calls) == 1 and search.tested == 1 and search.skipped == 1


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
    # (the rest of the allowance can't reverify it either: the pass ends, saying what it found)
    with pytest.raises(deep_permuter.SearchLimit, match='Exact match found with one -O2; run the pass again'):
        deep_profiles.probe(search, AGENT, proj, fn, 'exact C', {'toolchain':'one','flags':['-O2']})
    row = net.store.one("SELECT feedback FROM reuse_trials WHERE signature LIKE 'deep-profile-v1:%'")
    assert 'exact C' in row['feedback']


def test_exact_pair_waiting_for_approval_ends_the_pass_instead_of_counting_a_failure(monkeypatch):
    # An N64 function: its exact pair became an open suggestion (automatic compiler changes off), the probe said
    # "no score", and the pass ran 10,000 more variants to report "0 improvements".
    net = setup(monkeypatch)
    net._judge_with = lambda *args:(True, SimpleNamespace(score=0), '')
    suggestions = []
    net.suggest = lambda *args, **kwargs:(suggestions.append(kwargs) or
                                          'Suggested function-only compiler bundle; automatic compiler changes are off.')
    fn = net.function(); proj = net.project_row('game')
    log = Log()
    search = deep_permuter.Search(net, 1, 20, log, fn)
    with pytest.raises(deep_permuter.SearchLimit, match='waits for your approval as a compiler suggestion'):
        deep_profiles.probe(search, AGENT, proj, fn, 'exact C', {'toolchain':'one','flags':['-O2']})
    assert len(suggestions) == 1 and net.function()['status'] == 'open'
    assert any('automatic compiler changes are off' in line for line in log.lines)


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


def test_confirmed_alternate_profile_match_updates_progress_once(monkeypatch):
    net=setup(monkeypatch);fn=net.function();proj=net.project_row('game');log=Log()
    search=deep_permuter.Search(net,1,20,log,fn)
    search.compiler_profiles=[{'toolchain':'other','flags':[]}]
    search.winner_diff=([('r','old target','old candidate')],'old profile')
    def probe(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched',best_score=0")
        return 0
    monkeypatch.setattr(deep_profiles,'probe',probe)
    assert search._score(AGENT,proj,fn,'exact source',40,None)==0
    assert search.best==0 and search.improvements==1
    assert search.winner_diff is None
    assert log.updates[-1]['best_percent']==100
    assert log.updates[-1]['last_score']==0 and log.updates[-1]['last_outcome']=='matched'
    assert search._score(AGENT,proj,fn,'exact source',40,None)==0
    assert search.improvements==1


@pytest.mark.parametrize('accepted',[False,True])
def test_room_exact_profile_requires_host_acceptance_and_preserves_packet(monkeypatch,accepted):
    import json

    from decompnet.app import room_matches
    net=setup(monkeypatch);fn=net.function();proj=net.project_row('game')
    net._judge_with=lambda *args:(True,SimpleNamespace(score=0),'')
    sync=[];requests=[]
    def request(method,path,data):
        requests.append(data)
        return {'matched':accepted,'pending':not accepted,'reason':'Host needs compiler installed'}
    link=SimpleNamespace(net=net,slug='game',url='http://host',room='room',_req=request,sync=lambda:sync.append(True))
    monkeypatch.setattr(net,'links',{'game':link})
    monkeypatch.setattr(room_matches,'packet',lambda *args:{'compiler':{'toolchain':'old','flags':[]},'target_sha256':'target','source_sha256':'source','header':'old'})
    search=deep_permuter.Search(net,1,1,Log(),fn)
    profile={'toolchain':'other','flags':['-O2']}
    before=net.project_row('game')['config']
    if accepted:assert deep_profiles.probe(search,AGENT,proj,fn,'exact C',profile)==0
    else:
        with pytest.raises(deep_permuter.SearchLimit,match='awaiting host verification'):
            deep_profiles.probe(search,AGENT,proj,fn,'exact C',profile)
    assert requests[0]['evidence']['compiler']==profile
    assert requests[0]['evidence']['header']==net._context(proj['id'],fn['unit'])['header']
    saved=net.store.one("SELECT value FROM settings WHERE key LIKE 'room-match:%'")
    assert json.loads(saved['value'])['status']==('accepted' if accepted else 'pending')
    assert sync==([True] if accepted else [])
    assert net.function()['status']=='open' and net.project_row('game')['config']==before
    assert search.tested==1  # host checks do not consume local compilation slots


def test_room_search_tests_alternate_profiles_instead_of_skipping_them(monkeypatch):
    net=setup(monkeypatch);fn=net.function();proj=net.project_row('game')
    monkeypatch.setattr(net,'links',{'game':object()})
    search=deep_permuter.Search(net,1,20,Log(),fn)
    search.compiler_profiles=[{'toolchain':'other','flags':[]}]
    seen=[]
    monkeypatch.setattr(deep_profiles,'probe',lambda *args:seen.append(args[-1]) or 0)
    assert search._score(AGENT,proj,fn,'candidate',40,None)==0
    assert seen==search.compiler_profiles


def test_offline_alternate_profile_is_durable_and_not_a_review_error(monkeypatch):
    from decompnet.app import room_matches
    net=setup(monkeypatch);fn=net.function();proj=net.project_row('game')
    net._judge_with=lambda *args:(True,SimpleNamespace(score=0),'')
    link=SimpleNamespace(net=net,slug='game',url='http://host',room='room',error="can't reach the host (ReadTimeout)")
    monkeypatch.setattr(net,'links',{'game':link})
    monkeypatch.setattr(room_matches,'packet',lambda *args:{'header':''})
    search=deep_permuter.Search(net,1,1,Log(),fn)
    with pytest.raises(deep_permuter.SearchLimit,match='Host unavailable'):
        deep_profiles.probe(search,AGENT,proj,fn,'exact C',{'toolchain':'other','flags':['-O2']})
    saved=room_matches.pending(net,'game','client')[0]
    assert saved['source']=='exact C' and saved['evidence']['compiler']['toolchain']=='other'
