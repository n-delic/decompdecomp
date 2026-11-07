"""Room verification and membership across real independent SQLite files."""
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from decompnet.app import compiler_context, prepared_evidence, room_matches, room_membership, source_preparation
from decompnet.network import MACHINE_AGENTS, Agent, NetworkError

from . import test_project_storage as base

scoped = base.scoped


def request(token):
    return Request({'type': 'http', 'headers': [(b'authorization', ('Bearer '+token).encode())]})


def member_room(net, stores, tmp_path):
    base._room_projects(net, stores, tmp_path)
    room = base._scoped_room(net, 'first')
    room.configure(approve=True)
    token, name = room.join('sam', 'synthetic-1')
    with pytest.raises(NetworkError, match='waiting'):
        room.member(request(token))
    room.approve(name)
    member = room.member(request(token))
    child = room.agent_for(member, 'helper')
    for pid in (1, 2):
        with stores[pid].tx() as db:
            db.execute('INSERT INTO claims(function_id,agent_id,expires_at) VALUES(?,?,?)', (pid*10, child.id, time.time()+60))
    return room, token, child


def test_membership_identities_are_shared_but_events_and_claim_cleanup_are_project_local(scoped, tmp_path):
    net, stores = scoped
    room, token, child = member_room(net, stores, tmp_path)
    assert room.pending() == []
    assert net.store.one('SELECT origin FROM agents WHERE id=?', child.id)['origin'] == 'collab:first'
    assert stores[1].one('SELECT COUNT(*) n FROM events')['n'] == 2
    assert stores[2].one('SELECT COUNT(*) n FROM events')['n'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM events')['n'] == 0
    room.remove('sam')
    with pytest.raises(NetworkError):
        net.authenticate(token)
    assert stores[1].one('SELECT 1 FROM claims') is None
    assert stores[2].one('SELECT agent_id FROM claims')['agent_id'] == child.id
    assert stores[1].one('SELECT COUNT(*) n FROM events')['n'] == 3
    assert net.store.one('SELECT COUNT(*) n FROM room_membership_effects')['n'] == 0


@pytest.mark.parametrize('after_project_commit', [False, True])
def test_membership_handoff_recovers_interruption_without_duplicate_events(scoped, tmp_path, monkeypatch, after_project_commit):
    net, stores = scoped
    room, token, _ = member_room(net, stores, tmp_path)
    if after_project_commit:
        original = net.store.tx
        calls = 0
        @contextmanager
        def fail_ack(*args, **kw):
            nonlocal calls
            calls += 1
            if calls == 2:  # identity change committed; the second transaction acknowledges delivery
                raise RuntimeError('interrupted acknowledgement')
            with original(*args, **kw) as db:
                yield db
        monkeypatch.setattr(net.store, 'tx', fail_ack)
    else:
        original = stores[1].tx
        @contextmanager
        def fail_project(*args, **kw):
            with original(*args, **kw) as db:
                yield db
                raise RuntimeError('interrupted project commit')
        monkeypatch.setattr(stores[1], 'tx', fail_project)
    with pytest.raises(RuntimeError, match='interrupted'):
        room.remove('sam')
    # Revocation is durable even if project delivery was interrupted.
    with pytest.raises(NetworkError):
        net.authenticate(token)
    assert net.store.one('SELECT COUNT(*) n FROM room_membership_effects')['n'] == 1
    assert bool(stores[1].one('SELECT 1 FROM claims')) is (not after_project_commit)
    assert stores[1].one('SELECT COUNT(*) n FROM events')['n'] == (3 if after_project_commit else 2)
    monkeypatch.setattr(net.store if after_project_commit else stores[1], 'tx', original)
    restarted = base._scoped_room(net, 'first')  # drains the persisted handoff
    restarted._drain_membership()
    assert stores[1].one('SELECT 1 FROM claims') is None
    assert stores[2].one('SELECT 1 FROM claims') is not None
    assert stores[1].one('SELECT COUNT(*) n FROM events')['n'] == 3
    assert net.store.one('SELECT COUNT(*) n FROM room_membership_effects')['n'] == 0


def test_empty_members_can_rejoin_but_direct_project_credits_are_preserved(scoped, tmp_path):
    net, stores = scoped
    base._room_projects(net, stores, tmp_path)
    room = base._scoped_room(net, 'first')
    _, name = room.join('sam', 'synthetic-1')
    assert room.join('sam', 'synthetic-1')[1] == name
    aid = net.store.one("SELECT id FROM agents WHERE name='sam'")['id']
    with stores[1].tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_agent_id=?", (aid,))
    assert room.join('sam', 'synthetic-1')[1] == 'sam2'


def test_machine_claim_expiry_uses_shared_identity_ids(scoped, tmp_path):
    net, stores = scoped
    base._room_projects(net, stores, tmp_path)
    room = base._scoped_room(net, 'first')
    with net.store.tx() as db:
        db.execute('INSERT INTO agents(id,name,token_hash) VALUES(2,?,?)', (MACHINE_AGENTS[0], 'machine'))
    with stores[1].tx() as db:
        db.execute('INSERT INTO claims(function_id,agent_id,expires_at) VALUES(10,2,0)')
    room._expire_claims()
    assert stores[1].one('SELECT abandons FROM functions')['abandons'] == 0
    assert stores[1].one("SELECT 1 FROM events WHERE kind='expired'") is None
    assert stores[1].one('SELECT 1 FROM claims') is None


def test_pending_verification_and_packets_never_read_another_project(scoped, tmp_path, monkeypatch):
    net, stores = scoped
    base._room_projects(net, stores, tmp_path)
    base._scoped_room(net, 'first')
    agent = Agent(1, 'worker', None)
    for pid in (1, 2):
        with stores[pid].tx() as db:
            cfg = json.loads(db.execute('SELECT config FROM projects').fetchone()['config'])
            cfg['compiler'] = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
            db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    first, _ = room_matches.save(net, 'first', 'host', agent, 'same_name', 'first candidate', None)
    second, _ = room_matches.save(net, 'second', 'host', agent, 'same_name', 'second candidate', None)
    assert [row['source'] for row in room_matches.pending(net, 'first')] == ['first candidate']
    assert [row['source'] for row in room_matches.pending(net, 'second')] == ['second candidate']
    assert 'source' not in room_matches.public(net, 'first')[0]
    with pytest.raises(NetworkError, match='belonging'):
        room_matches.retry(net, 'second', first)
    with pytest.raises(NetworkError, match='belonging'):
        room_matches.update(net, first, {}, project_id=2)
    with stores[1].tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='first exact'")
    result = room_matches.retry(net, 'first', first)
    assert result['matched'] and not room_matches.pending(net, 'first')
    assert room_matches.pending(net, 'second')[0]['key'] == second
    packet = room_matches.packet(net, 'second', 'same_name', 'second candidate')
    assert packet['header'] == 'int second;'
    assert net.store.one("SELECT 1 FROM settings WHERE key LIKE 'room-match:%'") is None


def test_prepared_verification_requires_exact_judgement_and_publishes_only_its_project(scoped, tmp_path, monkeypatch):
    net, stores = scoped
    base._room_projects(net, stores, tmp_path)
    base._scoped_room(net, 'first')
    profile = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    with stores[1].tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects').fetchone()['config'])
        cfg['compiler'] = profile
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
        db.execute('INSERT INTO claims(function_id,agent_id,expires_at) VALUES(10,1,?)', (time.time()+60,))
    agent = Agent(1, 'worker', None)
    fn, proj = net._function('same_name', 'first'), net.project_row('first')
    expected = source_preparation.inputs(net, proj, fn)
    monkeypatch.setattr(net, '_compile', lambda *args: SimpleNamespace(ok=True, obj=b'synthetic object', effective_settings={'compiler': profile}), raising=False)
    monkeypatch.setattr(compiler_context, 'acceptable', lambda *args: True)
    monkeypatch.setattr(net, '_evaluate', lambda *args: (object(), False, ['different linked bytes']), raising=False)
    with pytest.raises(NetworkError, match='linked-byte'):
        prepared_evidence.promote_measured(net, proj, fn, 'int same_name(void) { return 0; }', 'private header', profile, agent, expected)
    assert stores[1].one('SELECT COUNT(*) n FROM attempts')['n'] == 0
    assert net._function('same_name', 'first')['status'] == 'open'
    monkeypatch.setattr(net, '_evaluate', lambda *args: (object(), True, []))
    assert prepared_evidence.promote_measured(net, proj, fn, 'int same_name(void) { return 0; }', 'private header', profile, agent, expected)
    current = net._function('same_name', 'first')
    assert current['status'] == 'matched'
    assert net._context(1, current['unit'])['header'] == 'private header'
    assert stores[1].one('SELECT COUNT(*) n FROM attempts')['n'] == 1
    assert stores[2].one('SELECT COUNT(*) n FROM attempts')['n'] == 0
    assert net._context(2)['header'] == 'int second;'
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n'] == 0


def test_concurrent_membership_delivery_is_idempotent(scoped, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    net, stores = scoped
    base._room_projects(net, stores, tmp_path)
    with net.store.tx() as db:
        room_membership.enqueue(net, stores[1], db, 1, 'one durable event')
    original = net.store.all
    barrier = threading.Barrier(2)
    def read(sql, *args):
        rows = original(sql, *args)
        if sql.startswith('SELECT * FROM room_membership_effects'):
            barrier.wait(timeout=5)
        return rows
    monkeypatch.setattr(net.store, 'all', read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(room_membership.drain, net, stores[1], 1) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert stores[1].one('SELECT COUNT(*) n FROM events')['n'] == 1
    assert stores[2].one('SELECT COUNT(*) n FROM events')['n'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM room_membership_effects')['n'] == 0


def test_legacy_membership_effects_rollback_with_the_identity_transaction(scoped):
    net, _ = scoped
    with pytest.raises(RuntimeError, match='abort'):
        with net.store.tx() as db:
            room_membership.enqueue(net, net.store, db, 1, 'never committed')
            raise RuntimeError('abort')
    assert net.store.one('SELECT 1 FROM events') is None
    assert net.store.one("SELECT 1 FROM sqlite_master WHERE name='room_membership_effects'") is None


def test_missing_project_keeps_membership_handoff_for_recovery(scoped):
    net, stores = scoped
    with net.store.tx() as db:
        room_membership.enqueue(net, stores[1], db, 1, 'must not disappear')
    with stores[1].tx() as db:
        db.execute('DELETE FROM projects WHERE id=1')
    with pytest.raises(NetworkError, match='missing'):
        room_membership.drain(net, stores[1], 1)
    assert net.store.one('SELECT COUNT(*) n FROM room_membership_effects')['n'] == 1


def test_membership_delivery_defers_until_caller_transaction_closes(scoped):
    net, stores = scoped
    with net.store.tx() as db:
        room_membership.enqueue(net, stores[1], db, 1, 'after commit')
        room_membership.drain(net, stores[1], 1)
        assert stores[1].one('SELECT 1 FROM events') is None
    room_membership.drain(net, stores[1], 1)
    assert stores[1].one('SELECT detail FROM events')['detail'] == 'after commit'


def test_membership_delivery_waits_for_authenticated_requests(scoped, tmp_path):
    net, stores = scoped
    base._room_projects(net, stores, tmp_path)
    room = base._scoped_room(net, 'first')
    token, _ = room.join('sam', 'synthetic-1')
    with net.store.tx() as db:
        room_membership.enqueue(net, stores[1], db, 1, 'deliver on authenticated request')
    with pytest.raises(NetworkError, match='not a member'):
        room.member(request('invalid-token'))
    assert stores[1].one('SELECT COUNT(*) n FROM events')['n'] == 1
    assert net.store.one('SELECT COUNT(*) n FROM room_membership_effects')['n'] == 1
    room.member(request(token))
    assert stores[1].one('SELECT COUNT(*) n FROM events')['n'] == 2
    assert net.store.one('SELECT COUNT(*) n FROM room_membership_effects')['n'] == 0
