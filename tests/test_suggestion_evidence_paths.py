"""Shared evidence caches invalidate on actual inputs and keep expensive refreshes single-flight."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from decompnet import candidate_scope
from decompnet.app.collab import Room
from decompnet.app.profile_hypotheses import captured

from .test_claims import _game


def test_fingerprint_ignores_room_compiler_settings_and_preview_edits(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    proj = net._project('game')
    first = net.twins.prints(proj)
    revision = net.store.read_version('fingerprints', 1)
    cfg = json.loads(proj['config']); cfg['collab'] = {'open': True}; cfg['compiler'] = {'toolchain':'other','flags':[]}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=1', (json.dumps(cfg),))
    assert net.store.read_version('fingerprints', 1) == revision
    monkeypatch.setattr(net.twins, '_build_rom_prints', lambda _: pytest.fail('rebuilt unchanged fingerprints'))
    assert net.twins.prints(net._project('game')) is first
    cfg['big_endian'] = not cfg.get('big_endian', True)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=1', (json.dumps(cfg),))
    assert net.store.read_version('fingerprints', 1) != revision


def test_simultaneous_fingerprint_builds_share_work_and_forget_rebuilds(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    proj = net._project('game')
    entered, release, second_started = Event(), Event(), Event()
    calls = []
    original = net.twins._build_rom_prints
    def held(proj):
        calls.append(1); entered.set(); assert release.wait(5)
        return original(proj)
    monkeypatch.setattr(net.twins, '_build_rom_prints', held)
    def second():
        second_started.set(); return net.twins.prints(proj)
    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(net.twins.prints, proj)
        assert entered.wait(5)
        two = pool.submit(second); assert second_started.wait(5)
        release.set()
        assert one.result() is two.result()
    assert len(calls) == 1
    net.twins.forget(proj)
    net.twins.prints(proj)
    assert len(calls) == 2


def test_compact_profile_evidence_updates_deletes_and_rolls_back(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    def evidence(flag):
        return json.dumps({'before':{'source_sha256':'same'}, 'after':{'source_sha256':'same',
                          'settings':{'compiler':{'toolchain':'test','flags':[flag]}}}})
    with net.store.tx() as db:
        example = db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)', (evidence('-O1'),)).lastrowid
    own = {'toolchain':'test','flags':[]}
    assert captured(net, {'id':1}, own) == [['-O1']]
    with pytest.raises(ValueError), net.store.tx() as db:
        db.execute('UPDATE repair_examples SET evidence=? WHERE id=?', (evidence('-O2'), example))
        assert captured(net, {'id':1}, own) == [['-O2']]
        raise ValueError('rollback')
    assert captured(net, {'id':1}, own) == [['-O1']]
    with net.store.tx() as db:
        db.execute('DELETE FROM repair_examples WHERE id=?', (example,))
    assert captured(net, {'id':1}, own) == []
    assert net.store.one('SELECT COUNT(*) AS n FROM repair_profile_evidence')['n'] == 0


def test_room_member_refresh_is_single_flight(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    room = Room(net, 'game')
    entered, release, second_started = Event(), Event(), Event()
    calls = []
    def held():
        calls.append(1); entered.set(); assert release.wait(5)
        return [{'name':'member'}]
    monkeypatch.setattr(room, '_members_now', held)
    def second():
        second_started.set(); return room.members()
    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(room.members); assert entered.wait(5)
        two = pool.submit(second); assert second_started.wait(5)
        release.set()
        assert one.result() == two.result() == [{'name':'member'}]
    assert len(calls) == 1


def test_worker_scope_reuses_inputs_and_invalidates_headers_symbols_and_callees(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open'), ('helper', 40, 'matched')])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(void){return helper();}' WHERE name='f'")
        db.execute("UPDATE functions SET matched_source='int helper(void){return 1;}' WHERE name='helper'")
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'helper',123,4,'func')")
    proj, fn = net._project('game'), net._function('f', 'game')
    first = candidate_scope.key(net, proj, fn)
    queries = []; net.store._reader().set_trace_callback(queries.append)
    assert candidate_scope.key(net, proj, fn) == first
    assert not any('FROM symbols' in q or 'FROM context_versions' in q or 'SELECT name,matched_source' in q for q in queries)
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',1,'int helper(void);')")
    second = candidate_scope.key(net, proj, fn); assert second != first
    with net.store.tx() as db:
        db.execute("UPDATE symbols SET address=124 WHERE name='helper'")
    third = candidate_scope.key(net, proj, fn); assert third != second
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int helper(void){return 2;}' WHERE name='helper'")
    assert candidate_scope.key(net, proj, fn) != third


def test_similar_suggestions_limit_metadata_first_and_reuse_ranked_results(tmp_path, monkeypatch):
    from decompnet.app import matched_context, sdk_hypotheses, shared_reuse
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    cfg = {'platform':'n64','kind':'rom','big_endian':True,'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    code = lambda immediate: b''.join(word.to_bytes(4,'big') for word in [0x34020000|immediate, 0x03e00008, *([0]*8)])
    with net.store.tx() as db:
        db.execute("UPDATE projects SET arch='mips',big_endian=1,config=? WHERE id=1", (json.dumps(cfg),))
        db.execute("UPDATE functions SET target=?,relocs='[]' WHERE id=1", (code(1),))
        db.execute("INSERT INTO projects(id,slug,name,root,arch,big_endian,config) VALUES(2,'other','Other',?,'mips',1,?)", (str(tmp_path),json.dumps(cfg)))
        fid = db.execute("INSERT INTO functions(project_id,name,status,size,n_insns,vram,target,relocs,matched_source) VALUES(2,'helper','matched',40,10,8192,?,'[]','int helper(void){return 2;}')", (code(2),)).lastrowid
        db.execute("INSERT INTO cross_game_index VALUES(?,2,'different',?,40,0)", (fid, net.twins._compiler_key(cfg['compiler'])))
    monkeypatch.setattr(shared_reuse, 'hints', lambda *a: [])
    monkeypatch.setattr(matched_context, 'reference_hints', lambda net, items: items)
    monkeypatch.setattr(sdk_hypotheses, 'hints', lambda *a: [])
    queries = []; net.store._reader().set_trace_callback(queries.append)
    first = net.twins.suggestions('game', 'f')
    assert first and first[0]['name'] == 'helper' and first[0]['kind'] == 'similar code; unverified'
    assert net.twins.suggestions('game', 'f') == first
    shortlist = [q for q in queries if 'WITH shortlist AS MATERIALIZED' in q]
    assert len(shortlist) == 1 and 'LIMIT 400)' in shortlist[0]
    plan = net.store.all('EXPLAIN QUERY PLAN ' + shortlist[0])
    assert any('idx_cross_game_compiler_size' in r['detail'] for r in plan)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='new_helper' WHERE id=?", (fid,))
    assert net.twins.suggestions('game', 'f')[0]['name'] == 'new_helper'


def test_hot_shared_inputs_survive_cache_churn():
    from decompnet.read_cache import ReadCache
    cache, builds = ReadCache(), []
    def hot():
        return cache.get('hot-symbols', 1, lambda: builds.append(1) or 'symbols')
    hot()
    for i in range(200):
        cache.get(('candidate', i), 1, lambda i=i: i)
        assert hot() == 'symbols'
    assert builds == [1] and len(cache.entries) == 128


def test_active_build_survives_eviction_pressure():
    from decompnet.read_cache import ReadCache
    cache, calls = ReadCache(), []
    entered, release = Event(), Event()
    def build():
        calls.append(1); entered.set(); assert release.wait(5)
        return 'shared'
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(cache.get, 'building', 1, build)
        assert entered.wait(5)
        for i in range(200):
            cache.get(i, 1, lambda: 'other')
        second = pool.submit(cache.get, 'building', 1, build)
        release.set()
        assert first.result() == second.result() == 'shared'
    assert calls == [1] and len(cache.entries) <= 128


def test_profile_projection_backfills_existing_evidence(tmp_path):
    from decompnet.repair_profile_index import install
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    evidence = json.dumps({'before':{'source_sha256':'same'}, 'after':{'source_sha256':'same',
                          'settings':{'compiler':{'toolchain':'test','flags':['-O1']}}}})
    with net.store.tx() as db:
        for event in ('INSERT','UPDATE','DELETE'):
            db.execute(f'DROP TRIGGER repair_profile_{event}')
        db.execute('DROP TABLE repair_profile_evidence')
        db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)', (evidence,))
        install(db)
    assert captured(net, {'id':1}, {'toolchain':'test','flags':[]}) == [['-O1']]
    with net.store.tx() as db:
        db.executemany("INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,'invalid JSON')", [()] * 128)
    assert captured(net, {'id':1}, {'toolchain':'test','flags':[]}) == []


def test_projection_preserves_hash_value_types(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    evidence = json.dumps({'before':{'source_sha256':1}, 'after':{'source_sha256':'1',
                          'settings':{'compiler':{'toolchain':'test','flags':['-O1']}}}})
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)', (evidence,))
    assert captured(net, {'id':1}, {'toolchain':'test','flags':[]}) == []
