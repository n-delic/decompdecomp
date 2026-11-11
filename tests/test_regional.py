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
    lines, steps = [], []
    log = lambda line: lines.append(line)  # noqa: E731
    log.progress = steps.append  # (a job's log: its lines, and its progress for the panel's bar)
    result = regional.bring_names(net, "other-release", log)
    phases = [step["phase"] for step in steps]
    assert phases[0].startswith("Reading") and any(ph.startswith("Pairing functions") for ph in phases)
    counted = [step for step in steps if step["phase"].startswith("Renaming update_party (1 of 1): checking")]
    assert counted and all(0 <= step["done"] <= step["total"] == result["renamed"] for step in counted)
    assert any(step["phase"].startswith("Checking 2 header items") for step in steps)
    assert any("names checked" in line for line in lines)
    assert result["renamed"] >= 1 and result["identical"] == result["paired"] and not result["rejected"]
    assert net._function("update_party", "other-release")["vram"] == net._function("update_party", slug)["vram"]
    header = net._context(other["id"])["header"]
    assert "typedef struct Party { s32 size; } Party;" in header and "void update_party(Party *p);" in header
    assert regional.bring_names(net, "other-release", lambda *_: None)["renamed"] == 0  # nothing left to bring
    assert regional.linked(net, net.project_row("other-release"))["last"]["renamed"] == 0
    assert regional.link(net, "other-release", None)["linked"] is None


def _two_releases(tmp_path):
    """The demo game and a copy posing as its other region's release, linked, func_800007D0 split out in both."""
    from decompnet.app import regional
    from decompnet.app.engine import RomNetwork

    from .test_twins_across_games import copy_project

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    copy_project(net, slug, "other-release")
    net._update_cfg(slug, lambda cfg: cfg["rom"].update(game_code="NDQP", sha1="1" * 40))
    net._update_cfg("other-release", lambda cfg: cfg["rom"].update(game_code="NDQE", sha1="2" * 40))
    regional.link(net, "other-release", slug)
    return net, slug, agent


def test_the_other_releases_code_matches_and_polishes_here(built, tmp_path):  # noqa: F811
    from decompnet.app import regional

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")
    result = regional.transfer(net, "other-release")
    assert result["matched"] >= 1
    fn = net._function("func_800007D0", "other-release")  # its own name: not renamed by this phase
    assert fn["status"] == "matched" and "another release of this game" in fn["matched_source"]
    assert "clamp_s32" not in fn["matched_source"].split("*/", 1)[1]
    assert regional.transfer(net, "other-release")["tried"] == 0  # nothing left: a pass tries what changed
    assert net.twins.cross_game_results("other-release")["counts"].get("match", 0) >= 1  # in "Matches from other games"

    # A polished version in the other release comes over to the function matched here, if it still matches.
    polished = CLAMP.replace("func_800007D0", "clamp_s32").replace("s32 v,", "s32 value,").replace("(v ", "(value ") \
        .replace("return v;", "return value;")
    assert net.readability.submit(agent, slug, "clamp_s32", polished).startswith("✅")
    assert regional.transfer(net, "other-release")["polished"] == 1
    fn = net._function("func_800007D0", "other-release")
    assert fn["readable"] and "value" in fn["matched_source"]


def test_a_function_built_with_other_settings_takes_them_when_they_match(built, tmp_path, monkeypatch):  # noqa: F811
    from decompnet.app import regional
    from decompnet.project import compiler_for

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    donor_fn = net._function("func_800007D0", slug)
    own = net.cfg(net.project_row(slug))["compiler"]
    other = dict(toolchain=own["toolchain"], flags=[*own["flags"], "-g"])
    from decompnet.app.prepared_evidence import scoped_config
    net._apply_compiler_config(net.project_row(slug), scoped_config(net.project_row(slug), donor_fn, other))
    # Its own settings miss here (as a function only the other release's flags build would)
    monkeypatch.setattr(net.twins, "_attempt_twin", lambda *a, **k: ("miss", "score 20"))
    assert regional.transfer(net, "other-release")["matched"] == 1
    fn = net._function("func_800007D0", "other-release")
    assert fn["status"] == "matched"
    assert compiler_for(net.cfg(net.project_row("other-release")), fn["vram"], fn)["flags"] == other["flags"]


def test_a_compiler_this_computer_cannot_run_only_skips_its_function(built, tmp_path, monkeypatch):  # noqa: F811
    from decompnet.app import regional
    from decompnet.app.prepared_evidence import scoped_config
    from decompnet.toolchains import ToolchainError

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    own = net.cfg(net.project_row(slug))["compiler"]
    net._apply_compiler_config(net.project_row(slug), scoped_config(
        net.project_row(slug), net._function("func_800007D0", slug), dict(toolchain=own["toolchain"], flags=[*own["flags"], "-g"])))
    monkeypatch.setattr(net.twins, "_attempt_twin", lambda *a, **k: ("miss", "score 20"))

    def unavailable(*_a, **_k):
        raise ToolchainError("The Linux compiler runner is not ready.")
    monkeypatch.setattr(net, "_judge_with", unavailable)
    assert regional.transfer(net, "other-release")["matched"] == 0
    assert net._function("func_800007D0", "other-release")["status"] == "open"
