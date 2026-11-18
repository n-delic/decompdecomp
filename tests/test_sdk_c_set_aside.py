"""Library C the user's own SDK archive reproduces, set aside when the project chooses it (sdk_assembly.SETTING).

A homemade object, never SDK or game code: an N64 racing game had its Controller Pak functions named from the
user's libultra archive and still went to the agents for matching C, which a port replaces and a rebuild needn't.
"""
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app import sdk_assembly, source_assembly
from decompnet.app.engine import RomNetwork
from decompnet.rom import match
from decompnet.rom.symbols import SymbolTable

TOY = '''.text
.set noreorder
.globl osToyRead
.ent osToyRead
osToyRead:
 lui $t0,%hi(external)
 lw $v0,%lo(external)($t0)
 jr $ra
 xori $v0,$v0,3
.end osToyRead
'''


@pytest.fixture
def example(tmp_path, monkeypatch):
    if not toolchains.installed('binutils-mips'):
        pytest.skip('Pinned assembler unavailable; no downloads')
    tools = toolchains.path('binutils-mips')
    assembler, linker = next(tools.glob('*-as')), next(tools.glob('*-ld'))
    source = tmp_path / 'toy.s'
    source.write_text(TOY)
    obj = tmp_path / 'libtoy.a.o'
    subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(obj), str(source)], check=True, capture_output=True)
    layout = tmp_path / 'toy.ld'
    layout.write_text('SECTIONS { . = 0x80001000; .text : { *(.text) } } external = 0x80002000;')
    image = tmp_path / 'toy.elf'
    subprocess.run([str(linker), '-EB', '-T', str(layout), '-o', str(image), str(obj)], check=True, capture_output=True)
    with image.open('rb') as h:
        code = ELFFile(h).get_section_by_name('.text').data()[:16]
    net = RomNetwork(tmp_path / 'db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-G0']}}
    meta = {'sdk_display': {'name': 'osToyRead', 'fingerprint': 'x', 'corpora': ['Toy SDK']},
            'naming': {'method': 'sdk_signature', 'name': 'osToyRead', 'source_corpora': ['Toy SDK']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', 1, json.dumps(cfg))).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,target,relocs,status,meta) VALUES(?,?,?,?,?,?,?,?,?)',
                         (pid, 'osToyRead', 0x80001000, 16, 4, code, json.dumps([[0, 5, 0x80002000], [4, 6, 0x80002000]]),
                          'open', json.dumps(meta))).lastrowid
    monkeypatch.setattr(net, 'symbols', lambda _: SymbolTable())
    monkeypatch.setattr(net, '_asm_text', lambda *a: '')
    monkeypatch.setattr(net, 'reader', lambda *a: lambda addr, size: None)
    monkeypatch.setattr(net, '_rom_target', lambda fn: match.RomTarget(fn['name'], fn['vram'], bytes(fn['target']),
                                                                       json.loads(fn['relocs']), True))
    net.twins.import_sdk_objects(obj, 'Toy SDK')
    return net, fid


def status(net, fid):
    return net.project_store(1).one('SELECT status,meta FROM functions WHERE id=?', fid)


def test_on_by_default_and_a_project_that_turned_it_off_keeps_its_library_c_open(example):
    net, fid = example
    proj = net.project_row('game')
    assert sdk_assembly.SETTING not in json.loads(proj['config']) and sdk_assembly.enabled(net, proj)
    sdk_assembly.choose_c(net, 'game', False)
    assert sdk_assembly.discover_c(net, net.project_row('game')) == 0
    assert status(net, fid)['status'] == 'open'


def test_by_default_the_background_check_sets_library_c_aside(example):
    net, fid = example
    assert sdk_assembly.discover_c(net, net.project_row('game')) == 1  # nobody chose: background maintenance's call
    assert status(net, fid)['status'] == 'asm'


def test_on_sets_aside_linked_library_c_and_off_gives_it_back(example):
    net, fid = example
    sdk_assembly.choose_c(net, 'game', True)
    proj = net.project_row('game')
    seen = []
    assert sdk_assembly.discover_c(net, proj, threading.Event(), seen.append) == 1
    row = status(net, fid)
    proof = json.loads(row['meta'])['verified_assembly']
    assert row['status'] == 'asm' and proof['kind'] == 'sdk' and proof['from_c'] and proof['name'] == 'osToyRead'
    assert source_assembly.verified(proj, net._function('osToyRead', 'game'))
    assert source_assembly.breakdown(net, proj)['exact_c'] == 0  # never counted as matching C
    assert seen and seen[0]['total'] == 1
    assert sdk_assembly.set_aside_c(net, proj) == 1
    agent, _ = net.local_agent('worker')
    assert net.claim_row(agent, 'game') is None
    assert sdk_assembly.choose_c(net, 'game', False) == 1
    row = status(net, fid)
    assert row['status'] == 'open' and 'verified_assembly' not in json.loads(row['meta'])


def test_bytes_that_dont_link_are_never_called_linked(example, monkeypatch):
    """The archive's build differs: what sets the function aside is its signature identity, said as such."""
    net, fid = example
    sdk_assembly.choose_c(net, 'game', True)
    monkeypatch.setattr(net, '_rom_target', lambda f: match.RomTarget(f['name'], f['vram'], b'nope' + bytes(f['target'])[4:],
                                                                      json.loads(f['relocs']), True))
    assert sdk_assembly.discover_c(net, net.project_row('game'), threading.Event()) == 1
    proof = json.loads(status(net, fid)['meta'])['verified_assembly']
    assert proof['verification_method'] == 'sdk-signature' and 'object_sha256' not in proof


def test_reopened_and_claimed_functions_keep_their_c_work(example):
    net, fid = example
    sdk_assembly.choose_c(net, 'game', True)
    proj = net.project_row('game')
    agent, _ = net.local_agent('other')
    assert net.permuter._hold(agent, net._function('osToyRead', 'game'), count_turn=False, seconds=60)
    assert sdk_assembly.discover_c(net, proj, threading.Event()) == 0
    with net.project_store(1).tx() as db:
        db.execute('DELETE FROM claims')
        meta = json.loads(db.execute('SELECT meta FROM functions WHERE id=?', (fid,)).fetchone()['meta'])
        db.execute('UPDATE functions SET meta=? WHERE id=?', (json.dumps({**meta, 'reopened': True}), fid))
    assert sdk_assembly.discover_c(net, proj, threading.Event()) == 0
    assert status(net, fid)['status'] == 'open'


def test_archive_indexed_before_references_is_found_through_the_inventory(example):
    """Folders indexed before `sdk-object-reference:` records existed are skipped as unchanged on a rescan."""
    net, fid = example
    record = json.loads(net.store.one('SELECT value FROM settings WHERE key=?', sdk_assembly.PREFIX + 'Toy SDK')['value'])
    with net.store.tx() as db:
        db.execute('DELETE FROM settings WHERE key=?', (sdk_assembly.PREFIX + 'Toy SDK',))
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', ('sdk_library', json.dumps(
            {'folders': [], 'files': {record['path']: {'corpus': 'Toy SDK', 'sha256': record['sha256']}}})))
    assert sdk_assembly.reference(net, 'Toy SDK') == {'path': record['path'], 'sha256': record['sha256']}
    sdk_assembly.choose_c(net, 'game', True)
    assert sdk_assembly.discover_c(net, net.project_row('game'), threading.Event()) == 1


def test_changed_archive_is_parsed_again_and_never_linked(example, tmp_path):
    net, fid = example
    record = sdk_assembly.reference(net, 'Toy SDK')
    path = __import__('pathlib').Path(record['path'])
    assert list(sdk_assembly.objects(net, {'names': ['osToyRead'], 'corpora': ['Toy SDK']}))
    path.write_bytes(path.read_bytes() + b'changed')
    assert not list(sdk_assembly.objects(net, {'names': ['osToyRead'], 'corpora': ['Toy SDK']}))


def test_detection_alone_sets_aside_without_any_archive(example):
    """A user with no SDK: the console's built-in signatures find the function, and nothing can be linked."""
    net, fid = example
    with net.store.tx() as db:
        db.execute('DELETE FROM settings WHERE key=?', (sdk_assembly.PREFIX + 'Toy SDK',))
    assert sdk_assembly.reference(net, 'Toy SDK') is None
    sdk_assembly.choose_c(net, 'game', True)
    proj = net.project_row('game')
    assert sdk_assembly.discover_c(net, proj, threading.Event()) == 1
    fn = net._function('osToyRead', 'game')
    proof = json.loads(fn['meta'])['verified_assembly']
    assert fn['status'] == 'asm' and proof['verification_method'] == 'sdk-signature' and proof['from_c']
    assert 'not linked' in proof['evidence'] and proof['corpora'] == ['Toy SDK']
    assert source_assembly.verified(proj, fn)
    assert source_assembly.breakdown(net, proj)['exact_c'] == 0
    changed = dict(fn); changed['target'] = b'bad!' + bytes(fn['target'])[4:]
    assert not source_assembly.verified(proj, changed)


def test_linked_bytes_are_preferred_to_the_signature(example):
    net, fid = example
    sdk_assembly.choose_c(net, 'game', True)
    assert sdk_assembly.discover_c(net, net.project_row('game'), threading.Event()) == 1
    proof = json.loads(net._function('osToyRead', 'game')['meta'])['verified_assembly']
    assert 'verification_method' not in proof and proof['object_sha256']


def test_code_the_port_builds_from_c_is_never_a_lead(example):
    net, fid = example
    proj = net.project_row('game')
    fn = net._function('osToyRead', 'game')
    # libultra's math and synthesizer, which the N64 port compiles from the game's C, and names the kit doesn't know
    for name in ('guMtxF2L', 'alSynNew', '_n_loadBuffer', 'read32'):
        assert sdk_assembly._lead(net, proj, fn, {'names': [name], 'corpora': ['Toy SDK']}) is None
    assert sdk_assembly._lead(net, proj, fn, {'names': ['osPfsInitPak', 'osOther'], 'corpora': ['Toy SDK']}) is None
    assert sdk_assembly._lead(net, proj, fn, {'names': ['osPfsInitPak'], 'corpora': ['Toy SDK']}) == ('osPfsInitPak', ['Toy SDK'], None)


def test_sdk_code_is_its_own_event_and_map_state(example):
    """The activity log said "assembly-verified" for library code, the same as the game's own assembly."""
    net, fid = example
    sdk_assembly.choose_c(net, 'game', True)
    assert sdk_assembly.discover_c(net, net.project_row('game'), threading.Event()) == 1
    kinds = [(r['kind'], r['detail']) for r in net.project_store(1).all('SELECT kind,detail FROM events')]
    assert ('sdk-verified', 'osToyRead: osToyRead (linked against your SDK)') in kinds
    assert not any(k == 'assembly-verified' for k, _ in kinds)
    row = next(f for f in net.function_map('game') if f['id'] == fid)
    assert row['status'] == 'asm' and row['sdk_verified']


def test_the_gui_shows_sdk_apart_from_assembly():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    subprocess.run([node, str(Path(__file__).with_name('gui_sdk_category.js'))], check=True, capture_output=True,
                   text=True, timeout=60)


def test_the_setting_is_on_unless_the_project_turned_it_off():
    """No tools needed: the default every caller (background checks, Libraries panel) reads."""
    from types import SimpleNamespace
    for config, on in (({}, True), ({sdk_assembly.SETTING: True}, True), ({sdk_assembly.SETTING: False}, False)):
        net = SimpleNamespace(cfg=lambda proj, config=config: config)
        assert sdk_assembly.enabled(net, {}) is on
