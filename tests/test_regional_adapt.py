"""The other release's C fitted to this release's numbers (app/regional_adapt.py), and the names and offsets the
release plan gives it (app/regional.py)."""
from decompnet.app import regional
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
    moves, kept, constants, floats = ra.differences(old, new)
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


def test_a_field_read_earlier_here_takes_the_place_of_fields_nothing_reads():
    """A list node's key read at 0x24 in one release and 0x20 in the other: the other release's C has an unread
    unk20 there, which makes way; fields this release reads still never overlap."""
    node = ra.structs("typedef struct Node { int unk00; struct Node *next; int unk08; int unk20; int key; } Node;\n")[0]
    fields = [(0, 4), (4, 4), (8, 4), (0x20, 4), (0x24, 4)]
    placed, size, _ = ra.moved_layout(node, fields, 0x28, {0x24: 0x20}, {4}, {})
    assert [(name, at) for _, name, at, _ in placed] == [('unk00', 0), ('next', 4), ('unk08', 8), ('key', 0x20)]
    assert size == 0x24
    assert ra.moved_layout(node, fields, 0x28, {0x24: 0x6}, {4}, {}) is None  # over `next`, which is read


def test_numbers_in_the_c_change_where_the_instructions_map_them_one_way():
    source = ('/* 0x4C in a comment stays */\nvoid f(u8 *p) { p[0x4C] = 56; *(s32 *)(p + 0x4C) = 0x4C; '
              'g(p, 350); h(1.5f, 0x28u); }')
    out = ra.adapt_numbers(source, {0x4c: 0x42}, {56: 48, 350: 256, 0x28: 0x24})
    assert '/* 0x4C in a comment stays */' in out
    assert 'p[0x42] = 48' in out and '(p + 0x42) = 0x4C' in out  # an offset only where it's added or indexed
    assert 'g(p, 256)' in out and '1.5f' in out and '0x24u' in out
    assert ra.adapt_numbers(source, {}, {}) is None


def test_a_negative_number_that_changed_is_written_back_the_same_way():
    """A mask written `-9` or `~8` builds the instructions' -9; this release's -0x21 is `-0x21` and `~0x20`."""
    source = 'void f(u8 *p) { s32 clear_mask = -9; p[1] &= ~0x8; p[2] = 3 - 9; q = -4; }'
    out = ra.adapt_numbers(source, {}, {-9: -0x21, 3: 5})
    assert 'clear_mask = -33' in out and '&= ~0x20' in out and 'p[2] = 5 - 33' in out
    assert 'q = -4' in out  # a negative number that didn't change stays


def test_a_number_that_is_the_other_releases_address_names_this_releases_variable():
    """`*(u8 **)0x800b68f0`: the other release's address of a variable, written as a number. Here it is inside
    one of this release's big blocks, named by its address and declared; a number below any address stays."""
    pal = [fn(1, 0x100, 'k1', relocs=[(0, 5, 0x800B68F0), (8, 5, 0x800B6900)])]
    ntsc = [fn(11, 0x100, 'k1', relocs=[(0, 5, 0x800BA340), (8, 5, 0x800BA350)])]
    p = _plan([Symbol('gVoices', 0x800B68F0, 4)], [Symbol('D_800B21B0', 0x800B21B0, 0x20000)], pal, ntsc)
    out = regional._raw_addresses(p, 'base = *(u8 **)0x800b68f0; mask = x & 0x80000000;', '')
    assert out == 'extern char D_800BA340[];\nbase = *(u8 **)((u32)&D_800BA340); mask = x & 0x80000000;'
    declared = regional._raw_addresses(p, 'base = *(u8 **)0x800b68f0;', 'extern s32 D_800BA340;')
    assert declared == 'base = *(u8 **)((u32)&D_800BA340);'


def _plan(pal_syms, ntsc_syms, pal_fns, ntsc_fns):
    addresses = AddressMap(pair(pal_fns, ntsc_fns, lambda *_: 0.0))
    mine, theirs = SymbolTable(ntsc_syms), SymbolTable(pal_syms)
    return Plan([], addresses, [], 0, 0, 0, {}, mine.name_at, their_address=theirs.resolve, address_of=mine.resolve,
                their_size=lambda name: theirs.by_name[name].size if name in theirs.by_name else 0)


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


def test_numbers_are_read_around_added_instructions_and_branch_targets_left_out():
    old = insns(('lw', '$v0, 0x178($a0)'), ('addiu', '$a2, $a2, -8'), ('beqz', '$v0, 0x40'), ('jr', '$ra'))
    new = insns(('lw', '$v0, 0x170($a0)'), ('move', '$a1, $s7'), ('addiu', '$a2, $a2, -0x10'), ('beqz', '$v0, 0x48'),
                ('jr', '$ra'))
    moves, kept, constants, floats = ra.differences(old, new)
    assert moves == {0x178: 0x170} and constants == {-8: -16}  # not the branch's 0x40 -> 0x48


def test_floats_built_from_immediates_take_this_releases_value():
    """`lui $at, 0x42f0; mtc1 $at, $f2` is 120.0f: the other release's 120.0f becomes this release's 13000.0f."""
    from decompnet.arch import mips

    old = insns(('lui', '$at, 0x42f0'), ('mtc1', '$at, $f2'), ('lui', '$at, 0x463b'), ('ori', '$at, $at, 0x8000'),
                ('mtc1', '$at, $f4'))
    new = insns(('lui', '$at, 0x464b'), ('ori', '$at, $at, 0x2000'), ('mtc1', '$at, $f2'), ('lui', '$at, 0x4302'),
                ('mtc1', '$at, $f4'))
    assert mips.built_floats(old) == {0: 0x42F00000, 2: 0x463B8000}
    *_, floats = ra.differences(old, new, 'mips')
    assert floats == {0x42F00000: 0x464B2000, 0x463B8000: 0x43020000}
    out = ra.adapt_floats('f(120.0f, 12000.0f, 120, 1.5f);', floats)
    assert out == 'f(13000.0f, 130.0f, 120, 1.5f);'


def test_a_pairs_own_relocations_place_its_names_before_the_game_wide_map():
    """Data inside a big block moves unevenly: the map's guess for a variable can be off, the pair's own
    instructions aren't. `ptr + 0x245` on a 4-byte pointer variable is where it points: left alone."""
    pal = [fn(1, 0x100, 'k1', size=12, ops=('lui', 'lw', 'jr'), relocs=[(0, 5, 0x800D0000), (4, 6, 0x800D0000)]),
           fn(2, 0x200, 'k2', size=8, ops=('lui', 'lw'), relocs=[(0, 5, 0x800C0000)])]
    ntsc = [fn(11, 0x100, 'k3', size=16, ops=('lui', 'nop', 'lw', 'jr'), relocs=[(0, 5, 0x800C8000), (8, 6, 0x800C8000)]),
            fn(12, 0x200, 'k2', size=8, ops=('lui', 'lw'), relocs=[(0, 5, 0x800C0000)])]
    p = _plan([Symbol('gRecords', 0x800D0000, 0x100), Symbol('gPtr', 0x800C0000, 4)],
              [Symbol('D_800C0000', 0x800C0000, 0x10000)], pal, ntsc)
    own = p.for_pair(pal[0], ntsc[0])
    assert own.local == {0x800D0000: 0x800C8000}
    assert own.text('x = gRecords;') == 'x = D_800C8000;'
    assert p.offsets('*(gPtr + 0x245) = 0;') == '*(gPtr + 0x245) = 0;'


def test_a_big_variable_reached_by_its_fields_starts_where_most_of_them_say():
    """The C reaches a big struct's fields by name and never its start: each field's address here, less its offset
    there, says where it starts; a field that moved says how the struct is laid out here."""
    base = 0x800B2218
    pal = [fn(1, 0x100, 'k1', size=12, ops=('lui', 'lw', 'lw'),
              relocs=[(0, 5, 0x800B0100), (4, 6, 0x800B0100), (8, 6, 0x800B6E30)])]
    ntsc = [fn(11, 0x100, 'k1', size=12, ops=('lui', 'lw', 'lw'),
               relocs=[(0, 5, base + 0x100), (4, 6, base + 0x100), (8, 6, base + 0x6E38)])]
    p = _plan([Symbol('gOrderState', 0x800B0000, 0x8000)], [Symbol('D_800A0000', 0x800A0000, 0x20000)], pal, ntsc)
    own = p.for_pair(pal[0], ntsc[0])
    assert own.start_of('gOrderState') == base
    assert own.field_moves('gOrderState') == ({0x6E30: 0x6E38}, {0x100})
    assert own.text('gOrderState.count = 0;') == f'D_{base:08X}.count = 0;'


def test_the_other_releases_declarations_come_for_names_only_it_declares():
    from types import SimpleNamespace

    from decompnet.app.regional import _their_declarations

    donor_header = 'typedef struct Rec { int a; } Rec;\nextern Rec gRec;\nextern int gOther;\n'
    net = SimpleNamespace(_context=lambda *_: {'header': donor_header})
    own = SimpleNamespace(name=lambda word: None)
    out = _their_declarations(net, {'id': 1}, {'unit': ''}, own, 'extern int gHere;\n',
                              'void f(void) { gRec.a = gHere; }')
    assert out == 'typedef struct Rec { int a; } Rec;\nextern Rec gRec;'
    assert _their_declarations(net, {'id': 1}, {'unit': ''}, own, 'extern int gHere;\n', 'void f(void) { gHere = 1; }') == ''


def test_a_functions_own_name_is_never_placed_by_the_addresses_its_code_reaches():
    """start_of reads big variables only: a function's name placed by addresses inside it renamed the function in
    its own C, which then had no function of that name."""
    from decompnet.app.regional import _data_size

    pal = [fn(1, 0x100, 'k1', size=8, ops=('lui', 'lw'), relocs=[(0, 5, 0x80008E38), (4, 6, 0x80008E38)])]
    ntsc = [fn(11, 0x100, 'k1', size=8, ops=('lui', 'lw'), relocs=[(0, 5, 0x80008E48), (4, 6, 0x80008E48)])]
    addresses = AddressMap(pair(pal, ntsc, lambda *_: 0.0))
    mine, theirs = SymbolTable([]), SymbolTable([Symbol('vtx_pool_add', 0x80008E28, 0x100, 'func')])
    p = Plan([], addresses, [], 0, 0, 0, {'vtx_pool_add': 'func_80008E28'}, mine.name_at, their_address=theirs.resolve,
             their_size=lambda name: _data_size(theirs.by_name.get(name)), address_of=mine.resolve)
    own = p.for_pair(pal[0], ntsc[0])
    assert own.start_of('vtx_pool_add') is None
    assert own.text('void vtx_pool_add(void)') == 'void func_80008E28(void)'
