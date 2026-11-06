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
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")  # this game's name only
    assert net.twins.across_games("another-game") >= 1
    fn = net._function("func_800007D0", "another-game")
    assert fn["status"] == "matched" and "identical to clamp_s32 in " in fn["matched_source"]
    assert "clamp_s32" not in fn["matched_source"].split("*/", 1)[1]  # its own name, not the other game's
    assert net.twins.across_games("another-game") == 0  # nothing left to try
