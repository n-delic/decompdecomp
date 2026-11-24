"""A function whose first word is padding or dead is suggested for review, never changed (app/start_boundaries.py)."""
import json
import struct

import pytest

from decompnet.app import boundary_investigations, boundary_repairs
from decompnet.app import start_boundaries as sb
from decompnet.app.engine import RomNetwork

NOP, JR_RA = 0, 0x03E00008
SP, RA, A0, A1, V0 = 29, 31, 4, 5, 2


def addiu(rt, rs, imm):
    return 0x24000000 | rs << 21 | rt << 16 | imm & 0xFFFF


def lw(rt, base, off):
    return 0x8C000000 | base << 21 | rt << 16 | off & 0xFFFF


def sw(rt, base, off):
    return 0xAC000000 | base << 21 | rt << 16 | off & 0xFFFF


def jal(address):
    return 0x0C000000 | address >> 2 & 0x3FFFFFF


def beqz(rs, off):
    return 0x10000000 | rs << 21 | off & 0xFFFF


def code(*words):
    return struct.pack('>%dI' % len(words), *words)


def body(frame=0x18):
    """A usual compiled function: frame, save ra, a call, restore, return."""
    return [addiu(SP, SP, -frame), sw(RA, SP, 0x10), jal(0x80023D58), addiu(A0, 0, 0x14E),
            lw(RA, SP, 0x10), JR_RA, addiu(SP, SP, frame)]


def entry(name, vram, words, relocs=(), status='open'):
    return sb.Entry(name, vram, code(*words), tuple(relocs), status)


def pointer_to(address):
    return [(0, 5, address), (4, 6, address)]  # %hi / %lo, as a callback's address is built


def test_callback_into_the_function_points_at_its_real_entry():
    padded = entry('func_80058E1C', 0x80058E1C, [addiu(A1, 0, 0x4F)] + body())
    other = entry('func_80058E90', 0x80058E90, [JR_RA, NOP, NOP, NOP], pointer_to(0x80058E20))
    found = sb.find([padded, other])
    assert set(found) == {'func_80058E1C'}
    f = found['func_80058E1C']
    assert (f.skip, f.start, f.signals) == (1, 0x80058E20, ['callback'])
    assert 'suggested start +4 (0x80058E20)' in f.summary()
    assert 'func_80058E90' in f.evidence[0]['text'] and f.evidence[0]['address'] == 0x80058E20


def test_two_and_three_words_in():
    for k in (2, 3):
        padded = entry('f', 0x80001000, [NOP + 0x3C010001] * k + body())
        other = entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001000 + 4 * k))
        assert sb.find([padded, other])['f'].skip == k
    far = entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001000 + 16))
    assert not sb.find([entry('f', 0x80001000, [NOP] * 4 + body()), far])


def test_anything_entering_the_first_word_keeps_it():
    padded = entry('f', 0x80001000, [addiu(A1, 0, 1)] + body())
    both = [pointer_to(0x80001004), pointer_to(0x80001000)]
    other = entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], both[0] + [(8, 4, 0x80001000)])
    assert not sb.find([padded, other])
    looped = entry('f', 0x80001000, [addiu(V0, 0, 1), NOP, NOP, 0x1000FFFC, NOP, JR_RA, NOP])  # b back to the start
    assert 0x1000FFFC >> 26 == 4
    assert not sb.find([looped, entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001004))])


def test_a_frame_the_first_word_makes_and_the_epilogue_does_not_return_is_strong_with_a_callback():
    words = [addiu(SP, SP, -0x20)] + body(0x18)
    other = entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001004))
    f = sb.find([entry('f', 0x80001000, words), other])['f']
    assert f.confidence == 'strong' and f.signals == ['callback', 'dead']
    assert any('allocates a 0x20 frame' in e['text'] and '0x18' in e['text'] for e in f.evidence)


def test_a_branch_whose_delay_slot_is_the_real_first_instruction():
    words = [beqz(A0, 6)] + body()
    other = entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001004))
    f = sb.find([entry('f', 0x80001000, words), other])['f']
    assert f.confidence == 'strong'
    assert any('delay slot is the real first instruction' in e['text'] for e in f.evidence)


def test_a_reload_from_a_frame_nothing_made_needs_no_pointer():
    leaf = entry('f', 0x8000C33C, [lw(RA, SP, 0x28), 0x00840018, 0x00001012, JR_RA, NOP])  # lw; mult; mflo; jr; nop
    f = sb.find([leaf])['f']
    assert (f.skip, f.signals, f.confidence) == (1, ['dead'], 'tentative')
    assert 'reloads $ra from the stack of a function that never made a frame' in f.evidence[-1]['text']
    kept = entry('f', 0x8000C33C, [addiu(SP, SP, -8), sw(RA, SP, 0x28), lw(RA, SP, 0x28), JR_RA, addiu(SP, SP, 8)])
    assert not sb.find([kept])


def test_a_function_that_balances_its_frame_is_left_alone():
    assert not sb.find([entry('f', 0x80001000, body())])
    two_step = [addiu(SP, SP, -0x20), addiu(SP, SP, -0x18), sw(RA, SP, 0x10), jal(0x80023D58), NOP,
                lw(RA, SP, 0x10), addiu(SP, SP, 0x18), JR_RA, addiu(SP, SP, 0x20)]
    assert not sb.find([entry('f', 0x80001000, two_step)])


def test_layout_counts_only_in_a_game_whose_functions_are_aligned():
    aligned = [entry(f'a{i}', 0x80010000 + 0x40 * i, body()) for i in range(20)]
    padded = entry('f', 0x80001004, [addiu(A1, 0, 0x4F), addiu(A1, 0, 7), NOP] + body())  # constant set again: dead
    found = sb.find(aligned + [padded])
    assert found['f'].signals == ['layout', 'dead'] and found['f'].start == 0x80001008
    assert any('4 bytes past an 8-byte boundary' in e['text'] for e in found['f'].evidence)
    # the same function in a game where half the functions start off the boundary proves nothing
    loose = [entry(f'a{i}', 0x80010000 + 0x40 * i + 4 * (i % 2), body()) for i in range(20)]
    assert not sb.find(loose + [padded])
    # and layout alone, with a word that does something, is not enough
    live = entry('f', 0x80001004, [addiu(V0, 0, 5)] + [addiu(SP, SP, -8), JR_RA, addiu(SP, SP, 8)] + [NOP] * 3)
    assert not sb.find(aligned + [live])


def test_matched_functions_and_functions_starting_where_the_cut_would_land_are_skipped():
    padded = [addiu(A1, 0, 0x4F)] + body()
    other = entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001004))
    assert not sb.find([entry('f', 0x80001000, padded, status='matched'), other])
    assert not sb.find([entry('f', 0x80001000, padded), entry('h', 0x80001004, body()), other])
    own = entry('f', 0x80001000, padded, pointer_to(0x80001004))  # only itself points there (recursion)
    assert not sb.find([own])


def test_big_and_little_endian_agree():
    padded = entry('f', 0x80001000, [addiu(A1, 0, 0x4F)] + body())
    little = sb.Entry('f', 0x80001000, b''.join(w.to_bytes(4, 'little') for w in struct.unpack('>%dI' % (len(padded.code) // 4), padded.code)))
    other = entry('g', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001004))
    assert sb.find([little, other], big_endian=False)['f'].skip == 1
    assert 'f' in sb.find([padded, other])


# ---- the project: briefs, the review list and the suggestion record ----

@pytest.fixture
def game(tmp_path):
    net = RomNetwork(tmp_path / 'db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'gp': 0, 'rom': {'file': 'rom.z64'},
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}, 'segments': []}
    rows = [('padded', 0x80001000, [addiu(A1, 0, 0x4F)] + body(), []),
            ('caller', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001004)),
            ('done', 0x80003000, [addiu(A1, 0, 0x4F)] + body(), [])]
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,root,arch,big_endian,config) VALUES(1,'game','Game',?,'mips',1,?)",
                   (str(tmp_path), json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(1,1,'',0)")
        for name, vram, words, relocs in rows:
            db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm,unit,status) "
                       "VALUES(1,?,?,?,?,?,?,?,'','code',?)",
                       (name, vram, vram & 0xFFFF, 4 * len(words), len(words), code(*words), json.dumps(relocs),
                        'matched' if name == 'done' else 'open'))
    return net, net.project_row('game')


def test_the_brief_names_the_suggested_start(game):
    net, proj = game
    fn = dict(net._function('padded', 'game'))
    text = sb.brief(net, proj, fn)
    assert 'Possible start boundary problem' in text and 'suggested start +4 (0x80001004)' in text
    assert 'release it' in text
    assert sb.brief(net, proj, dict(net._function('caller', 'game'))) is None
    assert sb.brief(net, proj, dict(net._function('done', 'game'))) is None
    assert 'suggested start +4' in net._brief(net._function('padded', 'game'), None, 'CLAIMED')


def test_the_review_list_offers_it_without_pausing_agents(game):
    net, _ = game
    rows = [f for f in boundary_repairs.audit(net, 'game')['findings'] if f['suggested_action'] == 'trim_start']
    assert [(r['function'], r['start'], r['override']) for r in rows] == [('padded', 0x80001004, True)]
    assert not boundary_repairs.blocked(net, net.project_row('game'))  # nothing is held back from agents


def test_the_suggestion_record_has_suggest_boundarys_shape_and_changes_nothing(game):
    net, proj = game
    before = [dict(r) for r in net.project_store(1).all('SELECT * FROM functions ORDER BY id')]
    packet = {'function': 'padded', 'revision': 'r1', 'image': 'i1'}
    items = boundary_investigations.suggestions(net, 'game', packet)
    assert [i['action'] for i in items] == ['trim_start']
    item = items[0]
    assert item['start'] == 0x80001004 and item['confidence'] == 'tentative' and item['automatic']
    assert item['revision'] == 'r1' and item['image'] == 'i1'
    assert {'function', 'action', 'end', 'confidence', 'supporting', 'counter', 'uncertainty', 'citations', 'agent',
            'model', 'time', 'revision', 'image'} <= set(item)
    assert 0x80001004 in item['citations'] and 'suggested start +4' in item['supporting']
    assert boundary_investigations.suggestions(net, 'game', {**packet, 'function': 'caller'}) == []
    assert [dict(r) for r in net.project_store(1).all('SELECT * FROM functions ORDER BY id')] == before
    assert not net.project_store(1).all("SELECT 1 FROM settings WHERE key LIKE 'boundary-suggestion:%'")
