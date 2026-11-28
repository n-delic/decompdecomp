"""Scoped recovery only publishes candidates after an isolated split preserves existing code."""

from pathlib import Path

import pytest
import yaml

from decompnet.consoles.n64 import runtime_staging


def test_staged_basename_flattens_title_slashes_without_allowing_escape(tmp_path):
    cfg = {'options': {'basename': 'game/edition'}, 'segments': []}
    runtime_staging._inputs(cfg, tmp_path, tmp_path)
    assert cfg['options']['basename'] == 'game_edition'
    for basename in ('../outside', '/absolute/path'):
        with pytest.raises(ValueError, match='basename points outside'):
            runtime_staging._inputs({'options': {'basename': basename}}, tmp_path, tmp_path)


def test_called_delay_slot_entry_requires_the_branch_and_exact_following_body(tmp_path):
    asm = tmp_path / 'asm' / 'nonmatchings' / 'unit'
    asm.mkdir(parents=True)
    first = asm / 'previous.s'
    first.write_text('glabel previous\n'
                     '/* 000000 80000000 10000001 */ b .L80000008\n'
                     'alabel entry\n'
                     '/* 000004 80000004 00000000 */ nop\n')
    (asm / 'tail.s').write_text('glabel tail\n'
                               '/* 000008 80000008 03E00008 */ jr $ra\n'
                               '/* 00000C 8000000C 00000000 */ nop\n')
    image = bytes.fromhex('10000001 00000000 03e00008 00000000')
    known = [{'name': 'entry', 'rom_offset': 4, 'vram': 0x80000004, 'size': 12}]
    functions = runtime_staging._functions(tmp_path)
    runtime_staging._delay_slot_entries(tmp_path, known, image, functions)
    assert functions[(4, 0x80000004)] == (12, image[4:])

    first.write_text(first.read_text().replace('10000001 */ b .L80000008', '24020001 */ addiu $v0, $zero, 1'))
    functions = runtime_staging._functions(tmp_path)
    runtime_staging._delay_slot_entries(tmp_path, known, image, functions)
    assert (4, 0x80000004) not in functions

    first.write_text(first.read_text().replace('24020001 */ addiu $v0, $zero, 1', '04080000 */ tgei $zero, 0'))
    functions = runtime_staging._functions(tmp_path)
    runtime_staging._delay_slot_entries(tmp_path, known, image, functions)
    assert (4, 0x80000004) not in functions  # REGIMM traps do not have branch delay slots.

    first.write_text(first.read_text().replace('04080000 */ tgei $zero, 0', '10000001 */ b .L80000008'))
    functions = runtime_staging._functions(tmp_path)
    runtime_staging._delay_slot_entries(tmp_path, known, image[:-4] + b'bad!', functions)
    assert (4, 0x80000004) not in functions


def test_staging_rejects_losing_an_existing_delay_slot_entry(tmp_path, monkeypatch):
    config = tmp_path / 'game.yaml'
    cfg = {'options': {'target_path': 'rom.z64'}, 'segments': []}
    config.write_text(yaml.safe_dump(cfg))
    image = bytes.fromhex('10000001 00000000 03e00008 00000000')
    known = [{'name': 'entry', 'rom_offset': 4, 'vram': 0x80000004, 'size': 12}]
    drop = [False]

    def splat(args, root, log, timeout):
        asm = root / 'asm' / 'nonmatchings' / 'unit'
        asm.mkdir(parents=True)
        label = '' if root.name == 'after' and drop[0] else 'alabel entry\n'
        (asm / 'previous.s').write_text('glabel previous\n'
                                        '/* 000000 80000000 10000001 */ b .L80000008\n'
                                        f'{label}'
                                        '/* 000004 80000004 00000000 */ nop\n')
        (asm / 'tail.s').write_text('glabel tail\n'
                                   '/* 000008 80000008 03E00008 */ jr $ra\n'
                                   '/* 00000C 8000000C 00000000 */ nop\n')

    monkeypatch.setattr(runtime_staging.split, '_splat', splat)
    prepare = lambda settings, candidates: (settings, [], known)
    cache = {}
    assert runtime_staging.verify(config, image, cfg, [], lambda *_: None, prepare=prepare, cache=cache)
    drop[0] = True
    conflicts = []
    assert not runtime_staging.verify(config, image, cfg, [], lambda *_: None, prepare=prepare, cache=cache,
                                      on_conflict=conflicts.extend)
    assert conflicts == known


def test_existing_internal_entry_requires_its_label_and_every_instruction(tmp_path):
    asm = tmp_path / 'asm' / 'nonmatchings' / 'unit'
    asm.mkdir(parents=True)
    first = asm / 'first.s'
    first.write_text('glabel first\n'
                     '/* 000000 80000000 24020001 */ addiu $v0, $zero, 1\n'
                     'alabel entry\n'
                     '/* 000004 80000004 24030002 */ addiu $v1, $zero, 2\n')
    tail = asm / 'tail.s'
    tail.write_text('glabel tail\n'
                    '/* 000008 80000008 03E00008 */ jr $ra\n'
                    '/* 00000C 8000000C 00000000 */ nop\n')
    image = bytes.fromhex('24020001 24030002 03e00008 00000000')
    known = [{'name': 'entry', 'rom_offset': 4, 'vram': 0x80000004, 'size': 12}]
    functions = runtime_staging._functions(tmp_path)
    runtime_staging._overlapping_entries(tmp_path, known, image, functions)
    assert functions[(4, 0x80000004)] == (12, image[4:])

    tail.write_text(tail.read_text().replace('03E00008', '03E00009'))
    functions = runtime_staging._functions(tmp_path)
    runtime_staging._overlapping_entries(tmp_path, known, image, functions)
    assert (4, 0x80000004) not in functions
    tail.unlink()
    functions = runtime_staging._functions(tmp_path)
    runtime_staging._overlapping_entries(tmp_path, known, image, functions)
    assert (4, 0x80000004) not in functions


def test_staging_compares_both_splits_without_writing_the_project(tmp_path, monkeypatch):
    config = tmp_path / 'game.yaml'
    original = {'options': {'target_path': 'rom.z64', 'symbol_addrs_path': ['symbol_addrs.txt']}, 'segments': []}
    config.write_text(yaml.safe_dump(original))
    (tmp_path / 'symbol_addrs.txt').write_text('entry = 0x80000000;\n')
    runs = []

    def splat(args, root, log, timeout):
        assert Path(root).is_relative_to(Path('/tmp')) or 'decomp-overlay-split-' in str(root)
        assert (root / 'rom.z64').read_bytes() == b'abcdefgh'
        assert (root / 'symbol_addrs.txt').is_file()
        runs.append(root.name)

    def functions(root):
        old = {(0, 0x80000000): (4, b'abcd')}
        if root.name == 'after':
            old[(4, 0x80000004)] = (4, b'efgh')
        return old

    monkeypatch.setattr(runtime_staging.split, '_splat', splat)
    monkeypatch.setattr(runtime_staging, '_functions', functions)
    candidate = {'rom_start': 4, 'rom_end': 8, 'vram': 0x80000004}
    assert runtime_staging.verify(config, b'abcdefgh', original, [candidate], lambda *_: None)
    assert runs == ['before', 'after']
    assert yaml.safe_load(config.read_text()) == original
    monkeypatch.setattr(runtime_staging, '_functions', lambda root: functions(root) if root.name == 'before' else {})
    assert not runtime_staging.verify(config, b'abcdefgh', original, [candidate], lambda *_: None)


def test_staging_refuses_paths_outside_the_project(tmp_path, monkeypatch):
    config = tmp_path / 'game.yaml'
    cfg = {'options': {'target_path': 'rom.z64', 'symbol_addrs_path': ['../outside']}, 'segments': []}
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(runtime_staging.split, '_splat', lambda *a, **kw: pytest.fail('unsafe split ran'))
    assert not runtime_staging.verify(config, b'abcdefgh', cfg, [], lambda *_: None)


def test_staging_refuses_segment_paths_outside_the_project(tmp_path, monkeypatch):
    config = tmp_path / 'game.yaml'
    cfg = {'options': {'target_path': 'rom.z64'}, 'segments': [
        {'name': 'bank', 'type': 'code', 'start': 0, 'vram': 0x80000000,
         'subsegments': [{'start': 0, 'type': 'c', 'path': '../outside.c'}]}, [8]]}
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(runtime_staging.split, '_splat', lambda *a, **kw: pytest.fail('unsafe split ran'))
    assert not runtime_staging.verify(config, b'abcdefgh', cfg, [], lambda *_: None)


def test_staging_uses_export_entries_instead_of_unlisted_splat_fragments(tmp_path, monkeypatch):
    config = tmp_path / 'game.yaml'
    cfg = {'options': {'target_path': 'rom.z64'}, 'segments': []}
    config.write_text(yaml.safe_dump(cfg))
    calls = []

    def prepare(settings, candidates):
        calls.append(len(candidates))
        known = [{'rom_offset': 0, 'vram': 0x80000000, 'size': 4}]
        return settings, ['existing = 0x80000000; // type:func'], known

    def splat(args, root, log, timeout):
        assert 'existing' in (root / 'symbol_addrs.txt').read_text()

    def functions(root):
        result = {(0, 0x80000000): (4, b'abcd')}
        if root.name == 'before':
            result[(2, 0x80000002)] = (2, b'cd')  # not an entry in the project's database
        else:
            result[(4, 0x80000004)] = (4, b'efgh')
        return result

    monkeypatch.setattr(runtime_staging.split, '_splat', splat)
    monkeypatch.setattr(runtime_staging, '_functions', functions)
    candidate = {'rom_start': 4, 'rom_end': 8, 'vram': 0x80000004}
    assert runtime_staging.verify(config, b'abcdefgh', cfg, [candidate], lambda *_: None, prepare=prepare)
    assert calls == [0, 1]

    def missing_known(root):
        result = functions(root)
        if root.name == 'after':
            result.pop((0, 0x80000000))
        return result

    monkeypatch.setattr(runtime_staging, '_functions', missing_known)
    conflicts, errors = [], []
    assert not runtime_staging.verify(config, b'abcdefgh', cfg, [candidate], lambda *_: None, prepare=prepare,
                                      on_conflict=conflicts.extend, on_error=errors.append)
    assert [(row['rom_offset'], row['vram']) for row in conflicts] == [(0, 0x80000000)]
    assert errors == ['the staged split changed or removed an existing function']


def test_staging_refuses_to_ignore_an_existing_entry_missing_from_both_splits(tmp_path, monkeypatch):
    config = tmp_path / 'game.yaml'
    cfg = {'options': {'target_path': 'rom.z64'}, 'segments': []}
    config.write_text(yaml.safe_dump(cfg))
    known = [{'name': 'missing', 'rom_offset': 4, 'vram': 0x80000004, 'size': 4}]
    prepare = lambda settings, candidates: (settings, [], known)
    monkeypatch.setattr(runtime_staging.split, '_splat', lambda *a, **kw: None)
    monkeypatch.setattr(runtime_staging, '_functions', lambda root: {(0, 0x80000000): (4, b'abcd')})
    conflicts = []
    assert not runtime_staging.verify(config, b'abcdefgh', cfg, [], lambda *_: None, prepare=prepare,
                                      on_conflict=conflicts.extend)
    assert conflicts == known
    monkeypatch.setattr(runtime_staging, '_functions', lambda root: {
        (0, 0x80000000): (4, b'abcd'),
        **({(4, 0x80000004): (4, b'efgh')} if root.name == 'after' else {}),
    })
    assert runtime_staging.verify(config, b'abcdefgh', cfg, [], lambda *_: None, prepare=prepare)


def test_failed_batch_is_subdivided_and_safe_bodies_are_checked_together():
    tested = []

    def valid(items):
        tested.append(tuple(items))
        return 'bad' not in items and not ({'a', 'c'} <= set(items))

    accepted, deferred = runtime_staging.isolate([['a', 'bad'], ['c', 'd']], valid, lambda *_: None)
    assert accepted == ['a', 'd']
    assert deferred == ['bad', 'c']
    assert ('a', 'c') in tested and ('a', 'd') in tested


def test_isolation_checks_cancellation_between_groups():
    class Log:
        checks = 0

        def __call__(self, *_):
            pass

        def check_cancel(self):
            self.checks += 1
            if self.checks == 2:
                raise RuntimeError('stopped')

    with pytest.raises(RuntimeError, match='stopped'):
        runtime_staging.isolate([['a'], ['b']], lambda items: True, Log())


def test_a_search_reuses_its_baseline_but_rechecks_changed_inputs(tmp_path, monkeypatch):
    config = tmp_path / 'game.yaml'
    cfg = {'options': {'target_path': 'rom.z64', 'symbol_addrs_path': ['symbols.txt']}, 'segments': []}
    config.write_text(yaml.safe_dump(cfg))
    symbols = tmp_path / 'symbols.txt'
    symbols.write_text('first = 0x80000000;\n')
    runs = []
    monkeypatch.setattr(runtime_staging.split, '_splat', lambda args, root, log, timeout: runs.append(root.name))
    monkeypatch.setattr(runtime_staging, '_functions', lambda root: {(0, 0x80000000): (4, b'code')})
    cache = {}
    assert runtime_staging.verify(config, b'code', cfg, [], lambda *_: None, cache=cache)
    assert runtime_staging.verify(config, b'code', cfg, [], lambda *_: None, cache=cache)
    assert runs == ['before', 'after', 'after']
    symbols.write_text('second = 0x80000000;\n')
    assert runtime_staging.verify(config, b'code', cfg, [], lambda *_: None, cache=cache)
    assert runs == ['before', 'after', 'after', 'before', 'after']
def test_stored_assembly_requires_exact_labeled_bytes(tmp_path):
    from decompnet.consoles.n64 import runtime_staging

    asm = tmp_path / 'asm'
    asm.mkdir()
    file = asm / 'entry.s'
    file.write_text('glabel existing\n/* 0000 80000000 03E00008 */ jr $ra\n'
                    '/* 0004 80000004 00000000 */ nop\nendlabel existing\n')
    row = {'name': 'existing', 'status': 'asm', 'rom_offset': 0, 'vram': 0x80000000, 'size': 8}
    image = bytes.fromhex('03e0000800000000')
    found = {}
    runtime_staging._stored_assembly(tmp_path, [row], image, found)
    assert found[(0, 0x80000000)] == (8, image)
    file.write_text('dlabel existing\n/* 0000 80000000 03E00008 */ .word 0x03E00008\n'
                    '/* 0004 80000004 00000000 */ .word 0x00000000\nenddlabel existing\n')
    found = {}
    runtime_staging._stored_assembly(tmp_path, [row], image, found)
    assert found[(0, 0x80000000)] == (8, image)
    for altered in (file.read_text().replace('0x03E00008', '0x03E00009'),
                    file.read_text().replace('80000004', '80000008'),
                    file.read_text().replace('dlabel existing', 'dlabel another')):
        file.write_text(altered)
        found = {}
        runtime_staging._stored_assembly(tmp_path, [row], image, found)
        assert not found
    file.write_text('dlabel existing\n/* 0000 80000000 03E00008 */ .word 0x03E00008\n'
                    '/* 0004 80000004 00000000 */ .word 0x00000000\n')
    found = {}
    runtime_staging._stored_assembly(tmp_path, [row | {'status': 'open'}], image, found)
    assert not found


