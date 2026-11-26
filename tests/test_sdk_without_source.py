"""A library no public source builds is set aside on detection alone (`Console.sdk_without_source`).

An N64 racing game's n_audio functions were named from exact signatures, but the port counts them as C it compiles
(`PortKit.sdk_group` "native"), and a native function waits for ultralib's C, which has no n_audio: agents and the
deep permuter spent attempts on them. Synthetic bytes; no tools, SDK or game needed.
"""
import json
import threading

import pytest

from decompnet import consoles
from decompnet.app import sdk_assembly, source_assembly
from decompnet.app.engine import RomNetwork
from decompnet.rom.symbols import SymbolTable

NAUDIO = 'built in / n_audio 2.0J-2.0L'
ARCHIVE = 'lib 2.0K / libgn_audio.a / sha256:00'
ULTRALIB = 'built in / decompals/ultralib e24c836796df'


@pytest.fixture
def project(tmp_path, monkeypatch):
    """(net, add): `add(name, corpus)` an open function library detection named from `corpus`."""
    net = RomNetwork(tmp_path / 'db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': []}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', 1, json.dumps(cfg))).lastrowid
    monkeypatch.setattr(net, 'symbols', lambda _: SymbolTable())
    hints = {}
    monkeypatch.setattr(net, 'sdk_functions', lambda proj: hints)

    def add(name, corpus):
        with net.store.tx() as db:
            fid = db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,target,relocs,status) '
                             'VALUES(?,?,?,?,?,?,?,?)', (pid, name, 0x80001000 + 64 * len(hints), 64, 16,
                                                         bytes([len(hints) + 1]) * 64, '[]', 'open')).lastrowid
        hints[fid] = {'names': [name], 'corpora': [corpus], 'fingerprint': name,
                      'evidence': 'exact relocation-normalized SDK signature'}
        return fid

    return net, add


def statuses(net):
    return {r['name']: r['status'] for r in net.project_store(1).all('SELECT name,status FROM functions')}


def test_only_the_n64s_n_audio_has_no_source():
    n64 = consoles.get('n64')
    assert n64.sdk_without_source([NAUDIO]) and n64.sdk_without_source([ARCHIVE])
    assert not n64.sdk_without_source([ULTRALIB]) and not n64.sdk_without_source([NAUDIO, ULTRALIB])
    assert not n64.sdk_without_source(['libultra 2.0H / LIBGULTRA.A / sha256:00', 'confirmed by the user'])
    assert not consoles.get('ps1').sdk_without_source([NAUDIO])


def test_n_audio_is_set_aside_whatever_the_port_calls_the_name(project, monkeypatch):
    """`n_alSeqpNew` is "native" to the port and `_n_loadBuffer` unknown to it: both come from n_audio."""
    from decompnet.port import ultralib

    monkeypatch.setattr(ultralib, 'root', lambda: None)
    net, add = project
    add('n_alSeqpNew', NAUDIO)
    add('_n_loadBuffer', ARCHIVE)
    add('alSynNew', ULTRALIB)  # ultralib has its C: it waits for the pack, as before
    proj = net.project_row('game')
    assert sdk_assembly.ultralib_needed(net, proj) == []  # recorded identities only, none here
    assert sdk_assembly.discover_c(net, proj, threading.Event()) == 2
    assert statuses(net) == {'n_alSeqpNew': 'asm', '_n_loadBuffer': 'asm', 'alSynNew': 'open'}
    fn = net._function('n_alSeqpNew', 'game')
    proof = json.loads(fn['meta'])['verified_assembly']
    assert proof['kind'] == 'sdk' and proof['verification_method'] == 'sdk-signature' and 'port_source' not in proof
    assert source_assembly.verified(proj, fn)


def test_one_function_alone_for_the_deep_permuter(project):
    net, add = project
    first = add('n_alFxNew', NAUDIO)
    add('n_alFxPull', NAUDIO)
    assert sdk_assembly.discover_c(net, net.project_row('game'), function_id=first) == 1
    assert statuses(net) == {'n_alFxNew': 'asm', 'n_alFxPull': 'open'}


def test_check_again_sets_aside_a_project_imported_earlier(project, monkeypatch):
    """Already named (a restored save keeps names, not how they were found), so only detection can say."""
    net, add = project
    add('n_alSynNew', NAUDIO)
    from decompnet.app import boundary_repairs

    monkeypatch.setattr(boundary_repairs, 'apply_entry_fixes', lambda *a: {'fixed': []})  # no cartridge here
    monkeypatch.setattr(net.console(net.project_row('game')), 'on_start', lambda *a, **k: None)
    said = net.recheck('game', log=lambda *_: None)
    assert '1 library function set aside, found by library detection' in said
    assert statuses(net) == {'n_alSynNew': 'asm'}
