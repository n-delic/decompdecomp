"""Boundary problems the bytes settle are applied at import and Check again through the same guarded path as a
person's repair, recorded as automatic and undoable (app/boundary_repairs.py, apply_entry_fixes)."""
import json
import struct
import time

import pytest

from decompnet.app import boundary_repairs as repair
from decompnet.app.engine import RomNetwork

BASE = 0x80001000
NOP, JR_RA, BREAK7 = 0, 0x03E00008, 0x0007000D
SP, RA, A0, V0 = 29, 31, 4, 2


def addiu(rt, rs, imm):
    return 0x24000000 | rs << 21 | rt << 16 | imm & 0xFFFF


def lw(rt, base, off):
    return 0x8C000000 | base << 21 | rt << 16 | off & 0xFFFF


def sw(rt, base, off):
    return 0xAC000000 | base << 21 | rt << 16 | off & 0xFFFF


def jal(address):
    return 0x0C000000 | address >> 2 & 0x3FFFFFF


def j(address):
    return 0x08000000 | address >> 2 & 0x3FFFFFF


def body(frame=0x18):
    return [addiu(SP, SP, -frame), sw(RA, SP, 0x10), jal(0x80023D58), addiu(A0, 0, 0x14E),
            lw(RA, SP, 0x10), JR_RA, addiu(SP, SP, frame)]


def calling(address):
    """A compiled function that calls `address`: it saves $ra first, as compiled code always does."""
    return [addiu(SP, SP, -0x18), sw(RA, SP, 0x10), jal(address), NOP, lw(RA, SP, 0x10), JR_RA, addiu(SP, SP, 0x18)]


def project(tmp_path, monkeypatch, functions):
    """functions: (name, words, status, relocs) laid out one after another from BASE in one segment."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    net = RomNetwork(tmp_path / 'db', tmp_path)
    words = [w for _, ws, _, _ in functions for w in ws]
    image = struct.pack('>%dI' % len(words), *words)
    (tmp_path / 'rom.z64').write_bytes(image)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'gp': 0, 'rom': {'file': 'rom.z64'},
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']},
           'segments': [{'name': 'code', 'rom_start': 0, 'rom_end': len(image), 'vram': BASE}]}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,root,arch,big_endian,config) VALUES(1,'game','Game',?,'mips',1,?)",
                   (str(tmp_path), json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(1,1,'',0)")
        offset = 0
        for name, ws, status, relocs in functions:
            size = 4 * len(ws)
            db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm,unit,status,"
                       "best_source,best_score,matched_source) VALUES(1,?,?,?,?,?,?,?,'','code',?,'saved C',40,?)",
                       (name, BASE + offset, offset, size, len(ws), image[offset:offset + size], json.dumps(relocs),
                        status, 'matched C' if status == 'matched' else None))
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,'func')",
                       (name, BASE + offset, size))
            offset += size
    checked = []
    monkeypatch.setattr(net, 'regression_test', lambda proj, header, **kw: checked.append(kw.get('only')) or [])
    return net, checked


def stray_start(tmp_path, monkeypatch, status='open', caller_status='matched'):
    """prev returns at +0x10; 'f' begins with a `break 7` its predecessor left; 'caller' (matched) calls the real entry."""
    real = BASE + 0x14
    return project(tmp_path, monkeypatch, [
        ('prev', [addiu(V0, 0, 1), addiu(V0, 0, 2), JR_RA, NOP], 'matched', []),
        ('f', [BREAK7] + body(), status, []),
        ('caller', calling(real), caller_status, [[8, 4, real]])])


def test_a_stray_start_is_fixed_through_the_review_path(tmp_path, monkeypatch):
    net, checked = stray_start(tmp_path, monkeypatch)
    old = dict(net._function('f', 'game'))
    result = repair.apply_entry_fixes(net, 'game')
    assert [(f['kind'], f['entry']) for f in result['fixed']] == [('trim_start', BASE + 0x14)]
    head = net._function('f', 'game')
    assert head['gone_mark'] is not None and json.loads(head['meta'])['boundary_classification'] == 'padding'
    assert head['best_source'] == 'saved C', 'the old row keeps its work for undo'
    new = net._function('func_80001014', 'game')
    assert (new['vram'], new['size'], new['status'], new['rom_offset']) == (BASE + 0x14, 28, 'open', 0x14)
    assert new['target'] == old['target'][4:] and new['best_score'] is None
    assert json.loads(new['meta'])['boundary_split_from'] == 'f'
    assert net.store.one("SELECT address,size FROM symbols WHERE name='func_80001014'")['size'] == 28
    # the matched caller refers into the changed extent: it, and only it, was built again first
    assert [[r['name'] for r in only] for only in checked] == [['caller']]
    assert net._function('caller', 'game')['status'] == 'matched' and net._function('prev', 'game')['status'] == 'matched'
    history = repair.audit(net, 'game')['history']
    assert len(history) == 1 and history[0]['automatic'] and history[0]['entry'] == BASE + 0x14
    assert history[0]['evidence'].startswith('Automatic: f starts 4 bytes late')


def test_reimport_reuses_a_byte_exact_old_name_and_keeps_its_symbol_on_undo(tmp_path,monkeypatch):
    net,_=stray_start(tmp_path,monkeypatch)
    raw=net._function('f','game')['target'][4:]
    with net.project_store(1).tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'old_name',?,?,'func')",(BASE+0x14,len(raw)))
    preferred={(0x14,BASE+0x14,len(raw),raw):'old_name'}
    assert repair.apply_entry_fixes(net,'game',preferred_entries=preferred)['fixed']
    assert net._function('old_name','game')['target']==raw
    key=repair.audit(net,'game')['history'][0]['key']
    repair.undo(net,'game',key)
    assert net.project_store(1).one("SELECT name FROM symbols WHERE project_id=1 AND name='old_name'")


def test_reimport_does_not_reuse_an_old_name_for_different_bytes_or_conflicting_symbol(tmp_path,monkeypatch):
    for case in ('different_bytes','conflicting_symbol'):
        net,_=stray_start(tmp_path/case,monkeypatch)
        raw=net._function('f','game')['target'][4:]
        if case=='conflicting_symbol':
            with net.project_store(1).tx() as db:
                db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'old_name',?,?,'func')",(BASE+0x100,len(raw)))
        preferred={(0x14,BASE+0x14,len(raw),b'x'*len(raw) if case=='different_bytes' else raw):'old_name'}
        assert repair.apply_entry_fixes(net,'game',preferred_entries=preferred)['fixed']
        assert net._function('func_80001014','game')['target']==raw


def test_entry_fixes_are_idempotent_and_an_undo_is_durable(tmp_path, monkeypatch):
    net, _ = stray_start(tmp_path, monkeypatch)
    before = dict(net._function('f', 'game'))
    repair.apply_entry_fixes(net, 'game')
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []  # nothing looks broken any more
    key = repair.audit(net, 'game')['history'][0]['key']
    repair.undo(net, 'game', key)
    assert dict(net._function('f', 'game')) == before
    assert net._function('func_80001014', 'game')['gone_mark'] is not None
    assert not net.store.one("SELECT 1 FROM symbols WHERE name='func_80001014'")
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == [], 'an undone fix is a person\'s answer: never again'


def test_turned_off_matched_claimed_and_decided_functions_are_left_alone(tmp_path, monkeypatch):
    net, _ = stray_start(tmp_path, monkeypatch, status='matched')
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []
    net, _ = stray_start(tmp_path / 'b', monkeypatch, status='asm')
    result = repair.apply_entry_fixes(net, 'game')
    assert result['fixed'] == [] and result['suggested'] == 1  # set aside: a suggestion at most
    net, _ = stray_start(tmp_path / 'c', monkeypatch)
    net._update_cfg('game', lambda cfg: cfg.update({repair.ENTRY_FIX_SETTING: False}))
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []
    net, _ = stray_start(tmp_path / 'd', monkeypatch)
    worker, _ = net.local_agent('worker')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (net._function('f', 'game')['id'], worker.id, time.time(),
                                                           time.time() + 3600))
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []
    assert net._function('f', 'game')['gone_mark'] is None
    net.rooms['game'] = object()
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []


def test_a_broken_match_stops_every_fix(tmp_path, monkeypatch):
    net, _ = stray_start(tmp_path, monkeypatch)
    monkeypatch.setattr(net, 'regression_test', lambda *a, **kw: [('caller', 'compiles but no longer matches')])
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []
    assert net._function('f', 'game')['gone_mark'] is None


def test_a_swallowed_function_is_split_off_and_undo_joins_it_back(tmp_path, monkeypatch):
    inner = BASE + 0x18
    head = [addiu(SP, SP, -0x20), sw(RA, SP, 0x10), jal(0x80023D58), NOP, j(BASE + 0xC), NOP]  # for (;;) call();
    net, _ = project(tmp_path, monkeypatch, [
        ('f', head + body(0x28), 'open', []),
        ('caller', calling(inner), 'open', [[8, 4, inner]])])
    before = dict(net._function('f', 'game'))
    result = repair.apply_entry_fixes(net, 'game')
    assert [(f['kind'], f['entry']) for f in result['fixed']] == [('split_end', inner)]
    f = net._function('f', 'game')
    assert (f['size'], f['gone_mark'], f['status']) == (24, None, 'open')
    new = net._function('func_80001018', 'game')
    assert (new['vram'], new['size'], new['status']) == (inner, 28, 'open')
    repair.undo(net, 'game', repair.audit(net, 'game')['history'][0]['key'])
    assert dict(net._function('f', 'game')) == before


def test_check_again_fixes_projects_imported_before(tmp_path, monkeypatch):
    net, _ = stray_start(tmp_path, monkeypatch)
    monkeypatch.setattr(type(net.console(net.project_row('game'))), 'on_start', lambda *a, **k: None)
    said = net.recheck('game', log=lambda *a: None)
    assert '1 function boundary fixed' in said
    assert net._function('f', 'game')['gone_mark'] is not None


@pytest.mark.parametrize('kind', ['trim_start', 'split_end'])
def test_the_preview_refuses_an_entry_outside_the_function(tmp_path, monkeypatch, kind):
    net, _ = stray_start(tmp_path, monkeypatch)
    from decompnet.network import NetworkError
    with pytest.raises(NetworkError, match='inside the function'):
        repair.preview(net, 'game', 'f', kind, BASE + 0x100)


def test_a_new_import_fixes_them_before_any_agent_or_draft_sees_the_function(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import N64
    from decompnet.rom import Picked, split
    layout = [('func_80001000', [addiu(V0, 0, 1), addiu(V0, 0, 2), JR_RA, NOP]),
              ('func_80001010', [BREAK7] + body()),
              ('func_8000102C', calling(BASE + 0x14))]
    image = b''.join(struct.pack('>%dI' % len(ws), *ws) for _, ws in layout)

    def fake_split(workdir, *args, **kwargs):
        functions, offset = [], 0
        for name, ws in layout:
            lines = [split.AsmLine(offset + 4 * i, BASE + offset + 4 * i, w, 'nop', '') for i, w in enumerate(ws)]
            functions.append(split.RomFunction(name, BASE + offset, offset, 'code', lines, workdir / 'game.s'))
            offset += 4 * len(ws)
        return split.SplitResult(functions, [], [split.Segment('code', 0, len(image), BASE)], {}, 0, '', '', '')
    monkeypatch.setattr(N64, 'split_game', fake_split)
    monkeypatch.setattr(N64, 'debug_info', lambda *a: None)
    seen = []
    monkeypatch.setattr(N64, 'after_import', lambda net, slug, **kw: seen.append(
        net.store.one("SELECT 1 FROM functions WHERE name='func_80001014'") is not None))
    net = RomNetwork(tmp_path / 'db', tmp_path)
    picked = Picked('n64', image, 'game.z64', {'title': 'Game', 'size': len(image), 'game_code': 'TEST', 'region': 'US',
                                                 'compiler_guess': 'KMC', 'sha1': 'game'}, True)
    slug = net.import_split(N64, picked, toolchain='gcc-2.7.2-kmc', flags=['-O2'], log=lambda s: None)
    assert seen == [True], 'fixed before the console\'s after-import work (drafts, compiler tests) runs'
    assert net._function('func_80001010', slug)['gone_mark'] is not None
    assert net._function('func_80001014', slug)['status'] == 'open'
    assert repair.audit(net, slug)['history'][0]['automatic']


def test_the_review_dialog_turns_it_off_and_apply_safe_fixes_still_applies_on_request(tmp_path, monkeypatch):
    net, _ = stray_start(tmp_path, monkeypatch)
    assert repair.audit(net, 'game')['automatic'] is True
    assert repair.set_entry_fixes(net, 'game', False) == {'automatic': False}
    assert repair.audit(net, 'game')['automatic'] is False
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []  # import and Check again leave it alone now
    listed = {f['function']: f for f in repair.audit(net, 'game')['findings']}
    assert listed['f']['suggested_action'] == 'trim_start' and listed['f']['entry'] == BASE + 0x14
    result = repair.apply_safe(net, 'game')  # a person's click asks for it
    assert [(f['flagged'], f['action']) for f in result['fixed']] == [('f', 'trim_start')]
    assert net._function('func_80001014', 'game')['status'] == 'open'
    from decompnet.network import NetworkError
    with pytest.raises(NetworkError, match='Choose whether'):
        repair.set_entry_fixes(net, 'game', 'no')


def test_a_retained_tail_is_confirmed_and_an_undone_confirmation_stays_undone(tmp_path, monkeypatch):
    """A person's adjust left `boundary_tail_...` waiting for review (its keep never committed on an N64 game): a
    matched callback builder points at its first word, so Check again keeps it and agents may claim it."""
    tail = BASE + 0x10
    net, _ = project(tmp_path, monkeypatch, [
        ('prev', [addiu(V0, 0, 1), addiu(V0, 0, 2), JR_RA, NOP], 'matched', []),
        ('boundary_tail_80001010', body(), 'open', []),
        ('caller', calling(BASE + 0x100), 'matched', [[0, 5, tail], [4, 6, tail]])])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta=? WHERE name='boundary_tail_80001010'",
                   (json.dumps({'boundary_tail_review': 'Retained tail from a reviewed boundary adjustment.'}),))
    fid = net._function('boundary_tail_80001010', 'game')['id']
    assert fid in repair.blocked(net, net.project_row('game'))
    result = repair.apply_entry_fixes(net, 'game')
    assert [(f['kind'], f['function']) for f in result['fixed']] == [('keep', 'boundary_tail_80001010')]
    assert fid not in repair.blocked(net, net.project_row('game'))
    repair.undo(net, 'game', repair.audit(net, 'game')['history'][0]['key'])
    assert fid in repair.blocked(net, net.project_row('game'))
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []


def test_a_function_set_aside_only_for_its_stray_words_is_fixed(tmp_path, monkeypatch):
    """asm_reason filed four functions of an N64 game as assembly for the very words a trim cuts off ("writes $s3
    before saving it"): with no verdict on them, the fix applies; a person's or agents' verdict keeps it a suggestion."""
    for where, meta, fixed in (('a', {'asm_code': 'writes s3 before saving it'}, True),
                               ('b', {'asm_code': '2 agents found no C makes it', 'not_c': {'m': {'why': 'x'}}}, False)):
        net, _ = stray_start(tmp_path / where, monkeypatch, status='asm')
        with net.store.tx() as db:
            db.execute("UPDATE functions SET meta=? WHERE name='f'", (json.dumps(meta),))
        result = repair.apply_entry_fixes(net, 'game')
        assert bool(result['fixed']) == fixed
        if fixed:
            assert net._function('func_80001014', 'game')['status'] == 'open'


def test_the_rest_of_a_function_is_joined_and_an_undone_join_stays_undone(tmp_path, monkeypatch):
    first = [addiu(SP, SP, -0x18), sw(RA, SP, 0x10), 0x10800005, 0, jal(BASE + 0x100), 0, j(BASE + 0x30), 0]
    rest = [addiu(V0, 0, 1), lw(RA, SP, 0x10), 0x03E00008, addiu(SP, SP, 0x18)]
    net, _ = project(tmp_path, monkeypatch, [
        ('prev', [addiu(V0, 0, 1), addiu(V0, 0, 2), 0x03E00008, 0], 'matched', []),
        ('a', first, 'open', []), ('b', rest, 'open', [])])
    result = repair.apply_entry_fixes(net, 'game')
    assert [(f['kind'], f['function']) for f in result['fixed']] == [('join_previous', 'a')]
    assert net._function('a', 'game')['size'] == 48 and net._function('b', 'game')['gone_mark'] is not None
    repair.undo(net, 'game', repair.audit(net, 'game')['history'][0]['key'])
    assert net._function('a', 'game')['size'] == 32
    assert repair.apply_entry_fixes(net, 'game')['fixed'] == []
