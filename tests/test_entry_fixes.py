"""Start and end boundary problems the bytes settle are fixed automatically; tentative ones stay suggestions
(app/entry_fixes.py). Every fixture is a small homemade instruction list shaped like a case seen in an N64 game."""
import struct

from decompnet.app import entry_fixes as ef

NOP, JR_RA = 0, 0x03E00008
AT, V0, V1, A0, A1, A2, T3, S0, S1, S2, SP, RA = 1, 2, 3, 4, 5, 6, 11, 16, 17, 18, 29, 31
BREAK7 = 0x0007000D


def addiu(rt, rs, imm):
    return 0x24000000 | rs << 21 | rt << 16 | imm & 0xFFFF


def lui(rt, imm):
    return 0x3C000000 | rt << 16 | imm & 0xFFFF


def lw(rt, base, off):
    return 0x8C000000 | base << 21 | rt << 16 | off & 0xFFFF


def sw(rt, base, off):
    return 0xAC000000 | base << 21 | rt << 16 | off & 0xFFFF


def move(rd, rs):
    return rs << 21 | rd << 11 | 0x21  # addu rd, rs, $zero


def jal(address):
    return 0x0C000000 | address >> 2 & 0x3FFFFFF


def j(address):
    return 0x08000000 | address >> 2 & 0x3FFFFFF


def beqz(rs, off):
    return 0x10000000 | rs << 21 | off & 0xFFFF


def code(words, big_endian=True):
    return struct.pack(('>' if big_endian else '<') + '%dI' % len(words), *words)


def body(frame=0x18):
    """A usual compiled function: frame, save ra, a call, restore, return."""
    return [addiu(SP, SP, -frame), sw(RA, SP, 0x10), jal(0x80023D58), addiu(A0, 0, 0x14E),
            lw(RA, SP, 0x10), JR_RA, addiu(SP, SP, frame)]


def returning(vram, n=4):
    """A function of `n` words ending on `jr $ra` and its delay slot: what comes after it starts on a complete path."""
    return [addiu(V0, 0, 1)] * (n - 2) + [JR_RA, NOP]


def entry(name, vram, words, relocs=(), status='open', big_endian=True):
    return ef.Entry(name, vram, code(words, big_endian), tuple(relocs), status)


def pointer_to(address, at=0):
    return [(at, 5, address), (at + 4, 6, address)]  # %hi / %lo, as a callback's address is built


def game(stray, *, pointer=None, after=None, status='open', before=None, big_endian=True):
    """The function under test at 0x80001010 (its stray words, then `after` or `body()`), the function before it
    returning at 0x80001010, and a caller pointing at `pointer`."""
    prev = entry('prev', 0x80001000, before or returning(0x80001000), big_endian=big_endian)
    fn = entry('f', 0x80001010, stray + (after or body()), status=status, big_endian=big_endian)
    caller = entry('caller', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(pointer) if pointer else (),
                   big_endian=big_endian)
    return [prev, fn, caller]


def test_a_break_left_by_the_previous_functions_divide_check():
    found = ef.decide(game([BREAK7]))
    f = found['f']
    assert (f.kind, f.at, f.confidence) == ('trim_start', 0x80001014, 'auto')
    assert any('`break` traps' in line for line in f.evidence)
    assert 'starts 4 bytes late' in f.summary()


def test_called_leaf_does_not_turn_argument_adjusting_entry_into_padding():
    leaf = [0x00041040, JR_RA, NOP]
    entries = game([addiu(A0, A0, 0x4000)], pointer=0x80001014, after=leaf)
    assert 'f' not in ef.decide(entries)


def test_a_self_loop_can_be_an_independent_no_return_function():
    loop = [j(0x80001010), NOP]
    assert 'f' not in ef.decide(game(loop))  # an unreferenced SDK panic stub can look just like a loop tail
    f = ef.decide(game(loop, pointer=0x80001018))['f']
    assert (f.kind, f.at, f.confidence) == ('split_end', 0x80001018, 'auto')
    assert any('a loop that never returns' in line for line in f.evidence)


def test_a_whole_self_loop_is_not_archived_as_padding():
    prev = entry('prev', 0x80001000, returning(0x80001000))
    loop = entry('loop', 0x80001010, [j(0x80001010), NOP])
    after = entry('after', 0x80001018, body())
    assert 'loop' not in ef.decide([prev, loop, after])


def test_a_frame_given_back_and_taken_again():
    f = ef.decide(game([addiu(SP, SP, 0x20), addiu(SP, SP, -0x20)]))['f']
    assert (f.at, f.confidence) == (0x80001018, 'auto')
    assert any('gives back a 0x20 frame that was never taken' in line for line in f.evidence)


def test_a_callback_stored_as_func_plus_8():
    # `lui $a1; addiu $a1` built some other callback's address: they read nothing undefined, so only the pointer
    # to func + 8 tells. A frame opening there that the epilogue gives back makes it strong.
    stray = [lui(A1, 0x8001), addiu(A1, A1, -0x70E4)]
    f = ef.decide(game(stray, pointer=0x80001018))['f']
    assert (f.kind, f.at, f.confidence) == ('trim_start', 0x80001018, 'auto')
    assert 'caller points at 0x80001018 (f + 0x8)' in f.evidence[0]
    assert not ef.decide(game(stray)), 'without the pointer nothing says the words are not its own'


def test_a_call_before_saving_ra_and_a_reload_of_ra():
    stray = [lui(A1, 0x8001), addiu(A1, A1, 4), jal(0x80052BD4)]
    f = ef.decide(game(stray, pointer=0x8000101C))['f']
    assert f.at == 0x8000101C and any('calls out (`jal`) before saving $ra' in line for line in f.evidence)
    # The frame opens in the call's delay slot: without a pointer that slot is the call's, not an entry.
    assert not ef.decide(game(stray))
    leaf = [lw(RA, SP, 0x28)], [0x00840018, 0x00001012, JR_RA, NOP]  # lw $ra; then mult; mflo; jr; nop
    f = ef.decide(game(leaf[0], after=leaf[1]))['f']
    assert (f.at, f.confidence) == (0x80001014, 'auto')
    assert any('sets $ra before saving it' in line for line in f.evidence)


def test_a_soft_reason_alone_needs_a_frame_or_a_pointer():
    """Reading a register nobody set also fits a switch case: it counts beside a frame, never alone for a leaf."""
    framed = ef.decide(game([sw(0, S0, 0x88)]))['f']
    assert framed.at == 0x80001014 and any('$s0, a callee-saved register' in line for line in framed.evidence)
    leaf_body = [addiu(V0, 0, 1), sw(V0, A0, 0x8C), JR_RA, NOP]
    assert not ef.decide(game([sw(0, S0, 0x88)], after=leaf_body))
    assert ef.decide(game([sw(0, S0, 0x88)], after=leaf_body, pointer=0x80001014))['f'].at == 0x80001014


def test_a_whole_piece_between_two_functions_is_padding():
    prev = entry('prev', 0x80001000, returning(0x80001000))
    piece = entry('piece', 0x80001010, [JR_RA, addiu(SP, SP, 0x18)])
    after = entry('after', 0x80001018, body())
    f = ef.decide([prev, piece, after])['piece']
    assert (f.kind, f.confidence) == ('padding', 'auto')
    assert 'archived as padding' in f.summary()


def test_a_function_swallowed_after_a_loop_that_never_returns():
    head = [addiu(SP, SP, -0x20), sw(RA, SP, 0x10), jal(0x80023D58), NOP, j(0x8000100C), NOP]  # for (;;) call();
    inner = body(0x28)
    fn = entry('f', 0x80001000, head + inner)
    caller = entry('caller', 0x80002000, [jal(0x80001018), NOP, JR_RA, NOP], [(0, 4, 0x80001018)])
    f = ef.decide([fn, caller])['f']
    assert (f.kind, f.at, f.confidence) == ('split_end', 0x80001018, 'auto')
    assert any('a loop that never returns' in line for line in f.evidence)
    assert 'new function starts at 0x80001018' in f.summary()
    # nothing calls it: it could be the function's own unreachable code; a person looks
    assert not ef.decide([fn, entry('caller', 0x80002000, [JR_RA, NOP, NOP, NOP])])
    # a branch from the head into it: the two parts run into each other
    crossing = head[:3] + [beqz(A0, 4)] + head[4:] + inner
    assert not ef.decide([entry('f', 0x80001000, crossing), caller])
    # a switch case after a jump never opens a frame
    case = [addiu(V0, 0, 3), JR_RA, addiu(SP, SP, 0x20)]
    assert not ef.decide([entry('f', 0x80001000, head + case), caller])


def test_anything_entering_the_stray_words_keeps_them():
    assert not ef.decide(game([BREAK7], pointer=0x80001010))
    stored = ef.decide(game([BREAK7]), stored={0x80001010})
    assert not stored, 'a table word pointing at the start (a switch case, a function table) enters it'
    branched = game([BREAK7])
    branched[2] = entry('caller', 0x80002000, [j(0x80001010), NOP, NOP, NOP], [(0, 4, 0x80001010)])
    assert not ef.decide(branched)


def test_the_code_before_must_end_on_a_complete_path():
    ran_on = [addiu(V0, 0, 1)] * 4  # no return: the stray words could be its own continuation
    assert not ef.decide(game([BREAK7], before=ran_on))
    # one unowned alignment word after the return is fine (`word_at` reads it from the image)
    prev = entry('prev', 0x80001000, [addiu(V0, 0, 1), JR_RA, NOP])
    fn = entry('f', 0x80001010, [BREAK7] + body())
    assert ef.decide([prev, fn], word_at=lambda a: 0 if a == 0x8000100C else None)['f'].at == 0x80001014


def test_a_real_start_is_left_alone():
    assert not ef.decide(game([]))
    assert not ef.decide(game([move(S0, A0)], after=body()[:1] + body()[1:]))  # moves an argument: it is code
    leaf = [addiu(V0, 0, 1), JR_RA, NOP]
    assert not ef.decide(game([], after=leaf))


def test_matched_and_shared_overlay_functions_are_never_changed():
    assert not ef.decide(game([BREAK7], status='matched'))
    other = entry('overlay', 0x80001010, body())  # another overlay loaded at the same address
    assert not ef.decide(game([BREAK7]) + [other])


def test_the_entry_further_in_wins_over_an_earlier_shape():
    # A pointer to +0xC with a leaf shape at +8 too: only the pointed entry is the function.
    stray = [0x2A420008, 0x14400007, addiu(V0, 0, 6)]  # slti $v0,$s2,8; bnez $v0; addiu $v0,6
    f = ef.decide(game(stray, after=[JR_RA, NOP], pointer=0x8000101C))['f']
    assert f.at == 0x8000101C


def test_hand_written_assembly_is_only_suggested():
    mtc0 = 0x40806000  # mtc0 $zero, $12: only hand-written code touches coprocessor 0
    f = ef.decide(game([BREAK7], after=[addiu(SP, SP, -0x18), mtc0] + body()[1:]))['f']
    assert f.confidence == 'suggest' and 'hand-written assembly' in f.warning


def test_big_and_little_endian_agree():
    for big in (True, False):
        found = ef.decide(game([BREAK7], big_endian=big), big_endian=big)
        assert found['f'].at == 0x80001014


def test_a_value_computed_and_lost_is_a_leftover():
    """A leaf nothing points at, after two leftover words: `addiu $v0,1` that the leaf's first word overwrites (an
    ALU result nobody reads: no compiler leaves one), so the soft `$s1` read before it doesn't decide alone."""
    leaf = [lui(V0, 0x8014), lw(V1, V0, -0x48C), JR_RA, move(V0, V1)]
    f = ef.decide(game([sw(V0, S1, 0x7C), addiu(V0, 0, 1)], after=leaf))['f']
    assert (f.kind, f.at, f.confidence) == ('trim_start', 0x80001018, 'auto')
    assert any('loses it before anything reads it' in line for line in f.evidence)
    assert not ef.decide(game([sw(V0, S1, 0x7C), addiu(A1, 0, 1)], after=leaf)), 'a value kept for later is no evidence'


def test_a_load_lost_across_the_first_call_counts_beside_a_frame_only():
    """`lw $v0` before the frame, dead once the call returns: soft (a device register's read is a load nobody uses)."""
    f = ef.decide(game([lw(V0, A0, 0x90)]))['f']
    assert (f.kind, f.at) == ('trim_start', 0x80001014)
    leaf = [lui(V0, 0x8014), lw(V1, V0, -0x48C), JR_RA, move(V0, V1)]
    assert not ef.decide(game([lw(V0, A0, 0x90)], after=leaf))
    passes = [addiu(SP, SP, -0x18), sw(RA, SP, 0x10), jal(0x80023D58), NOP, lw(RA, SP, 0x10), JR_RA, addiu(SP, SP, 0x18)]
    assert not ef.decide(game([lw(A0, A0, 0x90)], after=passes)), 'the callee reads its arguments'
    assert ef.decide(game([lw(V0, A0, 0x90)], after=passes))['f'].at == 0x80001014


def test_code_after_an_early_return_is_split_off():
    """An empty function other code calls (`jr $ra; nop`), then the words of another inside the same extent."""
    other = [sw(V0, S1, 0x7C), lui(V0, 0x8014), lw(V1, V0, -0x48C), JR_RA, move(V0, V1)]
    f = ef.decide(game([], after=[JR_RA, NOP] + other, pointer=0x80001010))['f']
    assert (f.kind, f.at, f.confidence) == ('split_end', 0x80001018, 'auto')
    assert not ef.decide(game([], after=[JR_RA, NOP] + other)), 'nothing points at it: no evidence it is an entry'
    branch = [beqz(A0, 3), NOP, JR_RA, NOP] + other  # the branch reaches past the return
    assert not ef.decide(game([], after=branch, pointer=0x80001010))
    table = [0x00400008, NOP, JR_RA, NOP] + other  # jr $v0: a switch's table may reach anything
    assert not ef.decide(game([], after=table, pointer=0x80001010))
    assert not ef.decide(game([], after=[JR_RA, NOP, NOP, NOP], pointer=0x80001010)), 'alignment after a return'


def test_a_retained_tail_other_code_points_at_is_kept():
    tail = entry('boundary_tail_80001010', 0x80001010, body())
    prev = entry('prev', 0x80001000, returning(0x80001000))
    caller = entry('caller', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001010))
    f = ef.decide([prev, tail, caller], reviewed={tail.name})[tail.name]
    assert (f.kind, f.at, f.confidence) == ('keep', 0x80001010, 'auto') and 'caller' in f.evidence[0]
    assert not ef.decide([prev, tail, caller]), 'only a tail kept for review is confirmed'
    assert not ef.decide([prev, tail, entry('caller', 0x80002000, [JR_RA, NOP])], reviewed={tail.name})


def test_a_function_cut_where_its_first_part_branches_into_the_rest_is_joined():
    """The first part opens the frame and branches into the second, which gives that frame back: one function."""
    first = [addiu(SP, SP, -0x18), sw(RA, SP, 0x10), beqz(A0, 5), NOP, jal(0x80023D58), NOP, j(0x80001024), NOP]
    rest = [addiu(V0, 0, 1), lw(RA, SP, 0x10), JR_RA, addiu(SP, SP, 0x18)]
    prev = entry('prev', 0x80000FF0, returning(0x80000FF0))
    a, b = entry('a', 0x80001000, first), entry('b', 0x80001020, rest)
    f = ef.decide([prev, a, b])['b']
    assert (f.kind, f.at, f.confidence) == ('join_previous', 0x80001000, 'auto')
    caller = entry('caller', 0x80002000, [JR_RA, NOP, NOP, NOP], pointer_to(0x80001020))
    assert 'b' not in ef.decide([prev, a, b, caller]), 'other code points at it: an entry of its own'
    jumping = entry('a', 0x80001000, first[:2] + [NOP, NOP] + first[4:])  # only the `j` goes there: a tail call
    assert 'b' not in ef.decide([prev, jumping, b])
    framed = entry('b', 0x80001020, [addiu(SP, SP, -0x18)] + rest[1:])
    assert 'b' not in ef.decide([prev, a, framed])


def test_unentered_fallthrough_epilogue_is_joined():
    start = 0x80001000
    first = [addiu(SP, SP, -0x18), sw(S0, SP, 0x10), sw(RA, SP, 0x14),
             jal(0x80023D58), NOP, lw(RA, SP, 0x14)]
    rest = [lw(S0, SP, 0x10), addiu(SP, SP, 0x18), JR_RA, NOP]
    head = entry('head', start, first)
    tail = entry('tail', start + 4 * len(first), rest)
    fix = ef.decide([head, tail])['tail']
    assert (fix.kind, fix.at, fix.confidence) == ('join_previous', start, 'auto')
    caller = entry('caller', 0x80002000, [JR_RA, NOP], pointer_to(tail.vram))
    assert 'tail' not in ef.decide([head, tail, caller]) or ef.decide([head, tail, caller])['tail'].kind != 'join_previous'
    assert 'tail' not in ef.decide([head, tail], stored={tail.vram}) or ef.decide([head, tail], stored={tail.vram})['tail'].kind != 'join_previous'
    wrong = entry('tail', tail.vram, [lw(S0, SP, 0x0c), *rest[1:]])
    assert 'tail' not in ef.decide([head, wrong]) or ef.decide([head, wrong])['tail'].kind != 'join_previous'


def test_fallthrough_only_searches_stored_addresses_after_shape_checks():
    class Stored:
        def __init__(self):
            self.queries = []

        def __contains__(self, address):
            self.queries.append(address)
            return False

    start = 0x80001000
    head = entry('head', start, [JR_RA, NOP])
    tail = entry('tail', start+8, body())
    stored = Stored()
    assert ef._fallthrough_epilogue(head, tail, {}, set(), {}, stored, True) is None
    assert stored.queries == []

    first = [addiu(SP, SP, -0x18), sw(S0, SP, 0x10), sw(RA, SP, 0x14),
             jal(0x80023D58), NOP, lw(RA, SP, 0x14)]
    head = entry('head', start, first)
    tail = entry('tail', start+len(first)*4, [lw(S0, SP, 0x10), addiu(SP, SP, 0x18), JR_RA, NOP])
    assert ef._fallthrough_epilogue(head, tail, {}, set(), {}, stored, True).kind == 'join_previous'
    assert stored.queries == list(range(tail.vram, tail.vram+len(tail.code), 4))


def test_called_frame_shared_with_previous_return_delay_slot_joins_continuation():
    start = 0x80001000
    head = entry('head', start, [addiu(SP, SP, -0x18)])
    tail = entry('tail', start+4, body()[1:])
    caller = entry('caller', 0x80002000, [jal(start), NOP, JR_RA, NOP], [(0, 4, start)])
    prior = lambda at: JR_RA if at == start-4 else None
    fix = ef.decide([head, tail, caller], word_at=prior)['tail']
    assert (fix.kind, fix.at, fix.confidence) == ('join_previous', start, 'auto')
    assert 'tail' not in ef.decide([head, tail, caller], word_at=lambda _: None)
    assert 'tail' not in ef.decide([head, tail, caller], word_at=prior, stored={start+4})
    assert 'tail' not in ef.decide([head, tail, caller], word_at=prior, stored={start+8})
    extra = entry('other', 0x80003000, [jal(start+4), NOP, JR_RA, NOP], [(0, 4, start+4)])
    assert 'tail' not in ef.decide([head, tail, caller, extra], word_at=prior)
    wrong = entry('tail', start+4, body()[1:-1]+[addiu(SP, SP, 0x20)])
    assert 'tail' not in ef.decide([head, wrong, caller], word_at=prior)


def test_shared_frame_accepts_later_ra_save_before_first_transfer():
    start = 0x80001000
    head = entry('head', start, [addiu(SP, SP, -0x38)])
    words = [sw(S0, SP, 0x10), move(S0, A1), sw(S1, SP, 0x14), sw(RA, SP, 0x34),
             jal(0x80023D58), NOP, lw(RA, SP, 0x34), JR_RA, addiu(SP, SP, 0x38)]
    caller = entry('caller', 0x80002000, [jal(start), NOP, JR_RA, NOP], [(0, 4, start)])
    prior = lambda at: JR_RA if at == start-4 else None
    tail = entry('tail', start+4, words)
    fix = ef.decide([head, tail, caller], word_at=prior)['tail']
    assert (fix.kind, fix.at, fix.confidence) == ('join_previous', start, 'auto')
    late = entry('tail', start+4, [words[0], jal(0x80023D58), NOP, *words[2:]])
    assert 'tail' not in ef.decide([head, late, caller], word_at=prior)
    wrong_slot = entry('tail', start+4, words[:-3]+[lw(RA, SP, 0x30), *words[-2:]])
    assert 'tail' not in ef.decide([head, wrong_slot, caller], word_at=prior)
    local_jump = words[:4] + [j(start+28), NOP] + words[6:]
    tail = entry('tail', start+4, local_jump, [(16, 4, start+28)])
    assert ef.decide([head, tail, caller], word_at=prior)['tail'].kind == 'join_previous'
    outside = entry('outside', 0x80003000, [j(start+28), NOP, JR_RA, NOP], [(0, 4, start+28)])
    assert 'tail' not in ef.decide([head, tail, caller, outside], word_at=prior)


def test_decide_rust_parity(monkeypatch):
    from decompnet.app import mcp_gateway
    if mcp_gateway.program() is None:
        return
    prev = entry('prev', 0x80000000, [JR_RA, NOP])
    trimmed = entry('trimmed', 0x80000008, [addiu(SP, SP, 0x20), addiu(SP, SP, -0x20), JR_RA, addiu(SP, SP, 0x20)])
    pad = entry('pad', 0x80000024, [addiu(SP, SP, 0x20)])
    after_pad = entry('after_pad', 0x80000028, [addiu(SP, SP, -0x20), JR_RA, addiu(SP, SP, 0x20)])
    entries = [prev, trimmed, pad, after_pad]
    py_fixes = ef.decide(entries)
    rust_fixes = ef.decide_rust(entries)
    assert rust_fixes is not None
    assert set(rust_fixes) == set(py_fixes)
    for name in py_fixes:
        assert (rust_fixes[name].kind, rust_fixes[name].at, rust_fixes[name].confidence) == (
            py_fixes[name].kind, py_fixes[name].at, py_fixes[name].confidence
        )
    # Fallback when gateway binary is unavailable
    monkeypatch.setattr(mcp_gateway, 'program', lambda: None)
    assert ef.decide_rust(entries) is None
