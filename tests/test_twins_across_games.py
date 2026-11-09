"""Matched code from one of the user's games tried on another game's identical function (Twins.across_games):
libraries come in the same code from game to game. The second game here is the demo again under another name,
with its own names, so the names have to be swapped as its own symbols say."""

import json
import shutil
from pathlib import Path

from decompnet.app.engine import RomNetwork

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def copy_project(net, slug, new_slug):
    """The same game as another project: copy its ROM, functions, symbols and headers under a new id."""
    src = net._project(slug)
    root = Path(src["root"]) / new_slug
    cfg = json.loads(src["config"])
    rom = Path(cfg["rom"]["file"])
    destination = root / rom
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(src["root"]) / rom, destination)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         (new_slug, "Another Game", "", src["arch"], src["big_endian"], str(root),
                          src["config"])).lastrowid
        for table, cols in (("functions", "name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset"),
                            ("symbols", "name,address,size,kind"),
                            ("context_versions", "version,header,agent_id,message,created_at,unit")):
            db.execute(f"INSERT INTO {table}(project_id,{cols}) SELECT ?,{cols} FROM {table} WHERE project_id=?",
                       (pid, src["id"]))
    return pid


def test_a_match_in_one_game_matches_the_same_function_in_another(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    copy_project(net, slug, "another-game")  # the split is in both: it's the same code
    source_pid = net._project(slug)["id"]
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")  # this game's name only
    hints = net.twins.suggestions("another-game", "func_800007D0")
    assert any(h["name"] == "clamp_s32" and h["kind"] == "verified C in another game" for h in hints)
    assert net.twins.across_games("another-game") >= 1
    results = net.twins.cross_game_results("another-game")
    assert results["counts"]["match"] >= 1
    assert any(r["source"] == "clamp_s32" and r["target"] == "func_800007D0" for r in results["recent"])
    fn = net._function("func_800007D0", "another-game")
    assert fn["status"] == "matched" and "identical to clamp_s32 in " in fn["matched_source"]
    assert "clamp_s32" not in fn["matched_source"].split("*/", 1)[1]  # its own name, not the other game's
    assert net.twins.across_games("another-game") == 0  # nothing left to try
    net.remove_project(slug)
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_index WHERE project_id=?", source_pid)["n"] == 0


def test_local_object_signatures_are_name_hints_not_matches(built, tmp_path):  # noqa: F811
    from pathlib import Path

    from decompnet.app.twins import _ar_members
    from decompnet.network import NetworkError

    obj = Path(__file__).parents[1] / "projects/demo-rom/build/libc.o"
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    count = net.twins.import_sdk_objects(obj, "demo libc 1")
    assert count > 0
    assert net.store.one("SELECT COUNT(*) AS n FROM sdk_signatures WHERE corpus=?", "demo libc 1")["n"] == count
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    signatures = {r["fingerprint"]: r["name"] for r in net.store.all(
        "SELECT fingerprint,name FROM sdk_signatures WHERE corpus=?", "demo libc 1")}
    prints = net.twins.prints(net._project(slug))
    fn = next(r for r in net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open'",
                                        net._project(slug)["id"])
              if r["id"] in prints and prints[r["id"]].key in signatures)
    assert any(h["name"] == signatures[prints[fn["id"]].key] and h["kind"] == "SDK object signature; name only"
               for h in net.twins.suggestions(slug, fn["name"]))
    assert fn["status"] == "open"  # importing a signature never marks C as matched
    assert _ar_members(b"!<arch>\n" + b"x" * 48 + b"2         " + b"`\n" + b"ab") == [b"ab"]
    try:
        _ar_members(b"not an archive")
    except NetworkError:
        pass
    else:
        assert False, "invalid archives must be rejected"


def test_imported_c_is_rejudged_in_rom_and_misses_are_remembered(built, tmp_path, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from decompnet.app.twins import Print

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("autopilot")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    donor_id = copy_project(net, slug, "imported-library")
    proj, donor = net._project(slug), net._project("imported-library")
    b = net._function("func_800007D0", slug)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE project_id=? AND name=?", (donor_id, b["name"]))
    mine = net.twins.prints(proj)
    fingerprint = mine[b["id"]]
    a = net._function(b["name"], "imported-library")
    monkeypatch.setattr(net.forks, "get", lambda _: (donor, object(), {"platform": "n64"}))
    monkeypatch.setattr(net.forks, "is_asm", lambda *_: False)
    monkeypatch.setattr(net.twins, "_fork_prints", lambda _, **kwargs: {
        a["id"]: Print(fingerprint.key, fingerprint.refs, fingerprint.size, name="library_clamp")})
    # Even a donor marked matched cannot bypass the destination's actual compiler and judge.
    monkeypatch.setattr(net.forks, "_current_source", lambda *_: "int library_clamp(int x) { return 123; }")
    wanted = {fingerprint.key: [b["id"]]}
    stopped = SimpleNamespace(stop=SimpleNamespace(is_set=lambda: False), reuse_limit=0,
                              reuse_attempts=0, reuse_deadline=float("inf"))
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, stopped) == 0
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_attempts")["n"] == 0
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, None) == 0
    assert net._function(b["name"], slug)["status"] == "open"
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, None) == 0
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_attempts")["n"] == 1
    monkeypatch.setattr(net.forks, "_current_source", lambda *_: CLAMP.replace(b["name"], "library_clamp"))
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, None) == 1
    result = net._function(b["name"], slug)
    assert result["status"] == "matched" and "in Another Game" in result["matched_source"]
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_attempts WHERE outcome='match'")["n"] == 1
    job = SimpleNamespace(stop=SimpleNamespace(is_set=lambda: True))
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, job) == 0


def test_exact_donor_names_are_tracked_and_do_not_overwrite_names(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("tester")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    copy_project(net, slug, "target")
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")
    before = next(r for r in net.function_map("target") if r["name"] == "func_800007D0")
    assert not before["named"] and not before["renamed"]
    assert net.twins.reuse_names("target") == 1
    fn = net._function("clamp_s32", "target")
    naming = json.loads(fn["meta"])["naming"]
    assert naming["method"] == "exact_cross_game" and naming["source_project"] == slug
    assert naming["original_name"] == "func_800007D0"
    after = next(r for r in net.function_map("target") if r["name"] == "clamp_s32")
    assert after["named"] and after["renamed"]
    assert net.twins.reuse_names("target") == 0
    assert net.rename_symbol(agent, "target", "clamp_s32", "my_clamp").startswith("✅")
    assert net.twins.reuse_names("target") == 0
    assert json.loads(net._function("my_clamp", "target")["meta"])["naming"]["original_name"] == "func_800007D0"
    # Even an explicit rename to another generated-looking name is owned by the user.
    assert net.rename_symbol(agent, "target", "my_clamp", "sub_800007D0").startswith("✅")
    assert net.twins.reuse_names("target") == 0



def test_conflicting_exact_donor_names_are_not_guessed(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("tester")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    copy_project(net, slug, "target")
    copy_project(net, slug, "second-donor")
    for donor, name in [(slug, "clamp_s32"), ("second-donor", "limit_s32")]:
        assert net.compile_and_diff(agent, "func_800007D0", CLAMP, donor).startswith("✅ MATCH")
        assert net.rename_symbol(agent, donor, "func_800007D0", name).startswith("✅")
    assert net.twins.reuse_names("target") == 0
    assert net._function("func_800007D0", "target")["name"] == "func_800007D0"
    assert net.twins.reuse_names("target", donor_slug="not-a-project") == 0
    assert net.twins.reuse_names("target", donor_slug=slug) == 1
