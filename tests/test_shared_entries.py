from pathlib import Path
from types import SimpleNamespace

from decompnet.rom.shared_entries import intentional
from decompnet.rom.split import AsmLine, RomFunction, Segment


def function(name, address, rom, words, segment='one'):
    lines = [AsmLine(rom + 4*i, address + 4*i, word, mnemonic, '')
             for i, (word, mnemonic) in enumerate(words)]
    return RomFunction(name, address, rom, segment, lines, Path('unused'))


def test_independent_calls_prove_shared_entry_without_changing_its_extent():
    console = SimpleNamespace(arch='mips', big_endian=True, boundary_reason=lambda code: None)
    prefix = function('prefix', 0x80001000, 0x100, [(0x24840400, 'addiu')])
    tail = function('tail', 0x80001004, 0x104, [(0x03e00008, 'jr'), (0, 'nop')])
    caller = function('caller', 0x80002000, 0x200, [(0x0c000400, 'jal'), (0x0c000401, 'jal')])
    segments = [Segment('one', 0x100, 0x200, 0x80001000)]
    result = intentional([prefix, tail, caller], console, segments)
    assert result['prefix']['tail'] == 'tail'
    assert not intentional([prefix, tail, caller], console, segments + [Segment('overlay', 0x300, 0x400, 0x80001000)])
    assert prefix.size == 4
    assert not intentional([prefix, tail], console, segments)
    assert not intentional([prefix, tail, caller, function('alias', tail.vram, 0x304, [(0, 'nop')])], console, segments)
    assert not intentional([prefix, function('tail', tail.vram, tail.rom, [(0, 'nop')], 'other'), caller], console, segments)
    assert not intentional([function('prefix', prefix.vram, prefix.rom, [(0x10000000, 'beq')]), tail, caller], console, segments)
    console.boundary_reason = lambda code: 'incomplete'
    assert not intentional([prefix, tail, caller], console, segments)


def test_boundary_edits_invalidate_only_automatic_shared_reviews():
    import json
    import sqlite3

    from decompnet.app.boundary_repairs import _invalidate_shared_entries

    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE functions (project_id INTEGER, meta TEXT)')
    automatic = {'boundary_override': True, 'boundary_override_source': 'shared_entry', 'shared_entry': {'tail': 'tail'}}
    manual = {'boundary_override': True, 'boundary_decision': {'action': 'keep'}}
    db.executemany('INSERT INTO functions VALUES (?, ?)', [(1, json.dumps(automatic)), (1, json.dumps(manual)),
                                                          (2, json.dumps(automatic))])
    _invalidate_shared_entries(db, 1)
    rows = [json.loads(row[0]) for row in db.execute('SELECT meta FROM functions ORDER BY rowid')]
    assert rows == [{}, manual, automatic]


def test_shared_entry_explanation_does_not_assert_handwritten_assembly():
    import json

    from decompnet.app.boundary_repairs import reason

    console = SimpleNamespace(arch='mips', boundary_reason=lambda _: 'incomplete')
    row = {'status': 'open', 'target': b'\x24\x84\x04\x00',
           'meta': json.dumps({'boundary_override_source': 'shared_entry', 'shared_entry': {'tail': 'tail'}})}
    assert 'share this tail' in reason(console, row)
    assert row['status'] == 'open'


def test_refresh_keeps_automatic_shared_entry_out_of_unresolved_review(tmp_path):
    import json

    from decompnet.app import boundary_review
    from decompnet.app.engine import RomNetwork
    from decompnet.consoles import get

    net = RomNetwork(tmp_path / 'audit.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('game','Game','mips',?,'{}')", (str(tmp_path),)).lastrowid
        meta = {'boundary_override': True, 'boundary_override_source': 'shared_entry',
                'shared_entry': {'tail': 'tail'}, 'boundary_review': 'old warning'}
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,status,meta) VALUES(?,?,4,1,?,'open',?)",
                   (pid, 'prefix', bytes.fromhex('24840400'), json.dumps(meta)))
    boundary_review.refresh(net, {'id': pid}, get('n64'))
    row = net.store.one('SELECT status,meta FROM functions WHERE name=?', 'prefix')
    assert row['status'] == 'open'
    assert 'boundary_review' not in json.loads(row['meta'])


def test_argument_move_can_share_an_independently_called_tail():
    console = SimpleNamespace(arch='mips', big_endian=True, boundary_reason=lambda code: None)
    prefix = function('prefix', 0x80001000, 0x100, [(0x00802825, 'or')])
    tail = function('tail', 0x80001004, 0x104, [(0x03e00008, 'jr'), (0, 'nop')])
    caller = function('caller', 0x80002000, 0x200, [(0x0c000400, 'jal'), (0x0c000401, 'jal')])
    segments = [Segment('one', 0x100, 0x200, 0x80001000)]
    assert intentional([prefix, tail, caller], console, segments)['prefix']['tail'] == 'tail'
    for word in (0x03e02825, 0x0080f825, 0x00812825):
        prefix = function('prefix', 0x80001000, 0x100, [(word, 'or')])
        assert not intentional([prefix, tail, caller], console, segments)


def test_import_refresh_reproves_shared_entries_after_boundary_edits(tmp_path):
    import json

    from decompnet.app.boundary_review import refresh_shared_entries
    from decompnet.app.engine import RomNetwork
    from decompnet.consoles import get
    from decompnet.rom.split import function_asm

    net = RomNetwork(tmp_path / 'audit.db', tmp_path)
    prefix = function('prefix', 0x80001000, 0x100, [(0x00802825, 'or')])
    tail = function('tail', 0x80001004, 0x104, [(0x03e00008, 'jr'), (0, 'nop')])
    caller = function('caller', 0x80002000, 0x200, [(0x0c000400, 'jal'), (0x0c000401, 'jal')])
    segments = [Segment('one', 0x100, 0x200, 0x80001000)]
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('game','Game','mips',?,'{}')", (str(tmp_path),)).lastrowid
        for fn in (prefix, tail, caller):
            target = b''.join(line.word.to_bytes(4, 'big') for line in fn.lines)
            db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,status,unit,asm,meta) VALUES(?,?,?,?,?,?,?,'open',?,?,?)",
                       (pid, fn.name, fn.vram, fn.rom, fn.size, len(fn.lines), target, fn.segment, function_asm(fn), '{}'))
    refresh_shared_entries(net, {'id': pid}, get('n64'), segments)
    row = net.store.one('SELECT meta FROM functions WHERE name=?', 'prefix')
    meta = json.loads(row['meta'])
    assert meta['boundary_override_source'] == 'shared_entry'
    assert meta['shared_entry']['tail'] == 'tail'
    assert 'boundary_review' not in meta


def test_import_refresh_does_not_promote_an_unproven_piece(tmp_path):
    import json

    from decompnet.app.boundary_review import refresh_shared_entries
    from decompnet.app.engine import RomNetwork
    from decompnet.consoles import get
    from decompnet.rom.split import function_asm

    net = RomNetwork(tmp_path / 'audit.db', tmp_path)
    prefix = function('prefix', 0x80001000, 0x100, [(0x00802825, 'or')])
    tail = function('tail', 0x80001004, 0x104, [(0x03e00008, 'jr'), (0, 'nop')])
    caller = function('caller', 0x80002000, 0x200, [(0x0c000400, 'jal'), (0x0c000401, 'jal')])
    segments = [Segment('one', 0x100, 0x200, 0x80001000)]
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('game','Game','mips',?,'{}')", (str(tmp_path),)).lastrowid
        for fn in (prefix, tail, caller):
            target = b''.join(line.word.to_bytes(4, 'big') for line in fn.lines)
            db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,status,unit,asm,meta) VALUES(?,?,?,?,?,?,?,'open',?,?,?)",
                       (pid, fn.name, fn.vram, fn.rom, fn.size, len(fn.lines), target, fn.segment, function_asm(fn), '{}'))
    prefix.piece = True
    refresh_shared_entries(net, {'id': pid}, get('n64'), segments, [prefix,tail,caller])
    meta = json.loads(net.store.one('SELECT meta FROM functions WHERE name=?', 'prefix')['meta'])
    assert not meta.get('shared_entry')
    assert meta.get('boundary_review')


def test_non_mips_import_does_not_assume_mips_function_shapes(monkeypatch):
    from decompnet.app import boundary_review

    monkeypatch.setattr(boundary_review, 'refresh', lambda *args: 7)
    console = SimpleNamespace(arch='arm')
    assert boundary_review.refresh_shared_entries(None, {}, console, [], [object()]) == 7


def test_long_straight_prefix_and_called_tail_keep_both_entries():
    console = SimpleNamespace(arch='mips', big_endian=True, boundary_reason=lambda code: None)
    prefix = function('prefix', 0x80001000, 0x100, [(0x8c820000, 'lw'), (0xac820000, 'sw'),
                                                        (0x000a5880, 'sll'), (0x016a5821, 'addu'),
                                                        (0x016a5823, 'subu'), (0x35ef0006, 'ori')])
    tail = function('tail', 0x80001018, 0x118, [(0x03e00008, 'jr'), (0, 'nop')])
    caller = function('caller', 0x80002000, 0x200, [(0x0c000400, 'jal'), (0x0c000406, 'jal')])
    segments = [Segment('one', 0x100, 0x200, 0x80001000)]
    assert intentional([prefix, tail, caller], console, segments)['prefix']['tail'] == 'tail'
    branch = function('prefix', 0x80001000, 0x100, [(0x10000000, 'beq'), *[(0, 'nop')]*5])
    assert not intentional([branch, tail, caller], console, segments)
