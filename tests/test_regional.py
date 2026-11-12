"""Another release of the same game (app/regional.py): detection, pairing, how addresses move, and names."""
from decompnet import consoles
from decompnet.app.regional import AddressMap, Fn, built_items, defines, header_items, missing_items, pair

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


def test_a_port_branch_of_the_other_releases_header_is_not_copied():
    """A header kept for a PC port too defines a type in both branches of `#ifdef PORT`: the game's build is the
    `#else`, and the first one (`uintptr_t`) didn't build for an N64 game's compiler."""
    donor = """#ifdef PORT
typedef uintptr_t AddrWord;
#else
typedef s32 AddrWord;
#endif
#define FAST 1
#if defined(FAST)
typedef s16 Speed;
#elif 1
typedef s32 Speed;
#endif
#if SOMETHING > 2
typedef u8 Odd;
#else
typedef u16 Odd;
#endif
"""
    assert missing_items(donor, "", lambda n: None, lambda n: True) == [
        "typedef s32 AddrWord;", "#define FAST 1", "typedef s16 Speed;", "typedef u8 Odd;"]  # (unread: as before)
    assert built_items("#ifndef PORT\ntypedef s32 A;\n#endif\n") == ["typedef s32 A;"]


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


def test_a_miss_another_matcher_remembers_still_tries_the_other_releases_settings(built, tmp_path, monkeypatch):  # noqa: F811
    """Similar-code reuse tried the other release's C with this project's settings and remembered the miss; the
    pass skipped that C and never tried the other release's flags, which build it."""
    from decompnet.app import regional
    from decompnet.app.prepared_evidence import scoped_config

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    own = net.cfg(net.project_row(slug))["compiler"]
    net._apply_compiler_config(net.project_row(slug), scoped_config(
        net.project_row(slug), net._function("func_800007D0", slug), dict(toolchain=own["toolchain"], flags=[*own["flags"], "-g"])))
    monkeypatch.setattr(regional, "_try_fitted", lambda *a, **k: None)
    monkeypatch.setattr(net.twins, "_missed", lambda b_id, source, **k: "other release's compiler" not in source)
    tried = []
    monkeypatch.setattr(net.twins, "_attempt_twin", lambda *a, **k: tried.append(1) or ("miss", "score 20"))
    assert regional.transfer(net, "other-release")["matched"] == 1
    assert not tried  # its own settings weren't tried again
    assert net._function("func_800007D0", "other-release")["status"] == "matched"


def test_a_build_another_jobs_settings_change_outdated_is_built_again(built, tmp_path, monkeypatch):  # noqa: F811
    """Similar-code reuse changed the project's settings while the other release's settings built a function: the
    stale build was recorded as a miss, and two functions of an N64 game that match one at a time stayed open."""
    from decompnet.app import regional
    from decompnet.app.prepared_evidence import scoped_config
    from decompnet.network import NetworkError

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    own = net.cfg(net.project_row(slug))["compiler"]
    net._apply_compiler_config(net.project_row(slug), scoped_config(
        net.project_row(slug), net._function("func_800007D0", slug), dict(toolchain=own["toolchain"], flags=[*own["flags"], "-g"])))
    monkeypatch.setattr(regional, "_try_fitted", lambda *a, **k: None)
    monkeypatch.setattr(net.twins, "_attempt_twin", lambda *a, **k: ("miss", "score 20"))
    real, stale = net.try_quietly, []

    def outdated(*a, **k):
        if len(stale) < 2:
            stale.append(1)
            raise NetworkError("Compiler settings changed while this candidate was compiling. Compile it again.")
        return real(*a, **k)
    monkeypatch.setattr(net, "try_quietly", outdated)
    assert regional.transfer(net, "other-release")["matched"] == 1
    assert len(stale) == 2


def test_a_try_that_never_finishes_is_not_remembered_as_a_miss(built, tmp_path, monkeypatch):  # noqa: F811
    from decompnet.app import regional
    from decompnet.app.prepared_evidence import scoped_config
    from decompnet.network import NetworkError

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    own = net.cfg(net.project_row(slug))["compiler"]
    net._apply_compiler_config(net.project_row(slug), scoped_config(
        net.project_row(slug), net._function("func_800007D0", slug), dict(toolchain=own["toolchain"], flags=[*own["flags"], "-g"])))
    monkeypatch.setattr(regional, "_try_fitted", lambda *a, **k: None)
    monkeypatch.setattr(net.twins, "_attempt_twin", lambda *a, **k: ("miss", "score 20"))

    def outdated(*a, **k):
        raise NetworkError("Compiler settings changed while this candidate was compiling. Compile it again.")
    monkeypatch.setattr(net, "try_quietly", outdated)
    assert regional.transfer(net, "other-release")["matched"] == 0
    monkeypatch.undo()
    monkeypatch.setattr(regional, "_try_fitted", lambda *a, **k: None)
    assert regional.transfer(net, "other-release")["matched"] == 1  # the next pass tries their settings again


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


def test_trying_the_other_releases_code_does_not_sync_saved_files_per_function(built, tmp_path):  # noqa: F811
    """Each try synced the project's saved files before and after it, and again to read the project: about ten
    seconds a function on a finished 2,000-function game, and two days for one pass. Files follow through the
    progress watcher, as for autopilot's own drafts."""
    from decompnet.app import regional

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    synced = []
    sync = net.progress.sync
    net.progress.sync = lambda *a, **k: (synced.append(a), sync(*a, **k))[1]
    result = regional.transfer(net, "other-release")
    assert result["tried"] >= 1 and result["matched"] >= 1
    assert len(synced) <= 4  # opening the two projects; it was 17 for this one try


def test_the_same_bytes_are_tried_before_the_same_instructions():
    """A pair scored 1.0 for the same instructions can differ in a struct field's offset, and rarely matches as it is:
    pairs with the same bytes (twin keys) go first, so a pass's matches come in its first minute."""
    from decompnet.app.regional import _likeliest

    same = (fn(1, 0, 'k'), fn(2, 0, 'k'), 1.0)
    operands = (fn(3, 4, 'a'), fn(4, 4, 'b'), 1.0)
    near = (fn(5, 8, 'c'), fn(6, 8, 'c'), 0.9)
    done = (fn(7, 12, 'd'), Fn(8, 'f', 12, 12, 'd', 64, 'matched'), 1.0)
    assert sorted([done, operands, near, same], key=lambda t: _likeliest(*t)) == [same, near, operands, done]


def test_the_other_releases_code_is_tried_on_several_functions_at_once(built, tmp_path, monkeypatch):  # noqa: F811
    """Nearly all of a try is the compiler: one at a time took 20 minutes on a 2,000-function game."""
    import threading
    import time

    from decompnet.app import regional, workers

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    real = regional.plan
    def every_open_function(net_, proj, donor):  # the matched function's C offered to each open one
        p = real(net_, proj, donor)
        x = next(x for x, _, _ in p.pairs if x.name == "func_800007D0")
        p.pairs = [(x, y, 1.0) for _, y, _ in p.pairs if y.status == 'open']
        return p
    monkeypatch.setattr(regional, "plan", every_open_function)
    monkeypatch.setattr(workers, "WORKERS", 4)
    running, most, lock = [0], [0], threading.Lock()
    def slow(*_):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return 'miss'
    monkeypatch.setattr(regional, "_try_open", slow)
    result = regional.transfer(net, "other-release")
    assert result["tried"] >= 3 and most[0] > 1


def test_bringing_polished_code_does_not_sync_saved_files_per_function(built, tmp_path):  # noqa: F811
    """Each polished function read and wrote the saved files before and after it: 12 s a function on a finished
    2,000-function game. The progress watcher writes them afterwards."""
    from decompnet.app import regional

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, "other-release").startswith("✅ MATCH")
    polished = CLAMP.replace("s32 v,", "s32 value,").replace("(v ", "(value ").replace("return v;", "return value;")
    assert net.readability.submit(agent, slug, "func_800007D0", polished).startswith("✅")
    synced = []
    sync = net.progress.sync
    net.progress.sync = lambda *a, **k: (synced.append(a), sync(*a, **k))[1]
    assert regional.transfer(net, "other-release")["polished"] == 1
    assert len(synced) <= 4  # opening the two projects
    assert "value" in net._function("func_800007D0", "other-release")["matched_source"]


def test_a_function_a_helper_holds_waits_for_the_end_of_the_pass_and_is_reported(built, tmp_path):  # noqa: F811
    """A helper's claim comes first: the pass leaves the function, tries it once more at the end, and says how many
    it couldn't try, so a second pass when they're free picks them up."""
    import time

    from decompnet.app import regional

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    fn = net._function("func_800007D0", "other-release")
    helper, _ = net.local_agent("claude-opus-5.5-2")
    store = net.project_store(fn["project_id"])
    with store.tx() as db:
        db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], helper.id, time.time(), time.time() + 300))
    lines = []
    result = regional.transfer(net, "other-release", log=lines.append)
    assert result["held"] == 1 and result["matched"] == 0
    assert any("held by helpers" in line for line in lines)
    with store.tx() as db:
        db.execute("DELETE FROM claims")
    assert regional.transfer(net, "other-release")["matched"] == 1


def test_a_helpers_brief_shows_the_other_releases_c_for_its_function(built, tmp_path):  # noqa: F811
    """What Try its code couldn't match is a change between the releases: the brief gives the other release's C with
    this release's names, and where the instructions differ, so the helper makes just that change."""
    from decompnet.app import regional

    from .test_app_engine import CLAMP

    net, slug, agent = _two_releases(tmp_path)
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")
    proj = net.project_row("other-release")
    fn = net._function("func_800007D0", "other-release")
    lines = regional.brief(net, proj, fn)
    text = "\n".join(lines)
    assert "Another release of this game" in text and "clamp_s32" in text and "the same instructions" in text
    assert "func_800007D0" in text.split("```c", 1)[1]  # its C says this release's name for the function
    assert "Instructions that differ" not in text  # the same code: nothing to list
    assert "Another release of this game" in "\n".join(net._extra_brief(fn, proj))
    assert regional.brief(net, net.project_row(slug), net._function("clamp_s32", slug)) == []  # matched, or no link


def test_the_brief_lists_the_instructions_only_one_release_has():
    from types import SimpleNamespace

    from decompnet.app import regional
    from decompnet.scoring import Insn

    old = [Insn(0, 'lw', '$v0, 0x178($a0)', ''), Insn(4, 'jal', 'func_80027CFC', ''), Insn(8, 'jr', '$ra', '')]
    new = [Insn(0, 'lw', '$v0, 0x170($a0)', ''), Insn(4, 'jr', '$ra', '')]
    net = SimpleNamespace(_target_obj=lambda f, p: f)
    from unittest import mock
    with mock.patch('decompnet.scoring.disassemble', side_effect=lambda f: f):
        lines = regional._instruction_changes(net, {}, {'name': 'EUROPE'}, old, new)
    assert any('here `lw $v0, 0x170($a0)`, EUROPE `lw $v0, 0x178($a0)`' in line for line in lines)
    assert any('EUROPE only: `jal func_80027CFC`' in line for line in lines)
