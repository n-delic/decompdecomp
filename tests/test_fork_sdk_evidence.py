"""Project-mode SDK proofs require exact complete bodies without relocations."""

import json
import subprocess

import pytest

from decompnet import consoles, toolchains
from decompnet.app import source_assembly
from decompnet.forks.sdk_evidence import identities, reverify


def test_kmc_runtime_identity_requires_complete_reference_bytes(tmp_path, monkeypatch):
    import hashlib

    from decompnet.consoles.n64 import sdk_assembly
    from tests.mips_elf_fixture import linked_object

    # Use synthetic bytes so tests do not redistribute an SDK routine.
    code = bytes(range(56))
    monkeypatch.setitem(sdk_assembly.KMC_ASSEMBLY, hashlib.sha256(code).hexdigest(), ('__muldi3', 56, 'test'))
    obj = tmp_path / 'target.o'
    obj.write_bytes(linked_object('entry', 0x80001000, code))
    proof = identities(consoles.get('n64'), tmp_path, obj)['entry']
    assert proof['kind'] == 'sdk' and proof['name'] == '__muldi3'
    assert proof['corpus'] == 'KMC handwritten runtime'
    obj.write_bytes(linked_object('entry', 0x80001000, code[:-1] + b'\xff'))
    assert identities(consoles.get('n64'), tmp_path, obj) == {}


def _object(tmp_path, *, reloc=False, changed=False):
    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs pinned MIPS binutils')
    assembler = next(toolchains.path('binutils-mips').glob('*-as'))
    source = tmp_path / 'sample.s'
    body = 'mfc0 $v1,$12' if changed else 'mfc0 $v0,$12'
    source.write_text('.text\n.set noreorder\n.globl sample\n.ent sample\nsample:\n ' + body +
                      '\n jr $ra\n nop\n.end sample\n' + ('.reloc sample, R_MIPS_32, external\n' if reloc else ''))
    obj = tmp_path / 'sample.o'
    subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(obj), str(source)],
                   capture_output=True, check=True)
    return obj


def test_sdk_reference_proof_rechecks_the_complete_object(tmp_path):
    obj = _object(tmp_path)
    console = consoles.get('n64')
    proof = identities(console, tmp_path, obj)['sample']
    assert proof['name'] == '__osGetSR' and proof['kind'] == 'sdk'
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name}]}))
    assert reverify(console, tmp_path, 'unit', 'sample', 12, proof)
    proj = {'root': str(tmp_path), 'config': json.dumps({'kind': 'fork', 'platform': 'n64'})}
    fn = {'status': 'asm', 'size': 12, 'unit': 'unit', 'meta': json.dumps({'symbol': 'sample', 'verified_assembly': proof})}
    assert source_assembly.verified(proj, fn)
    _object(tmp_path, changed=True)
    assert not source_assembly.verified(proj, fn)


def test_sdk_reference_rejects_relocations_and_non_elf(tmp_path):
    obj = _object(tmp_path, reloc=True)
    assert identities(consoles.get('n64'), tmp_path, obj) == {}
    obj.write_bytes(b'not an ELF object')
    assert identities(consoles.get('n64'), tmp_path, obj) == {}


@pytest.mark.parametrize('change', ['valid', 'missing', 'wrong_symbol', 'extra', 'wrong_instruction', 'wrong_arch'])
def test_relocated_entry_requires_its_complete_sdk_shape_and_paired_addresses(tmp_path, change):
    from decompnet.consoles.n64.sdk_assembly import _ENTRY_SHAPE

    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs pinned MIPS binutils')
    assembler = next(toolchains.path('binutils-mips').glob('*-as'))
    words = list(_ENTRY_SHAPE)
    if change == 'wrong_instruction':
        words[4] ^= 1
    pairs = [(0, 'R_MIPS_HI16', 'bss'), (4, 'R_MIPS_LO16', 'bss'),
             (8, 'R_MIPS_HI16', 'size'), (12, 'R_MIPS_LO16', 'size'),
             (40, 'R_MIPS_HI16', 'start'), (44, 'R_MIPS_LO16', 'start'),
             (48, 'R_MIPS_HI16', 'stack'), (56, 'R_MIPS_LO16', 'stack')]
    if change == 'missing':
        pairs.pop()
    elif change == 'wrong_symbol':
        pairs[-1] = (56, 'R_MIPS_LO16', 'start')
    elif change == 'extra':
        pairs.append((16, 'R_MIPS_32', 'start'))
    source = tmp_path / 'entry.s'
    source.write_text('.text\n.set noreorder\n.globl entry\n.ent entry\nentry:\n'
                      + ''.join(f'.word 0x{word:08x}\n' for word in words) + '.end entry\n'
                      + ''.join(f'.reloc entry+{at}, {kind}, {symbol}\n' for at, kind, symbol in pairs))
    obj = tmp_path / 'entry.o'
    subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(obj), str(source)],
                   capture_output=True, check=True)
    if change == 'wrong_arch':
        raw = bytearray(obj.read_bytes())
        raw[18:20] = (20).to_bytes(2, 'big')  # EM_PPC
        obj.write_bytes(raw)
    found = identities(consoles.get('n64'), tmp_path, obj)
    if change == 'valid':
        assert found['entry']['kind'] == 'sdk'
        assert found['entry']['name'] == 'entrypoint'
        (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name}]}))
        assert reverify(consoles.get('n64'), tmp_path, 'unit', 'entry', len(words) * 4, found['entry'])
        obj.write_bytes(obj.read_bytes() + b'changed reference')
        assert not reverify(consoles.get('n64'), tmp_path, 'unit', 'entry', len(words) * 4, found['entry'])
    else:
        assert found == {}


def test_sdk_reference_never_reads_a_target_outside_project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    outside = _object(tmp_path)
    assert identities(consoles.get('n64'), root, outside) == {}


def test_fork_sync_sets_aside_only_exact_sdk_and_reopens_when_reference_changes(tmp_path):
    from decompnet.app.engine import RomNetwork

    root = tmp_path / 'project'
    root.mkdir()
    obj = _object(root)
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name}]}))
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    report = {'measures': {'total_functions': 1, 'matched_functions': 0, 'total_code': '12', 'matched_code': '0'},
              'units': [{'name': 'unit', 'functions': [{'name': 'sample', 'size': '12',
                         'metadata': {'virtual_address': '2147487744'}}]}]}
    measures = net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='sample'")
    proof = json.loads(row['meta'])['verified_assembly']
    assert row['status'] == 'asm' and proof['kind'] == 'sdk'
    assert measures['set_aside_functions'] == 1
    assert source_assembly.verified(net.project_row('p'), row)
    _object(root, changed=True)
    assert not source_assembly.verified(net.project_row('p'), row)
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='sample'")
    assert row['status'] == 'nosource'
    assert 'verified_assembly' not in json.loads(row['meta'])


def test_fork_sync_keeps_existing_c_match_and_respects_user_reopen(tmp_path):
    from decompnet.app.engine import RomNetwork

    root = tmp_path / 'project'
    root.mkdir()
    obj = _object(root)
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name,
                                                               'metadata': {'source_path': 'source.c'}}]}))
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    function = {'name': 'sample', 'size': '12', 'fuzzy_match_percent': 100.0,
                'metadata': {'virtual_address': '2147487744'}}
    report = {'measures': {'total_functions': 1, 'matched_functions': 1, 'total_code': '12', 'matched_code': '12'},
              'units': [{'name': 'unit', 'functions': [function]}]}
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='sample'")
    assert row['status'] == 'matched' and 'verified_assembly' not in json.loads(row['meta'])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open', meta=json_set(meta, '$.reopened', 1) WHERE name='sample'")
    function.pop('fuzzy_match_percent')
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='sample'")
    assert row['status'] == 'open' and 'verified_assembly' not in json.loads(row['meta'])


def test_fork_sync_does_not_reclassify_an_agent_claim(tmp_path):
    from decompnet.app.engine import RomNetwork

    root = tmp_path / 'project'
    root.mkdir()
    obj = _object(root)
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name,
                                                               'metadata': {'source_path': 'source.c'}}]}))
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    function = {'name': 'sample', 'size': '12', 'metadata': {'virtual_address': '2147487744'}}
    report = {'measures': {'total_functions': 1, 'matched_functions': 0, 'total_code': '12', 'matched_code': '0'},
              'units': [{'name': 'unit', 'functions': [function]}]}
    # Start without a recognised body, then let an agent hold the open function.
    _object(root, changed=True)
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT * FROM functions WHERE name='sample'")
    assert row['status'] == 'open'
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta=json_set(meta, '$.not_c', 1) WHERE id=?", (row['id'],))
        db.execute('INSERT INTO claims(function_id,agent_id) VALUES(?,?)', (row['id'], 1))
    _object(root)
    net.forks.sync('p', rep=report, log=lambda *_: None)
    assert net.store.one("SELECT status FROM functions WHERE name='sample'")['status'] == 'open'
    with net.store.tx() as db:
        db.execute('DELETE FROM claims WHERE function_id=?', (row['id'],))
    net.forks.sync('p', rep=report, log=lambda *_: None)
    row = net.store.one("SELECT status,meta FROM functions WHERE name='sample'")
    assert row['status'] == 'asm' and 'not_c' not in json.loads(row['meta'])


def test_sdk_proof_rejects_a_changed_unit_reference(tmp_path):
    obj = _object(tmp_path)
    console = consoles.get('n64')
    proof = identities(console, tmp_path, obj)['sample']
    other = tmp_path / 'other.o'
    other.write_bytes(obj.read_bytes())
    config = tmp_path / 'objdiff.json'
    config.write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name}]}))
    assert reverify(console, tmp_path, 'unit', 'sample', 12, proof)
    config.write_text(json.dumps({'units': [{'name': 'unit', 'target_path': other.name}]}))
    assert not reverify(console, tmp_path, 'unit', 'sample', 12, proof)
    config.write_text(json.dumps({'units': [{'name': 'unit', 'target_path': obj.name},
                                            {'name': 'unit', 'target_path': other.name}]}))
    assert not reverify(console, tmp_path, 'unit', 'sample', 12, proof)


def test_autopilot_prepares_game_units_but_not_verified_sdk_units(tmp_path, monkeypatch):
    from decompnet.app.engine import RomNetwork
    from decompnet.forks.project import ForkProject

    root = tmp_path / 'project'
    root.mkdir()
    sdk = _object(root)
    refs = root / '.decomp-wtf/objdiff-references'
    (refs / 'objects').mkdir(parents=True)
    (refs / 'asm').mkdir()
    (refs / 'objects/sdk.o').write_bytes(sdk.read_bytes())
    assembler = next(toolchains.path('binutils-mips').glob('*-as'))
    game = root / 'game.s'
    game.write_text('.text\n.set noreorder\n.globl gamefn\n.ent gamefn\ngamefn:\n'
                    ' mfc0 $v1,$12\n jr $ra\n nop\n.end gamefn\n')
    subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(refs/'objects/game.o'), str(game)],
                   capture_output=True, check=True)
    (refs/'asm/sdk.s').write_text('glabel sample\n')
    (refs/'asm/game.s').write_text('glabel gamefn\n')
    (root/'objdiff.json').write_text(json.dumps({'units': [
        {'name': 'sdk', 'target_path': '.decomp-wtf/objdiff-references/objects/sdk.o'},
        {'name': 'game', 'target_path': '.decomp-wtf/objdiff-references/objects/game.o'},
    ]}))
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    report = {'measures': {'total_functions': 2, 'matched_functions': 0, 'total_code': '24', 'matched_code': '0'},
              'units': [{'name': 'sdk', 'functions': [{'name': 'sample', 'size': '12'}]},
                        {'name': 'game', 'functions': [{'name': 'gamefn', 'size': '12'}]}]}
    monkeypatch.setattr(ForkProject, 'report', lambda self: report)
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug: None)
    def build(self, unit, log=lambda *_: None):
        unit.base.write_bytes(b'candidate')
        return True, ''
    monkeypatch.setattr(ForkProject, 'build_unit', build)
    monkeypatch.setattr('decompnet.app.forkengine.toolchains.installed', lambda name: {'id': name})
    ids = net.forks.autopilot_prepare('p', all_units=True, progress=lambda *args: None)
    rows = {row['name']: row for row in net.store.all('SELECT * FROM functions')}
    assert rows['sample']['status'] == 'asm'
    assert rows['gamefn']['status'] == 'open' and ids == [rows['gamefn']['id']]
    units = json.loads((root/'objdiff.json').read_text())['units']
    assert 'source_path' not in (units[0].get('metadata') or {})
    assert units[1]['metadata']['source_path'].endswith('game.c')


def test_autopilot_defers_unresolved_functions_in_mixed_sdk_unit(tmp_path, monkeypatch):
    from decompnet.app.engine import RomNetwork
    from decompnet.forks.project import ForkProject

    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs pinned MIPS binutils')
    root = tmp_path / 'project'
    refs = root / '.decomp-wtf/objdiff-references'
    (refs / 'objects').mkdir(parents=True)
    (refs / 'asm').mkdir()
    assembler = next(toolchains.path('binutils-mips').glob('*-as'))
    for name, text in [('mixed', 'mfc0 $v0,$12'), ('game', 'addiu $v0,$a0,1')]:
        source = root / f'{name}.s'
        source.write_text('.text\n.set noreorder\n.globl first\n.ent first\nfirst:\n '
                          + text + '\n jr $ra\n nop\n.end first\n'
                          + ('.globl unknown\n.ent unknown\nunknown:\n addiu $v0,$a0,2\n jr $ra\n nop\n.end unknown\n'
                             if name == 'mixed' else ''))
        subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(refs/f'objects/{name}.o'), str(source)],
                       capture_output=True, check=True)
        (refs/f'asm/{name}.s').write_text('glabel first\n')
    (root/'objdiff.json').write_text(json.dumps({'units': [
        {'name': name, 'target_path': f'.decomp-wtf/objdiff-references/objects/{name}.o'}
        for name in ('mixed', 'game')]}))
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    report = {'measures': {'total_functions': 3, 'matched_functions': 0, 'total_code': '36', 'matched_code': '0'},
              'units': [{'name': 'mixed', 'functions': [{'name': 'first', 'size': '12'},
                                                        {'name': 'unknown', 'size': '12'}]},
                        {'name': 'game', 'functions': [{'name': 'gamefn', 'size': '12'}]}]}
    # Use a distinct symbol in the game report so the SDK entry is unambiguous.
    monkeypatch.setattr(ForkProject, 'report', lambda self: report)
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug: None)
    def build(self, unit, log=lambda *_: None):
        unit.base.write_bytes(b'candidate')
        return True, ''
    monkeypatch.setattr(ForkProject, 'build_unit', build)
    monkeypatch.setattr('decompnet.app.forkengine.toolchains.installed', lambda name: {'id': name})
    messages = []
    net.forks.autopilot_prepare('p', all_units=True, progress=lambda *args: messages.append(args))
    units = json.loads((root/'objdiff.json').read_text())['units']
    assert 'source_path' not in (units[0].get('metadata') or {})
    assert units[1]['metadata']['source_path'].endswith('game.c')
    rows = {row['name']: row for row in net.store.all('SELECT * FROM functions')}
    assert rows['first']['status'] == 'asm' and rows['unknown']['status'] == 'nosource'
    assert any('verified SDK assembly' in str(message) for message in messages)


def test_reference_proof_cache_uses_object_content(tmp_path, monkeypatch):
    import os

    from decompnet.forks import sdk_evidence

    obj = _object(tmp_path)
    console = consoles.get('n64')
    original = sdk_evidence.ELFFile
    parsed = []
    def counted(stream):
        parsed.append(True)
        return original(stream)
    monkeypatch.setattr(sdk_evidence, 'ELFFile', counted)
    first = identities(console, tmp_path, obj)
    assert first['sample']['name'] == '__osGetSR'
    assert identities(console, tmp_path, obj) == first and len(parsed) == 1
    first['sample']['name'] = 'changed by caller'
    assert identities(console, tmp_path, obj)['sample']['name'] == '__osGetSR' and len(parsed) == 1
    # A same-size rewrite with its old mtime must not reuse the original identity.
    stat = obj.stat()
    _object(tmp_path, changed=True)
    os.utime(obj, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert obj.stat().st_size == stat.st_size
    assert identities(console, tmp_path, obj) == {} and len(parsed) == 2
