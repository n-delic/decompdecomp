"""Automatic names share SDK identity evidence and the normal guarded rename routes."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_names import name_functions
from decompnet.network import NetworkError


@pytest.fixture
def sdk_case(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',
                         ('demo', 'Demo', 'mips', 1, str(tmp_path), json.dumps({'kind':'rom'}))).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,vram,meta) VALUES(?,?,?,?,?,?)',
                         (pid, 'func_80001000', 40, 10, 0x80001000, '{}')).lastrowid
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('Example SDK / library.a', 'mips', 1, 'sdkHelper', 'exact', 40))
    monkeypatch.setattr(net.twins, 'prints', lambda _: {fid: SimpleNamespace(key='exact',size=40,asm=False)})
    monkeypatch.setattr(net, 'symbols', lambda _: SimpleNamespace(by_name={}))
    monkeypatch.setattr(net, 'add_note', lambda *args: None)
    return net, pid, fid


@pytest.mark.parametrize('fork', [False, True])
@pytest.mark.parametrize('reject', [False, True])
def test_selected_sdk_names_use_normal_rom_or_imported_validation(sdk_case, monkeypatch, fork, reject):
    net, pid, fid = sdk_case
    monkeypatch.setattr(net, 'fork_slug', lambda _: 'demo' if fork else None)
    calls = []
    def rename(agent, slug, old, new, message):
        calls.append((old, new))
        if reject:
            raise NetworkError('matched code would change')
        with net.store.tx() as db:
            db.execute('UPDATE functions SET name=? WHERE id=?', (new, fid))
        return '✅ Renamed'
    def batch(agent, slug, pairs, message):
        assert not fork
        return rename(agent, slug, *pairs[0], message)
    monkeypatch.setattr(net, 'rename_symbol', rename)
    monkeypatch.setattr(net, 'rename_symbols', batch)
    assert name_functions(net, 'demo') == (0 if reject else 1)
    row = net.store.one('SELECT * FROM functions WHERE id=?', fid)
    assert row['status'] != 'matched'
    if reject:
        assert row['name'] == 'func_80001000' and not json.loads(row['meta']).get('naming')
    else:
        assert json.loads(row['meta'])['naming']['method'] == 'sdk_signature'
        assert json.loads(row['meta'])['naming']['source_corpora'] == ['Example SDK / library.a']
        assert name_functions(net, 'demo') == 0
    assert calls == [('func_80001000', 'sdkHelper')]


@pytest.mark.parametrize('reason', ['named','claimed','disabled','builtin','collision','ambiguous','duplicate','asm','size','generated'])
def test_names_are_not_guessed_or_overwritten(sdk_case, monkeypatch, reason):
    net, pid, fid = sdk_case
    agent = net.local_agent('worker')[0] if reason == 'claimed' else None
    with net.store.tx() as db:
        if reason == 'named':db.execute('UPDATE functions SET name=? WHERE id=?', ('userName',fid))
        if reason == 'claimed':
            db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fid,agent.id,1,99999999999))
        if reason == 'disabled':db.execute('UPDATE projects SET config=? WHERE id=?', ('{"sdk_libraries":[]}',pid))
        if reason == 'generated':db.execute('UPDATE sdk_signatures SET name=?', ('func_80002000',))
        if reason == 'builtin':db.execute('UPDATE sdk_signatures SET name=?', ('memcpy',))
        if reason == 'collision':db.execute('INSERT INTO functions(project_id,name,size) VALUES(?,?,?)', (pid,'sdkHelper',48))
        if reason == 'ambiguous':
            db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                       ('Other SDK','mips',1,'differentMeaning','exact',40))
        if reason == 'size':db.execute('UPDATE sdk_signatures SET size=44')
    if reason in ('duplicate','asm'):
        prints = {fid: SimpleNamespace(key='exact',size=40,asm=reason=='asm')}
        if reason == 'duplicate':prints[fid+1] = SimpleNamespace(key='exact',size=40,asm=False)
        monkeypatch.setattr(net.twins, 'prints', lambda _: prints)
    monkeypatch.setattr(net, 'rename_symbols', lambda *args: pytest.fail('unsafe rename'))
    assert name_functions(net, 'demo') == 0


def test_rejected_unchanged_sdk_names_wait_but_changed_inputs_retry(sdk_case, monkeypatch):
    net, pid, fid = sdk_case
    monkeypatch.setattr(net, 'fork_slug', lambda _: None)
    calls = []
    def reject(*args):
        calls.append(1)
        return 'Rejected: declarations conflict'
    monkeypatch.setattr(net, 'rename_symbols', reject)
    assert name_functions(net, 'demo') == name_functions(net, 'demo') == 0
    assert len(calls) == 1
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='changed declarations' WHERE id=?", (fid,))
    name_functions(net, 'demo')
    assert len(calls) == 2
    with net.store.tx() as db:
        db.execute("UPDATE sdk_signatures SET corpus='updated library'")
    name_functions(net, 'demo')
    assert len(calls) == 3
