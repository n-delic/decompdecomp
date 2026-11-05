"""Overlay drafts recover their own labels and entry names, then use the ordinary judge."""

import json
import struct
from types import SimpleNamespace

import pytest

from decompnet import seeds, toolchains
from decompnet.app import drafts
from decompnet.app.engine import RomNetwork, briefs
from decompnet.app.engine.common import BASE_HEADER
from decompnet.rom import split

ADDRESS = 0x80001000
NAME = 'func_80001000_ovl_01'
OLD_NAME = 'func_80001000_overlay_001'
WORDS = (0x10800003, 0, 0x03E00008, 0x24020001, 0x03E00008, 0x24020002)
# Homemade: return arg0 ? 1 : 2. Older imports lost the suffixed branch label.
ASSEMBLY = f'''glabel {OLD_NAME}
/* 0 80001000 10800003 */ beqz $a0, .L80001010_overlay_001
/* 4 80001004 00000000 */ nop
/* 8 80001008 03E00008 */ jr $ra
/* C 8000100C 24020001 */ addiu $v0, $zero, 1
/* 10 80001010 03E00008 */ jr $ra
/* 14 80001014 24020002 */ addiu $v0, $zero, 2
endlabel {OLD_NAME}'''


@pytest.mark.parametrize('suffix', ['', '_overlay_001', '_ovl_01', '_ovl_001'])
def test_overlay_labels_survive_import_and_stored_assembly_roundtrips(tmp_path, suffix):
    text = ASSEMBLY.replace('_overlay_001', suffix)
    text = text.replace('/* 10 ', f'.L80001010{suffix}:\n/* 10 ')
    path = tmp_path / 'function.s'
    path.write_text(text)
    parsed = split.parse_function(path, 'overlay_001')
    assert parsed.labels == {ADDRESS + 16}
    rendered = split.function_asm(parsed)
    assert 'beqz       $a0, .L80001010\n' in rendered
    assert '.L80001010:\n' in rendered
    restored = split.function_from_text(parsed.name, text, parsed.segment)
    assert split.function_asm(restored) == rendered
    assert [line.word for line in restored.lines] == list(WORDS)


def test_legacy_recovery_only_defines_labels_at_recorded_instructions():
    text = ASSEMBLY.replace('nop', 'b .L80001080_overlay_001', 1)
    repaired = split.draft_asm(text, NAME, ADDRESS)
    assert f'glabel {NAME}\n' in repaired and f'endlabel {NAME}' in repaired
    assert OLD_NAME not in repaired
    assert '.L80001010_overlay_001:\n/* 10 ' in repaired
    assert '.L80001080_overlay_001:' not in repaired
    assert split.draft_asm(repaired, NAME, ADDRESS) == repaired
    assert split.draft_asm(text, NAME, ADDRESS + 4) == text
    assert [m.groups() for line in text.splitlines() if (m := split._INSN.match(line))] == [
        m.groups() for line in repaired.splitlines() if (m := split._INSN.match(line))]


def test_entry_labels_and_recursive_names_follow_only_the_current_function():
    text = ASSEMBLY.replace('nop', f'jal {OLD_NAME}', 1)
    repaired = split.draft_asm(text, 'named_function', ADDRESS)
    assert 'jal named_function' in repaired
    assert split.draft_asm(repaired, 'named_function', ADDRESS) == repaired
    text = ASSEMBLY.replace('.L80001010_overlay_001', '.L80001000_overlay_001')
    assert '.L80001000_overlay_001:\n/* 0 ' in split.draft_asm(text, NAME, ADDRESS)


def test_legacy_jump_tables_can_supply_case_labels_absent_from_branch_instructions(monkeypatch):
    import subprocess
    from pathlib import Path

    table = '.section .rodata\ndlabel jtbl_80002000\n.word .L80001008\n.word .L80002004\n'
    def run(argv, **kwargs):
        asm = next(Path(arg).read_text() for arg in argv if arg.endswith(f'{NAME}.s'))
        assert '.L80001008:\n/* 8 ' in asm
        assert '.L80002004:' not in asm
        return subprocess.CompletedProcess(argv, 0, f'int {NAME}(void) {{ return 1; }}', '')
    monkeypatch.setattr(seeds, 'available', lambda: True)
    monkeypatch.setattr(seeds.subprocess, 'run', run)
    assert seeds.run_m2c(split.draft_asm(ASSEMBLY, NAME, ADDRESS), NAME, 'mips-gcc-c', data_asm=table)


def test_jump_table_recovery_does_not_rename_other_functions_in_project_files():
    text = ASSEMBLY + '\nglabel second_function\n/* 18 80001018 03E00008 */ jr $ra\n'
    assert split.draft_asm(text, 'second_function', data_asm='.word .L80001008') == text
    assert split.draft_asm(ASSEMBLY, NAME, data_asm='.word .L80001008') == ASSEMBLY


@pytest.fixture
def network(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    target = struct.pack('>6I', *WORDS)
    (tmp_path / 'rom.z64').write_bytes(target)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'gp': 0,
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']},
           'rom': {'file': 'rom.z64', 'sha1': 'homemade-overlay-demo'},
           'segments': [{'name': 'overlay_001', 'rom_start': 0, 'rom_end': len(target), 'vram': ADDRESS}]}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                         ('overlay-demo', 'Overlay demo', str(tmp_path), 'mips', 1, json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO context_versions(project_id,version,header) VALUES(?,?,?)',
                   (pid, 1, BASE_HEADER.format(title='Overlay demo')))
        db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,asm,seed,seed_key) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?)', (pid, NAME, ADDRESS, 0, len(target), 6, target, ASSEMBLY, '', 'old-key'))
        db.execute("UPDATE functions SET relocs='[]' WHERE project_id=?", (pid,))
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                   (pid, NAME, ADDRESS, len(target), 'func'))
    proj = net.project_row('overlay-demo')
    fn = net.store.one('SELECT * FROM functions WHERE project_id=?', pid)
    return net, proj, fn


def test_stale_drafts_use_complete_rows_and_invalidate_previous_failed_inputs(network, monkeypatch):
    net, proj, fn = network
    calls = []
    def generate(asm, name, *args, **kwargs):
        calls.append(asm)
        return f'int {name}(int arg0) {{ return arg0 ? 1 : 2; }}'
    monkeypatch.setattr(seeds, 'available', lambda: True)
    monkeypatch.setattr(seeds, 'generate_from_asm', generate)
    assert net.stale_drafts(proj) == [fn['id']]
    net._seed_for(fn, proj)
    assert not net.stale_drafts(proj)
    assert '.L80001010_overlay_001:' in calls[0] and OLD_NAME not in calls[0]
    # A later drafting fix must reopen cached failures without a header change.
    monkeypatch.setattr(briefs, 'DRAFT_REVISION', briefs.DRAFT_REVISION + 1)
    assert net.stale_drafts(proj) == [fn['id']]


def test_stale_drafts_keep_the_function_specific_overlay_compiler(network):
    net, proj, fn = network
    cfg = json.loads(proj['config'])
    cfg['function_compilers'] = {f"0:{ADDRESS}": {'vram': ADDRESS, 'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']}}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), proj['id']))
    proj = net.project_row(proj['slug'])
    key = net._draft_plan(fn, proj)[0]
    with net.store.tx() as db:
        db.execute('UPDATE functions SET seed=?,seed_key=? WHERE id=?', ('saved draft', key, fn['id']))
    assert not net.stale_drafts(proj)


@pytest.mark.parametrize('previous,current,joined,expected', [
    ('', 'int f(void) { return 1; }', False, 1),
    ('', '', False, 0),
    ('', 'int f(void) { return 1; }', True, 0),
    (None, 'int f(void) { return 1; }', False, 0),
])
def test_recovered_failed_drafts_get_a_compile_even_without_an_attempt(previous, current, joined, expected, monkeypatch):
    from decompnet.app import boundary_repairs
    monkeypatch.setattr(boundary_repairs, 'blocked', lambda *args: set())
    calls = []
    fn = dict(id=1, status='open', seed=previous)
    def one(query, *args):
        return fn if 'SELECT *' in query else None
    net = SimpleNamespace(store=SimpleNamespace(one=one), links={'demo': object()} if joined else {},
                          fork_slug=lambda slug: False, _seed_for=lambda *args: current,
                          _autopilot_one=lambda *args: calls.append(args) or 'far')
    state = drafts.State()
    drafts.Drafter(net)._one(SimpleNamespace(id=1), {'slug': 'demo'}, 1, state)
    assert len(calls) == state.retried == expected
    assert state.made == 1


@pytest.mark.skipif(not seeds.available() or toolchains.installed('gcc-2.7.2-kmc') is None,
                    reason='needs pinned m2c and the period compiler')
def test_recovered_overlay_drafts_compile_through_autopilot(network):
    net, proj, fn = network
    agent, _ = net.local_agent('autopilot')
    outcome = net._autopilot_one(agent, proj, fn['id'])
    assert outcome in ('match', 'near', 'far')
    row = net.store.one('SELECT seed,best_source,best_score FROM functions WHERE id=?', fn['id'])
    assert row['seed'] and row['best_source'] and row['best_score'] is not None
    assert net.store.one('SELECT compiled FROM attempts WHERE function_id=?', fn['id'])['compiled'] == 1
