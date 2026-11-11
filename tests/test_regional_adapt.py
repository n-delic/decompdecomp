"""The other release's C fitted to this release's numbers (app/regional_adapt.py), and the names and offsets the
release plan gives it (app/regional.py)."""
from decompnet.app import regional_adapt as ra
from decompnet.app.regional import AddressMap, Plan, pair
from decompnet.rom.symbols import Symbol, SymbolTable
from decompnet.scoring import Insn

from .test_regional import fn
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def insns(*rows):
    return [Insn(4 * i, m, ops, f'{m} {ops}') for i, (m, ops) in enumerate(rows)]


GRP = """typedef struct Grp
{
  void *next;
  char pad4[0x28 - 4];
  void *subs;
  char pad2C[0x4C - 0x2C];
  u8 nsub;
} Grp;
"""


def test_what_moved_is_read_from_the_same_instructions():
    """A struct field read at 0x4c in one release and 0x42 in the other; 0x28 moves for one struct and stays for
    another; a stack slot moves with the code; a stride is a constant; a symbol that differs says nothing."""
    old = insns(('lbu', '$s0, 0x4c($s2)'), ('lw', '$s3, 0x28($s2)'), ('lw', '$v0, 0x28($a0)'), ('lw', '$s4, ($s2)'),
                ('sw', '$ra, 0x14($sp)'), ('addiu', '$a1, $a1, 0x38'), ('lw', '$v0, %lo(gThing)($v0)'),
                ('lw', '$v0, 0x10($a1)'))
    new = insns(('lbu', '$s0, 0x42($s2)'), ('lw', '$s3, 0x24($s2)'), ('lw', '$v0, 0x28($a0)'), ('lw', '$s4, ($s2)'),
                ('sw', '$ra, 0x1c($sp)'), ('addiu', '$a1, $a1, 0x30'), ('lw', '$v0, %lo(D_800A1104)($v0)'),
                ('lw', '$v0, 0x18($a1)'))
    old.append(Insn(32, 'lw', '$v0, 0x10($a2)', ''))
    new.append(Insn(32, 'lw', '$v0, 0x14($a2)', ''))
    moves, kept, constants = ra.differences(old, new)
    assert moves == {0x4c: 0x42, 0x28: 0x24}  # 0x10 moved two ways: nothing
    assert kept == {0x28, 0}
    assert constants == {0x38: 0x30}


def test_a_struct_the_c_defines_is_laid_out_as_this_release_reads_it():
    s = ra.structs(GRP)[0]
    assert s.type == 'struct Grp' and [n for _, n in s.members] == ['next', 'pad4', 'subs', 'pad2C', 'nsub']
    fields = [(0, 4), (4, 36), (0x28, 4), (0x2C, 32), (0x4C, 1)]
    placed, size, said = ra.moved_layout(s, fields, 0x50, {0x4c: 0x42, 0x28: 0x24}, {0, 0x28}, {})
    assert [(name, at) for _, name, at, _ in placed] == [('next', 0), ('subs', 0x24), ('nsub', 0x42)]
    assert size == 0x46 and not said
    text = ra.relaid(s, placed, size)
    assert 'char pad_4[0x20];' in text and 'char pad_28[0x1A];' in text and text.rstrip().endswith('} Grp;')
    # nothing of it moved (0x28 also stays elsewhere: another struct's field), or the new places overlap
    assert ra.moved_layout(s, fields, 0x50, {0x28: 0x24}, {0x28}, {}) is None
    assert ra.moved_layout(s, fields, 0x50, {0x4c: 0x2, 0x28: 0x24}, set(), {}) is None


def test_numbers_in_the_c_change_where_the_instructions_map_them_one_way():
    source = ('/* 0x4C in a comment stays */\nvoid f(u8 *p) { p[0x4C] = 56; *(s32 *)(p + 0x4C) = 0x4C; '
              'g(p, 350); h(1.5f, 0x28u); }')
    out = ra.adapt_numbers(source, {0x4c: 0x42}, {56: 48, 350: 256, 0x28: 0x24})
    assert '/* 0x4C in a comment stays */' in out
    assert 'p[0x42] = 48' in out and '(p + 0x42) = 0x4C' in out  # an offset only where it's added or indexed
    assert 'g(p, 256)' in out and '1.5f' in out and '0x24u' in out
    assert ra.adapt_numbers(source, {}, {}) is None


def _plan(pal_syms, ntsc_syms, pal_fns, ntsc_fns):
    addresses = AddressMap(pair(pal_fns, ntsc_fns, lambda *_: 0.0))
    mine, theirs = SymbolTable(ntsc_syms), SymbolTable(pal_syms)
    return Plan([], addresses, [], 0, 0, 0, {}, mine.name_at, theirs.resolve, mine.resolve)


def test_relocations_vote_by_their_place_when_one_release_lists_fewer():
    """A %lo inside a big symbol can be missing from one release's list: the rest still vote."""
    pal = [fn(1, 0x100, 'k1', ops=('lui', 'lw'), relocs=[(0, 5, 0x800C9554), (4, 6, 0x800C9554),
                                                         (8, 5, 0x800A0000)])]
    ntsc = [fn(11, 0x100, 'k2', ops=('lui', 'lw'), relocs=[(0, 5, 0x800CC514), (8, 5, 0x800A0010)])]
    addresses = AddressMap([(pal[0], ntsc[0], 1.0)])
    assert addresses.get(0x800C9554) == 0x800CC514 and addresses.get(0x800A0000) == 0x800A0010


def test_a_variable_inside_a_big_symbol_here_is_named_by_its_address():
    """This release has one big block where the other has variables: the C names the address, which links there."""
    pal = [fn(1, 0x100, 'k1', relocs=[(0, 5, 0x800D05B8), (8, 5, 0x800C3D0E)])]
    ntsc = [fn(11, 0x100, 'k1', relocs=[(0, 5, 0x800D4A78), (8, 5, 0x800C3D0E)])]
    p = _plan([Symbol('gOrderState', 0x800C3D0E, 0x20000)], [Symbol('D_800C02AE', 0x800C02AE, 0x20000)], pal, ntsc)
    assert p.text('x = D_800D05B8;') == 'x = D_800D4A78;'


def test_a_field_of_a_global_reached_by_offset_moves_as_the_address_map_says():
    pal = [fn(1, 0x100, 'k1', relocs=[(0, 5, 0x800C3D0E), (8, 5, 0x800C3D0E + 0x3252)])]
    ntsc = [fn(11, 0x100, 'k1', relocs=[(0, 5, 0x800C3D0E), (8, 5, 0x800C3D0E + 0x3240)])]
    p = _plan([Symbol('gOrderState', 0x800C3D0E, 0x20000)], [Symbol('gOrderState', 0x800C3D0E, 0x20000)], pal, ntsc)
    assert p.offsets('*(void **)(gOrderState + 0x3252) = q; r = gOrderState + 8; s->a + 0x3252;') == \
        '*(void **)(gOrderState + 0x3240) = q; r = gOrderState + 8; s->a + 0x3252;'


def test_the_compiler_lays_out_the_fitted_struct(built, tmp_path):  # noqa: F811
    """The probe reads where the compiler put each field, before and after the new layout."""
    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    proj = net.project_row(slug)
    f = net.project_store(proj['id']).one("SELECT * FROM functions WHERE project_id=? AND status='open' LIMIT 1",
                                          proj['id'])
    header = net._context(proj['id'], f['unit'])['header']
    source = GRP + 'int use(Grp *g) { return g->nsub + (int)g->subs; }\n'
    out = ra.adapt_structs(net, proj, header, source, f, {0x4c: 0x42, 0x28: 0x24}, {0, 0x28}, {})
    assert out is not None and 'char pad_28[0x1A];' in out
    items = ra.structs(out)
    values = ra._probe(net, proj, header, out, f, items, sum(2 * len(s.members) + 1 for s in items))
    offsets = dict(zip((n for _, n in items[0].members), (o for o, _ in ra.layouts(values, items)[0][0])))
    assert offsets['subs'] == 0x24 and offsets['nsub'] == 0x42
