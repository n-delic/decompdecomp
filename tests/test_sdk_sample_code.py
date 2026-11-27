"""SDK sample code the port runs as the game's own is still set aside as SDK code (`Console.sdk_sample_code`).

An N64 game's `__scExec`, named from the SDK's exact signature, stayed open for agents: the port kit calls the
scheduler "the game's own code" (its copy is what the port runs), and that was read as "keep matching it".
Synthetic bytes; no tools, SDK or game needed.
"""
import json
import threading

import pytest

from decompnet import consoles
from decompnet.app import sdk_assembly, source_assembly
from decompnet.app.engine import RomNetwork
from decompnet.rom.symbols import SymbolTable

ULTRALIB = 'built in / decompals/ultralib e24c836796df'


@pytest.fixture
def project(tmp_path, monkeypatch):
    """(net, add): `add(name)` an open function library detection named from the ultralib rebuild."""
    net = RomNetwork(tmp_path / 'db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': []}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', 1, json.dumps(cfg))).lastrowid
    monkeypatch.setattr(net, 'symbols', lambda _: SymbolTable())
    hints = {}
    monkeypatch.setattr(net, 'sdk_functions', lambda proj: hints)

    def add(name):
        with net.store.tx() as db:
            fid = db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,target,relocs,status) '
                             'VALUES(?,?,?,?,?,?,?,?)', (pid, name, 0x80001000 + 64 * len(hints), 64, 16,
                                                         bytes([len(hints) + 1]) * 64, '[]', 'open')).lastrowid
        hints[fid] = {'names': [name], 'corpora': [ULTRALIB], 'fingerprint': name,
                      'evidence': 'exact relocation-normalized SDK signature'}
        return fid

    return net, add


def statuses(net):
    return {r['name']: r['status'] for r in net.project_store(1).all('SELECT name,status FROM functions')}


def test_only_the_n64s_scheduler_is_sample_code():
    n64 = consoles.get('n64')
    for name in ('osCreateScheduler', 'osScAddClient', '__scMain', '__scExec', '__scSchedule', '__scYield'):
        assert n64.sdk_sample_code(name), name
        assert n64.port_kit.sdk_group(name) is None, name  # the port still runs the game's own copy
    assert not n64.sdk_sample_code('read32') and not n64.sdk_sample_code('scheduleTask')
    assert not consoles.get('ps1').sdk_sample_code('__scExec')


def test_a_detected_scheduler_is_set_aside_as_sdk_code(project):
    net, add = project
    add('__scExec')
    add('osScAddClient')
    add('read32')  # a name the port kit doesn't know and nobody calls sample code: still the game's C work
    proj = net.project_row('game')
    assert sdk_assembly.discover_c(net, proj, threading.Event()) == 2
    assert statuses(net) == {'__scExec': 'asm', 'osScAddClient': 'asm', 'read32': 'open'}
    fn = net._function('__scExec', 'game')
    proof = json.loads(fn['meta'])['verified_assembly']
    assert proof['kind'] == 'sdk' and proof['from_c'] and proof['verification_method'] == 'sdk-signature'
    assert source_assembly.verified(proj, fn)
    assert source_assembly.breakdown(net, proj)['exact_c'] == 0


def test_turning_the_library_setting_off_keeps_the_scheduler_open(project):
    net, add = project
    add('__scExec')
    sdk_assembly.choose_c(net, 'game', False)
    assert sdk_assembly.discover_c(net, net.project_row('game'), threading.Event()) == 0
    assert statuses(net) == {'__scExec': 'open'}


def test_check_again_sets_aside_a_project_imported_earlier(project, monkeypatch):
    net, add = project
    add('__scExec')
    from decompnet.app import boundary_repairs

    monkeypatch.setattr(boundary_repairs, 'apply_entry_fixes', lambda *a: {'fixed': []})  # no cartridge here
    monkeypatch.setattr(net.console(net.project_row('game')), 'on_start', lambda *a, **k: None)
    said = net.recheck('game', log=lambda *_: None)
    assert '1 library function set aside, found by library detection' in said
    assert statuses(net) == {'__scExec': 'asm'}
