"""Unchanged room snapshots need metadata reads, not source inflation or writes."""
import json
from types import SimpleNamespace

from decompnet.app import room_matches
from decompnet.app.engine import RomNetwork
from tests.test_loading import tiny_game


def test_unchanged_room_inputs_do_not_write_or_invalidate_map(tmp_path):
    net=RomNetwork(tmp_path/'db',tmp_path)
    tiny_game(net)
    proj=net.project_row('game')
    fn=net.store.one('SELECT id,vram FROM functions LIMIT 1')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=? WHERE id=?', ('int f(void){return 0;}\n'*1000,fn['id']))
    net.store.conn.create_function('payload_read',2,lambda *a: (_ for _ in ()).throw(AssertionError('Context sync must not inflate C')))
    state={'compiler_settings':{'compiler':json.loads(proj['config'])['compiler']},
           'function_contexts':[{'vram':fn['vram'],'header':'int helper(void);'}]}
    room_matches.sync_inputs(net,'game',state)
    before=net.store.conn.total_changes
    room_matches.sync_inputs(net,'game',state)
    assert net.store.conn.total_changes==before


def test_recovery_skips_payloads_already_known_to_host(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'db',tmp_path)
    tiny_game(net)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source=?", ('int f(void){return 0;}\n'*1000,))
    accepted=[dict(row) for row in net.store.all('SELECT name,vram FROM main.functions')]
    monkeypatch.setattr(room_matches,'packet',lambda *a: (_ for _ in ()).throw(AssertionError('Known match must not be prepared again')))
    net.store.conn.create_function('payload_read',2,lambda *a: (_ for _ in ()).throw(AssertionError('Must not inflate source')))
    room_matches.recover_client(SimpleNamespace(net=net,slug='game'),{'matched':accepted})
