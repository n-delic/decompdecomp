"""Unfinished donors save work only after the destination's ordinary judge improves its own score."""
from types import SimpleNamespace

import pytest

from decompnet.app import partial_reuse
from decompnet.app.twins import Job

from .test_rom import built  # noqa: F401
from .test_similar_matches import pair as pair_fixture

pair = pair_fixture


def prepare(net):
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL,best_source='int old(int x) { return x + 59; }',best_score=10 WHERE id=1")
        db.execute('UPDATE functions SET target=(SELECT target FROM functions WHERE id=1),best_score=100 WHERE id=2')


def judge(net, calls, score):
    def run(agent, proj, fn, source):
        calls.append(source)
        net._record(fn, agent, score, source, 1, matched=score == 0)
        return {'score': score, 'matched': score == 0}
    return run


def test_partial_candidate_is_rejudged_saved_as_open_and_not_retried(pair, monkeypatch):
    prepare(pair)
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', judge(pair, calls, 20))
    assert pair.twins.across_games('destination', Job()) == 0
    target = pair._function('new', 'destination')
    assert target['status'] == 'open' and target['best_score'] == 20
    assert 'int new(int' in target['best_source'] and 'Partial candidate' in target['best_source']
    assert pair.twins.cross_game_results('destination')['counts'] == {'improved': 1}
    assert not pair.store.one('SELECT 1 FROM cross_game_index WHERE function_id=1')
    assert pair.twins.across_games('destination', Job()) == 0
    assert len(calls) == 1
    # The same donor candidate is cached even though it claims a higher percentage than the receiver.
    assert target['best_score'] > pair._function('old', 'source')['best_score']


@pytest.mark.parametrize('score', [None, 150, 0])
def test_failed_worse_and_exact_verdicts_do_not_copy_donor_percent(pair, monkeypatch, score):
    prepare(pair)
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', judge(pair, calls, score))
    assert pair.twins.across_games('destination', Job()) == (score == 0)
    target = pair._function('new', 'destination')
    assert target['best_score'] == (0 if score == 0 else 100)
    assert target['status'] == ('matched' if score == 0 else 'open')
    assert len(calls) == 1


def test_changed_donor_or_destination_context_invalidates_cache(pair, monkeypatch):
    prepare(pair)
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', judge(pair, calls, 150))
    pair.twins.across_games('destination')
    pair.twins.across_games('destination')
    assert len(calls) == 1
    with pair.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef int s32;' WHERE project_id=2")
    pair.twins.across_games('destination')
    assert len(calls) == 2
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int old(int x) { return x + 58; }',best_score=5 WHERE id=1")
    pair.twins.across_games('destination')
    assert len(calls) == 3


@pytest.mark.parametrize('blocked', ['different', 'held', 'worse', 'stopped', 'budget'])
def test_nonidentical_held_worse_or_stopped_candidates_are_not_compiled(pair, monkeypatch, blocked):
    prepare(pair)
    job = Job()
    agent, _ = pair.local_agent('worker')
    with pair.store.tx() as db:
        if blocked == 'different':
            db.execute("UPDATE functions SET target=? WHERE id=2", (b'\0'*32,))
        if blocked == 'held':
            db.execute('INSERT INTO claims VALUES(?,?,0,99999999999)', (2, agent.id))
        if blocked == 'worse':
            db.execute('UPDATE functions SET best_score=200 WHERE id=1')
    if blocked == 'stopped':
        job.stop.set()
    if blocked == 'budget':
        job.reuse_limit = 0
        job.reuse_attempts = 0
        job.reuse_deadline = float('inf')
    monkeypatch.setattr(pair, 'try_quietly', lambda *a: pytest.fail('must not compile'))
    assert pair.twins.across_games('destination', job) == 0


def test_partial_improvement_notifies_configured_cascade(pair, monkeypatch):
    prepare(pair)
    seen = []
    monkeypatch.setattr(pair.cascades, 'notify', lambda p, fid: seen.append((p['slug'], fid)))
    agent, _ = pair.local_agent('worker')
    pair._record(pair._function('old', 'source'), agent, 5, 'int old(int x){return x;}', 1)
    pair._record(pair._function('old', 'source'), agent, 7, 'int old(int x){return x+1;}', 1)
    assert seen == [('source', 1)]


def test_imported_partial_is_checked_without_installing_unmatched_source(pair, monkeypatch, tmp_path):
    prepare(pair)
    calls = []
    proj = pair._project('destination')
    mine = pair.twins.prints(proj)
    monkeypatch.setattr(pair, 'fork_slug', lambda slug: slug if slug == 'destination' else None)
    monkeypatch.setattr(pair.forks, 'get', lambda slug: (None, SimpleNamespace(root=tmp_path), {'platform':'n64'}))
    def attempt(agent, slug, function, source, keep):
        assert keep is False
        return judge(pair, calls, 20)(agent, pair._project(slug), pair._function(function, slug), source)
    monkeypatch.setattr(pair.forks, 'attempt', attempt)
    # Directly exercise shared partial path; imported target readers have their own existing fingerprint tests.
    assert partial_reuse.run(pair.twins, proj, {mine[2].key:[2]}, mine) == 0
    assert len(calls) == 1 and pair._function('new', 'destination')['best_score'] == 20


def test_imported_header_changes_invalidate_input_stamp(tmp_path):
    (tmp_path/'include').mkdir()
    p = tmp_path/'include'/'types.h'
    p.write_text('typedef int value;')
    first = partial_reuse.input_stamp(tmp_path)
    p.write_text('typedef short value;')
    assert partial_reuse.input_stamp(tmp_path) != first


def test_partial_transfer_uses_real_compiler_and_target_byte_judge(built, tmp_path):  # noqa: F811
    from decompnet.app.engine import RomNetwork

    from .test_app_engine import CLAMP
    from .test_rom import ROM
    from .test_twins_across_games import copy_project

    net = RomNetwork(tmp_path/'real.db', tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('worker')
    net.split_function(agent, slug, 'func_800007C4', '0x800007D0')
    copy_project(net, slug, 'destination')
    # Establish a real, judged partial donor and a deliberately worse destination.
    candidate = CLAMP.replace('return hi;', 'return hi + 1;')
    assert candidate != CLAMP
    net.try_quietly(agent, net._project(slug), net._function('func_800007D0', slug), candidate)
    donor = net._function('func_800007D0', slug)
    assert donor['status'] == 'open' and donor['best_source']
    net.try_quietly(agent, net._project('destination'), net._function('func_800007D0', 'destination'),
                    'int func_800007D0(int x,int lo,int hi) { return 123; }')
    before = net._function('func_800007D0', 'destination')
    assert donor['best_score'] < before['best_score']
    assert net.twins.across_games('destination') == 0
    after = net._function('func_800007D0', 'destination')
    assert after['status'] == 'open' and after['best_score'] < before['best_score']
    assert net.twins.cross_game_results('destination')['counts']['improved'] >= 1


def test_configured_cascade_schedules_open_partial_donors(pair, monkeypatch):
    prepare(pair)
    queued = []
    monkeypatch.setattr('decompnet.app.reuse_cascade.purpose_work.start', lambda purpose,project,fn,*args,**kw:queued.append(lambda:fn(*args)))
    pair.cascades.configure(True)
    pair.cascades.notify(pair._project('source'), 1)
    assert pair.cascades.pending == {'destination'} and len(queued) == 1


def test_imported_donor_uses_saved_best_not_unverified_current_file(pair, monkeypatch, tmp_path):
    prepare(pair)
    proj = pair._project('destination')
    mine = pair.twins.prints(proj)
    donor_prints = pair.twins.prints(pair._project('source'))
    monkeypatch.setattr(pair, 'fork_slug', lambda slug: slug if slug == 'source' else None)
    monkeypatch.setattr(pair.forks, 'get', lambda slug: (None, SimpleNamespace(root=tmp_path), {'platform':'n64'}))
    monkeypatch.setattr(pair.twins, 'prints', lambda p: donor_prints if p['id'] == 1 else mine)
    monkeypatch.setattr(pair.twins, '_fork_prints', lambda p,**kwargs: donor_prints)
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', judge(pair, calls, 20))
    partial_reuse.run(pair.twins, proj, {mine[2].key:[2]}, mine)
    assert len(calls) == 1 and 'return x + 59;' in calls[0]


@pytest.mark.parametrize("limit", [10, float("inf")])
def test_manual_partial_budget_can_exceed_the_automatic_trial_cap(pair, monkeypatch, limit):
    from decompnet.app.free_candidates import budget
    from decompnet.app.twins import Print
    prepare(pair)
    with pair.store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,unit,vram,status,best_score) "
                   "SELECT 3,project_id,'new_copy',size,n_insns,target,relocs,unit,vram,'open',100 FROM functions WHERE id=2")
    monkeypatch.setattr(pair.twins, 'prints', lambda proj: {fid: Print('same', [], 32) for fid in
                       ([1] if proj['id'] == 1 else [2, 3])})
    monkeypatch.setattr(partial_reuse, 'MAX_TRIALS', 1)
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', judge(pair, calls, 20))
    job = Job()
    budget(job, seconds=float("inf") if limit == float("inf") else 60, attempts=limit)
    job.partial_limit = limit
    partial_reuse.run(pair.twins, pair.project_row('destination'), {'same':[2,3]},
                      pair.twins.prints(pair.project_row('destination')), job)
    assert len(calls) == 2 and job.reuse_attempts == 2


def test_all_saved_cross_game_matches_are_loaded_on_demand(pair):
    with pair.store.tx() as db:
        db.executemany("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,reason,created_at) VALUES(2,2,1,1,'match','verified',?)", [(i,) for i in range(45)])
    assert len(pair.twins.cross_game_results('destination')['matches']) == 30
    full = pair.twins.cross_game_results('destination', all_matches=True)
    assert len(full['matches']) == 45
    assert full['counts']['match'] == 45
    assert len(full['recent']) == 30


@pytest.mark.parametrize('gate', ['missing fingerprint', 'small', 'assembly', 'different', 'claimed', 'worse', 'cancelled'])
def test_rejected_partial_donors_do_not_decode_saved_source(pair, monkeypatch, gate):
    from decompnet import large_text_storage, payload_storage
    from decompnet.app.twins import Print
    prepare(pair)
    large_text_storage.install(pair.store.conn, {'functions'})
    source = '/* ' + 'unused synthetic source\n' * 4000 + ' */\nint old(int x){return x;}'
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET best_source=? WHERE id=1', (source,))
        if gate == 'worse':db.execute('UPDATE functions SET best_score=200 WHERE id=1')
        if gate == 'claimed':
            agent, _ = pair.local_agent('busy')
            db.execute('INSERT INTO claims VALUES(?,?,0,99999999999)', (2, agent.id))
    reads = []
    digest = payload_storage.digest(source)
    def unpack(key, payload):
        if key == digest:reads.append(key)
        return payload_storage.unpack(key, payload)
    pair.store.conn.create_function('payload_read', 2, unpack, deterministic=True)
    pair.store._reader().create_function('payload_read', 2, unpack, deterministic=True)
    assert pair.store.one('SELECT best_source FROM functions WHERE id=1')['best_source'] == source
    assert reads == [digest]
    reads.clear()
    job = Job()
    def prints(proj):
        if gate == 'cancelled':job.stop.set()
        if gate == 'missing fingerprint':return {}
        return {1: Print('different' if gate == 'different' else 'same', [], 8 if gate == 'small' else 32,
                         asm=gate == 'assembly')}
    monkeypatch.setattr(pair.twins, 'prints', prints)
    monkeypatch.setattr(pair, 'try_quietly', lambda *args: pytest.fail('rejected donor compiled'))
    partial_reuse.run(pair.twins, pair.project_row('destination'), {'same': [2]}, {2: Print('same', [], 32)},
                      job, projects=[pair.project_row('source')])
    assert reads == []


@pytest.mark.parametrize('change', ['source', 'matched', 'deleted', 'worse'])
def test_partial_donor_details_are_fresh_after_fingerprint_selection(pair, monkeypatch, change):
    from decompnet.app.twins import Print
    prepare(pair)
    def prints(proj):
        with pair.store.tx() as db:
            if change == 'source':db.execute("UPDATE functions SET best_source='int old(int x){return x+7;}' WHERE id=1")
            elif change == 'matched':db.execute("UPDATE functions SET status='matched' WHERE id=1")
            elif change == 'deleted':db.execute('DELETE FROM functions WHERE id=1')
            else:db.execute('UPDATE functions SET best_score=200 WHERE id=1')
        return {1: Print('same', [], 32)}
    monkeypatch.setattr(pair.twins, 'prints', prints)
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', judge(pair, calls, 20))
    partial_reuse.run(pair.twins, pair.project_row('destination'), {'same': [2]}, {2: Print('same', [], 32)},
                      projects=[pair.project_row('source')])
    assert len(calls) == (1 if change == 'source' else 0)
    if calls:assert 'return x+7;' in calls[0]
