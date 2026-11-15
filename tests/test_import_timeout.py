"""An operational split deadline must not repeat the import as a debug-name fallback."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.rom import Picked, RomError, split


def fixture(tmp_path):
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    debug = SimpleNamespace(named=['synthetic_name'], summary=lambda: 'synthetic debug symbols',
                            symbol_addrs=lambda: ['synthetic_name = 0x1000;'])
    console = SimpleNamespace(id='ps2', name='Synthetic console', debug_info=lambda *args: debug)
    picked = Picked('ps2', b'synthetic program', 'game.bin', dict(title='Synthetic', size=17,
                   game_code='TEST', region='test', compiler_guess='test', sha1='synthetic'), False)
    return net, console, picked


@pytest.mark.parametrize('reimport', [False, True])
def test_deadline_stops_one_process_without_retry_or_publishing(tmp_path, monkeypatch, reimport):
    net, console, picked = fixture(tmp_path)
    if reimport:
        root = tmp_path/'existing'
        root.mkdir()
        (root/'saved.txt').write_text('preserved work')
        with net.store.tx() as db:
            db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                       ('existing', 'Existing', str(root), 'mips', json.dumps({'kind':'rom','platform':'ps2'})))
        monkeypatch.setattr(net, 'console', lambda _: console)
    before = [dict(r) for r in net.store.all('SELECT * FROM projects')]
    processes, stopped, symbols, messages = [], [], [], []
    def start(*args, **kwargs):
        proc = SimpleNamespace(communicate=lambda **kwargs: pytest.fail('deadline must precede polling'))
        processes.append(proc)
        return proc
    def disassemble(workdir, picked, *, log, **kwargs):
        symbols.append(kwargs.get('symbols'))
        return split._splat(['split','synthetic.yaml'], workdir, log, timeout=0)
    monkeypatch.setattr(console, 'split_game', disassemble, raising=False)
    monkeypatch.setattr(split, '_check_splat_runtime', lambda: None)
    monkeypatch.setattr(split.subprocess, 'Popen', start)
    monkeypatch.setattr(split, 'stop_process_tree', stopped.append)
    with pytest.raises(RomError, match='Staged disassembly exceeded its time limit'):
        net.import_split(console, picked, log=messages.append, reimport_slug='existing' if reimport else None)
    assert len(processes) == 1 and stopped == processes
    assert symbols == [['synthetic_name = 0x1000;']]
    assert not any('splitting without them' in m for m in messages)
    assert [dict(r) for r in net.store.all('SELECT * FROM projects')] == before
    if reimport:
        assert (root/'saved.txt').read_text() == 'preserved work'
        assert not list(tmp_path.glob('.existing-reimport-*'))


def test_symbol_rejection_still_retries_without_debug_names(tmp_path, monkeypatch):
    net, console, picked = fixture(tmp_path)
    calls, messages = [], []
    reached = RuntimeError('synthetic fallback reached')
    def disassemble(workdir, picked, *, log, **kwargs):
        calls.append(kwargs.get('symbols'))
        if len(calls) == 1:raise RomError('synthetic symbol rejection')
        raise reached
    monkeypatch.setattr(console, 'split_game', disassemble, raising=False)
    with pytest.raises(RuntimeError) as caught:net.import_split(console, picked, log=messages.append)
    assert caught.value is reached
    assert calls == [['synthetic_name = 0x1000;'], None]
    assert any('splitting without them' in m for m in messages)
    assert not net.store.all('SELECT * FROM projects')
