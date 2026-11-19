"""An N64 project whose library detection named libultra C (the port's "native" math and synthesizer) requires the
`ultralib-source` pack like its compiler: without it those functions can't be set aside, and agents and deep passes
spent whole budgets on SDK code (an N64 game's guMtxIdent, guTranslateF and guPosition). Homemade data; nothing is
downloaded: installs are stubbed."""
import json

import pytest

from decompnet import toolchains
from decompnet.app import sdk_assembly
from decompnet.app.engine import RomNetwork
from decompnet.port import ultralib


def _meta(name):
    return json.dumps({'naming': {'method': 'sdk_signature', 'name': name, 'source_corpora': ['built in']}})


@pytest.fixture
def game(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True,
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-G0']}, 'rom': {}}
    rows = [('guMtxIdent', 'open', _meta('guMtxIdent')),          # libultra math: the port builds ultralib's C
            ('guTranslateF', 'matched', _meta('guTranslateF')),   # already matched: not waiting
            ('osContStartReadData', 'open', _meta('osContStartReadData')),  # replaced by the port: no pack needed
            ('func_80001000', 'open', '{}')]                       # the game's own code
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', 1, json.dumps(cfg))).lastrowid
        for i, (name, status, meta) in enumerate(rows):
            db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,target,status,meta) VALUES(?,?,?,?,?,?,?,?)',
                       (pid, name, 0x80001000 + 0x40 * i, 16, 4, b'\0' * 16, status, meta))
    monkeypatch.setattr(ultralib, 'root', lambda: None)
    return net, net._project('game')


def test_only_libultra_c_waiting_for_the_pack_is_needed(game, monkeypatch, tmp_path):
    net, proj = game
    assert sdk_assembly.ultralib_needed(net, proj) == ['guMtxIdent']
    monkeypatch.setattr(ultralib, 'root', lambda: tmp_path)  # installed: discover_c sets it aside on its own
    assert sdk_assembly.ultralib_needed(net, proj) == []


def test_not_needed_when_the_library_setting_is_off(game):
    net, proj = game
    net._update_cfg('game', lambda cfg: cfg.__setitem__(sdk_assembly.SETTING, False))
    assert sdk_assembly.ultralib_needed(net, net._project('game')) == []


def test_the_overview_names_the_pack_and_what_waits(game):
    net, proj = game
    pack = net._library_pack(proj)
    assert pack['id'] == ultralib.PACK and pack['proprietary'] is True
    assert pack['functions'] == 1 and pack['examples'] == ['guMtxIdent']


def test_install_all_takes_the_pack_only_with_consent(game, monkeypatch):
    net, _ = game
    installs = []
    monkeypatch.setattr(toolchains, 'installed', lambda tid: None if tid == ultralib.PACK else '/stub')
    monkeypatch.setattr(toolchains, 'install', lambda tid, **kw: installs.append((tid, kw.get('accept_license'))))
    left = net.install_compilers('game', False, retest=False, log=lambda *_: None)
    assert ultralib.PACK in left['left_out'] and not installs
    done = net.install_compilers('game', True, retest=False, log=lambda *_: None)
    assert (ultralib.PACK, True) in installs and ultralib.PACK in done['installed']
