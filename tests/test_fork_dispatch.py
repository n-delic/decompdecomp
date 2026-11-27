"""A reference-only dispatch entry stays assembly until its reference changes."""
import json

import pytest

from decompnet import consoles
from decompnet.app.engine import RomNetwork
from decompnet.forks.assembly_evidence import dispatches, padding_only
from tests.mips_elf_fixture import linked_object


def test_lone_nop_is_deferred_as_possible_padding(tmp_path):
    obj = tmp_path / 'target.o'
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('00000000')))
    assert padding_only(obj)
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('03e0000800000000')))
    assert not padding_only(obj)
    obj.write_bytes(b'not an object')
    assert not padding_only(obj)


@pytest.mark.parametrize('body, expected', [
    ('3c0910000120000824080100', True),
    ('3c0910000120000800004025', True),
    ('3c0910010120000824080100', False),
    ('3c0910000120000824090100', False),
    ('3c09100003e0000824080100', False),
])
def test_fixed_dispatch_requires_exact_calling_convention(tmp_path, body, expected):
    obj = tmp_path / 'target.o'
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex(body)))
    assert bool(dispatches(consoles.get('n64'), tmp_path, obj)) is expected


def test_reference_requires_a_complete_non_sdk_dispatch(tmp_path):
    obj = tmp_path / 'target.o'
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('0000000824080100')))
    console = consoles.get('n64')
    assert dispatches(console, tmp_path, obj)['entry']['bytes'] == 8
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('03e0000800000000')))
    assert dispatches(console, tmp_path, obj) == {}
    obj.write_bytes(b'not an object')
    assert dispatches(console, tmp_path, obj) == {}


def test_sync_sets_aside_source_less_dispatch_and_refreshes_reference(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    obj = root / 'target.o'
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('0000000824080100')))
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name}]}))
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    report = {'measures': {'total_functions': 1, 'matched_functions': 0, 'total_code': '8', 'matched_code': '0'},
              'units': [{'name': 'unit', 'functions': [{'name': 'entry', 'size': '8'}]}]}
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='entry'")
    assert row['status'] == 'asm' and json.loads(row['meta'])['fork_dispatch']['symbol'] == 'entry'
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('03e0000800000000')))
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='entry'")
    assert row['status'] == 'nosource' and 'asm_code' not in json.loads(row['meta'])
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('0000000824080100')))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta=json_set(meta, '$.reopened', 1) WHERE id=?", (row['id'],))
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='entry'")
    assert row['status'] == 'nosource' and 'fork_dispatch' not in json.loads(row['meta'])


def test_sync_preserves_claims_and_matches(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    obj = root / 'target.o'
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('03e0000800000000')))
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name,
                                                             'metadata': {'source_path': 'source.c'}}]}))
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    fn = {'name': 'entry', 'size': '8'}
    report = {'measures': {'total_functions': 1, 'matched_functions': 0, 'total_code': '8', 'matched_code': '0'},
              'units': [{'name': 'unit', 'functions': [fn]}]}
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='entry'")
    assert row['status'] == 'open'
    with net.store.tx() as db:
        db.execute('INSERT INTO claims(function_id,agent_id) VALUES(?,?)', (row['id'], 1))
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex('0000000824080100')))
    net.forks.sync('p', rep=report, log=lambda *_: None)
    assert net.store.one("SELECT status FROM functions WHERE name='entry'")['status'] == 'open'
    with net.store.tx() as db:
        db.execute('DELETE FROM claims WHERE function_id=?', (row['id'],))
    fn['fuzzy_match_percent'] = 100
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='entry'")
    assert row['status'] == 'matched' and 'fork_dispatch' not in json.loads(row['meta'])


@pytest.mark.parametrize('body', ['0000000824080100', '3c0910000120000824080100'])
def test_autopilot_does_not_scaffold_an_assembly_only_unit(tmp_path, monkeypatch, body):
    from decompnet.forks.project import ForkProject

    root = tmp_path / 'project'
    root.mkdir()
    obj = root / 'target.o'
    obj.write_bytes(linked_object('entry', 0x80001000, bytes.fromhex(body)))
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name}]}))
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    report = {'measures': {'total_functions': 1, 'matched_functions': 0, 'total_code': '8', 'matched_code': '0'},
              'units': [{'name': 'unit', 'functions': [{'name': 'entry', 'size': str(len(bytes.fromhex(body)))}]}]}
    monkeypatch.setattr(ForkProject, 'report', lambda self: report)
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug: None)
    monkeypatch.setattr('decompnet.app.forkengine.toolchains.installed', lambda name: {'id': name})
    assert net.forks.autopilot_prepare('p', all_units=True, progress=lambda *args: None) == []
    assert net.store.one("SELECT status FROM functions WHERE name='entry'")['status'] == 'asm'
    assert 'source_path' not in json.loads((root / 'objdiff.json').read_text())['units'][0].get('metadata', {})


def test_reference_rejects_relocations(tmp_path):
    import subprocess

    import pytest

    from decompnet import toolchains
    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs pinned MIPS assembler')
    assembler = next(toolchains.path('binutils-mips').glob('*-as'))
    source = tmp_path / 'entry.s'
    source.write_text('.text\n.set noreorder\n.globl entry\n.ent entry\nentry:\n'
                      'jr $zero\naddiu $t0,$zero,256\n.end entry\n.reloc entry, R_MIPS_32, external\n')
    obj = tmp_path / 'entry.o'
    subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(obj), str(source)],
                   capture_output=True, check=True)
    assert dispatches(consoles.get('n64'), tmp_path, obj) == {}
    source.write_text('.text\n.set noreorder\n.globl entry\n.ent entry\nentry:\n'
                      'nop\n.end entry\n.reloc entry, R_MIPS_32, external\n')
    subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(obj), str(source)],
                   capture_output=True, check=True)
    assert not padding_only(obj)
