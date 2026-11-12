"""The other release's C edited where the two releases' code differs (app/regional_edits.py), and the processor's
hints for it (arch/mips.py edit_hint)."""
from types import SimpleNamespace

from decompnet.app import regional_edits as re_
from decompnet.arch import mips
from decompnet.rom.symbols import Symbol, SymbolTable

from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def test_what_a_different_instruction_asks_of_the_c():
    assert mips.edit_hint('lw $v0, 0x10($s0)', 'lhu $v0, 0x10($s0)') == ('width', 4, True)
    assert mips.edit_hint('beq $v0, $zero, 0x40', 'bne $v0, $zero, 0x40') == ('compare',)
    assert mips.edit_hint('subu $v0, $a0, $a1', 'addu $v0, $a0, $a1') == ('operator', '-', '+')
    assert mips.edit_hint('sltu $v0, $a0, $a1', 'slt $v0, $a0, $a1') == ('signed', False)
    assert mips.edit_hint('addiu $v0, $zero, 1', 'move $v0, $zero') == ('constant', 0, 1)
    assert mips.edit_hint('jal func_80027CFC', 'nop') == ('call',)
    assert mips.edit_hint('lw $v0, 0x10($s0)', 'lw $v0, 0x10($s0)') is None


SOURCE = """void f(Obj *o, s32 n) {
    u16 v;
    if (n < 4) {
        v = o->count + n;
    }
    helper(o);
    return 0;
}"""


def test_each_differing_instruction_gives_edits_on_its_line():
    rows = [('|', 'bgez $a1, 0x20', 'bltz $a1, 0x20'), ('|', 'subu $v0, $v0, $a1', 'addu $v0, $v0, $a1'),
            ('>', '', 'jal helper'), ('|', 'addiu $v0, $zero, 1', 'move $v0, $zero'),
            ('|', 'lw $v0, 4($a0)', 'lhu $v0, 4($a0)')]
    at = {0: 3, 1: 4, 2: 6, 3: 7, 4: 4}
    out = re_.edits(SOURCE, rows, at, mips.edit_hint)
    assert any('if (n >= 4)' in s for s in out) and any('if (n <= 4)' in s for s in out)
    assert any('o->count - n' in s for s in out)
    assert any('helper(o);' not in s and 'return 0;' in s for s in out)  # the call only this C makes, dropped
    assert any('return 1;' in s for s in out)
    assert any('    s32 v;' in s for s in out)  # a local of the width the target loads


def test_a_call_only_the_target_makes_goes_in_before_the_next_line():
    rows = [(' ', 'lw $v0, 4($a0)', 'lw $v0, 4($a0)'), ('<', 'jal reset', ''), (' ', 'jr $ra', 'jr $ra')]
    out = re_.edits('void f(void) {\n    x = 1;\n    done();\n}', rows, {0: 2, 2: 3}, mips.edit_hint)
    assert 'void reset();\nvoid f(void) {\n    x = 1;\n    reset();\n    done();\n}' in out


def test_offsets_that_moved_change_array_indexes_and_global_offsets():
    """`child[5]` reads offset 0x14; `D_800C4776 + 0x8062` reaches an address the target's code doesn't."""
    syms = SymbolTable([Symbol('D_800C4776', 0x800C4776, 0x20000)])
    net = SimpleNamespace(symbols=lambda _pid: syms)
    rows = [('i', 'lw $v0, 0x10($s0)', 'lw $v0, 0x14($s0)'),
            ('i', 'lui $at, %hi(D_800C4776+0x12a96)', 'lui $at, %hi(D_800C4776+0x8062)')]
    source = 'void f(s32 *child, s32 slot) {\n    g(child[5]);\n    *(s32 *)(D_800C4776 + 0x8062 + slot * 4) = 0;\n}'
    out = re_.whole_edits(net, {'id': 1, 'arch': 'mips'}, '', source, {}, rows)
    assert any('child[4]' in s for s in out)
    assert any('D_800C4776 + 0x12A96 + slot' in s for s in out)


def test_the_search_finds_the_edit_the_releases_differ_by(built, tmp_path):  # noqa: F811
    """The demo's clamp with `<=` where it has `<`: the search traces the branch to its line and flips it back."""
    from decompnet.app.engine import RomNetwork

    from .test_app_engine import CLAMP

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    proj, fn = net.project_row(slug), net._function("func_800007D0", slug)
    header = net._context(proj['id'], fn['unit'])['header']
    changed = CLAMP.replace('if (v < lo)', 'if (v <= lo)')
    first = re_.judge(net, proj, header, changed, fn)
    assert first is not None and first[0] > 0
    source, score, _ = re_.search(net, proj, header, [changed], fn)
    assert score == 0 and 'v < lo' in source
