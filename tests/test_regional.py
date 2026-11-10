"""Another release of the same game (app/regional.py): detection, pairing, how addresses move, and names."""
from decompnet import consoles
from decompnet.app.regional import AddressMap, Fn, defines, header_items, missing_items, pair

from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def fn(i, rom, key, size=64, ops=(), relocs=(), vram=None, name=None):
    return Fn(i, name or f'func_{0x80000000 + rom:08X}', rom, vram if vram is not None else 0x80000000 + rom, key,
              size, relocs=list(relocs), ops=tuple(ops))


def similar(x, y):
    return 1.0 if x.ops == y.ops else 0.9 if x.ops[:-1] == y.ops[:-1] else 0.0


def test_release_keys_are_the_game_code_without_its_region():
    n64, gba = consoles.get('n64'), consoles.get('gba')
    assert n64.release_key({'game_code': 'NVLP'}) == n64.release_key({'game_code': 'NVLE'}) == 'NVL'
    assert n64.release_key({'game_code': ''}) is None and n64.release_key({'game_code': 'N00E'}) is None
    assert gba.release_key({'game_code': 'AXVE'}) == gba.release_key({'game_code': 'AXVP'})
    assert consoles.get('ps1').release_key({'game_code': 'SLES-01234'}) is None  # serials differ by region


def test_identical_code_pairs_in_order_and_the_rest_by_place():
    pal = [fn(1, 0x100, 'a', ops='abc'), fn(2, 0x200, 'x', ops='xyz'), fn(3, 0x300, 'b', ops='bcd'),
           fn(4, 0x400, 'd', ops='def'), fn(5, 0x500, 'c', ops='cde')]
    # NTSC: an extra function, the second changed slightly, and 'c' and 'd' swapped (one of them is out of order)
    ntsc = [fn(11, 0x100, 'a', ops='abc'), fn(12, 0x180, 'new', ops='qqq'), fn(13, 0x220, 'x2', ops='xyw'),
            fn(14, 0x320, 'b', ops='bcd'), fn(15, 0x400, 'c', ops='cde'), fn(16, 0x500, 'd', ops='def')]
    pairs = {(x.id, y.id): s for x, y, s in pair(pal, ntsc, similar)}
    assert pairs[(1, 11)] == pairs[(3, 14)] == 1.0
    assert pairs[(2, 13)] == 0.9  # between two identical pairs, in the same place, alike enough
    assert not any(y == 12 for _, y in pairs)  # nothing in PAL is like the new function
    assert len([p for p in pairs if p in ((4, 16), (5, 15))]) == 1  # only one of the swapped pair keeps its order


def test_repeated_identical_code_pairs_only_when_both_have_it_as_often():
    pal = [fn(1, 0x100, 'stub'), fn(2, 0x200, 'stub'), fn(3, 0x300, 'twice')]
    ntsc = [fn(11, 0x100, 'stub'), fn(12, 0x200, 'stub'), fn(13, 0x300, 'twice'), fn(14, 0x400, 'twice')]
    pairs = {(x.id, y.id) for x, y, _ in pair(pal, ntsc, lambda *_: 0.0)}
    assert pairs == {(1, 11), (2, 12)}


def test_addresses_move_as_the_relocations_of_identical_pairs_say():
    pal = [fn(1, 0x100, 'k1', relocs=[(0, 4, 0x80100000), (8, 5, 0x80100100)]),
           fn(2, 0x200, 'k2', relocs=[(0, 5, 0x80100200), (4, 5, 0x80300000)]),
           fn(3, 0x300, 'k3', relocs=[(0, 5, 0x80300000)])]
    ntsc = [fn(11, 0x100, 'k1', relocs=[(0, 4, 0x80100040), (8, 5, 0x80100140)]),
            fn(12, 0x200, 'k2', relocs=[(0, 5, 0x80100240), (4, 5, 0x80300010)]),
            fn(13, 0x300, 'k3', relocs=[(0, 5, 0x80300020)])]
    addresses = AddressMap(pair(pal, ntsc, lambda *_: 0.0))
    assert addresses.get(0x80100100) == 0x80100140
    assert addresses.get(0x80100180) == 0x801001C0  # between two that moved by 0x40
    assert addresses.get(0x80300000) is None and addresses.conflicts == 1  # voted two ways
    assert addresses.get(0x80200000) is None  # between addresses that moved differently
    assert addresses.get(0x80000300) == 0x80000300  # a paired function's own address


def test_header_items_and_what_they_define():
    header = '''/* types */
#define SCALE(x) \\
    ((x) * 4)
typedef struct Car { s32 speed; struct Car *next; } Car;
typedef void (*Handler)(Car *);
struct Track { s32 laps; };
enum { A, B };
extern Car gPlayerCar;
s32 car_update(Car *car, s32 (*cb)(s32));
s32 gTable[4] = { 1, 2, 3, 4 };
'''
    items = header_items(header)
    assert [defines(i) for i in items] == [
        ('type', {'SCALE'}), ('type', {'struct Car', 'Car'}), ('type', {'Handler'}), ('type', {'struct Track'}),
        ('type', {'A', 'B'}), ('decl', {'gPlayerCar'}), ('decl', {'car_update'}), ('decl', {'gTable'})]


def test_missing_items_come_with_this_releases_names_and_only_for_names_it_has():
    donor = '''typedef struct Car { s32 speed; } Car;
typedef s32 Lap;
extern Car gPlayerCar;
extern s32 D_8009B5D0;
extern s32 D_8009FFFF;
s32 car_update(Car *car);
void func_80001000(Car *car);
void func_80002000(void);
'''
    header = 'typedef s32 Lap;\n'
    translate = {'gPlayerCar': 'gPlayerCar', 'D_8009B5D0': 'D_800A1130', 'func_80001000': 'func_80001020'}
    have = {'gPlayerCar', 'D_800A1130', 'func_80001020'}
    items = missing_items(donor, header, translate.get, lambda n: n in have)
    assert items == ['typedef struct Car { s32 speed; } Car;', 'extern Car gPlayerCar;', 'extern s32 D_800A1130;',
                     'void func_80001020(Car *car);']


def test_a_linked_release_gives_its_names_and_header_types(built, tmp_path):  # noqa: F811
    from decompnet.app import regional
    from decompnet.app.engine import RomNetwork

    from .test_twins_across_games import copy_project

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    copy_project(net, slug, "other-release")

    def region(letter, sha1):  # one game, two releases: the game code's last letter and the file differ
        return lambda cfg: cfg["rom"].update(game_code="NDQ" + letter, sha1=sha1, region=letter)
    net._update_cfg(slug, region("P", "1" * 40))
    net._update_cfg("other-release", region("E", "2" * 40))
    other = net.project_row("other-release")
    assert [c["slug"] for c in regional.candidates(net, other)] == [slug]
    assert regional.status(net, "other-release")["candidates"][0]["evidence"] == "game code"

    agent, _ = net.local_agent("claude-opus-5.5-1")
    named = next(r["name"] for r in net.project_store(slug).all(
        "SELECT name FROM functions WHERE project_id=? AND name LIKE 'func_%' ORDER BY vram", net.project_row(slug)["id"]))
    assert net.rename_symbol(agent, slug, named, "update_party").startswith("✅")
    assert net.edit_context(agent, slug, "", "typedef struct Party { s32 size; } Party;\nvoid update_party(Party *p);",
                            "a type").startswith("✅")
    try:
        regional.bring_names(net, "other-release", lambda *_: None)
    except regional.NetworkError as error:
        assert "Link" in str(error)
    else:
        assert False, "names come only from a linked release"
    assert regional.link(net, "other-release", slug)["linked"]["slug"] == slug
    result = regional.bring_names(net, "other-release", lambda *_: None)
    assert result["renamed"] >= 1 and result["identical"] == result["paired"] and not result["rejected"]
    assert net._function("update_party", "other-release")["vram"] == net._function("update_party", slug)["vram"]
    header = net._context(other["id"])["header"]
    assert "typedef struct Party { s32 size; } Party;" in header and "void update_party(Party *p);" in header
    assert regional.bring_names(net, "other-release", lambda *_: None)["renamed"] == 0  # nothing left to bring
    assert regional.linked(net, net.project_row("other-release"))["last"]["renamed"] == 0
    assert regional.link(net, "other-release", None)["linked"] is None
