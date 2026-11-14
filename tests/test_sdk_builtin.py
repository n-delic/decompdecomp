"""The N64's library signatures ship with the app, so a user without the SDK still has libultra found."""
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import consoles
from decompnet.app import sdk_builtin
from decompnet.app import sdk_library as sdk
from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_identity import identify

TABLE = Path(consoles.get("n64").sdk_table)
TOOL = Path(__file__).resolve().parent.parent / "tools" / "ultralib_signatures.py"


def _tool():
    spec = importlib.util.spec_from_file_location("ultralib_signatures", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows():
    return [line.split("\t") for line in TABLE.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]


def _project(net, tmp_path, slug="game", big_endian=1):
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                   (slug, slug, "mips", big_endian, str(tmp_path), json.dumps({"kind": "rom"})))
    return net.project_row(slug)


def test_the_shipped_table_is_ultralibs_rebuild_hashes_and_names_only():
    rows = _rows()
    header = TABLE.read_text(encoding="utf-8").splitlines()[0]
    assert _tool().COMMIT in header  # the table is the pinned commit's, as the tool writes it
    names = {name for _, _, name, _ in rows}
    # the libultra a game calls on every version, threads, messages, the controller, the matrix helpers
    assert {"osCreateThread", "osRecvMesg", "osSendMesg", "osContInit", "guMtxIdent", "osPiStartDma"} <= names
    assert len(rows) > 2000
    for fingerprint, size, name, builds in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", fingerprint) and int(size) >= 32 and name
        assert all(re.fullmatch(r"2\.0[IJKL]/lib(g?ultra)(_d|_rom)?", b) for b in builds.split(","))


def test_the_table_loads_once_as_its_own_corpus_and_reloads_when_it_changes(tmp_path, monkeypatch):
    table = tmp_path / "table.tsv"
    table.write_text("# decompals/ultralib 0123456789abcdef, test\n# fingerprint\tsize\tname\tbuilds\n"
                     f"{'a' * 40}\t64\tosOne\t2.0L/libgultra\n{'b' * 40}\t48\tosTwo\t2.0L/libgultra\n")
    monkeypatch.setattr(consoles.get("n64"), "sdk_table", str(table))
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    sdk_builtin.ensure(net)
    sdk_builtin.ensure(net)
    rows = net.store.all("SELECT corpus,arch,big_endian,name,size FROM sdk_signatures ORDER BY name")
    assert [tuple(r) for r in rows] == [("built in / decompals/ultralib 0123456789ab", "mips", 1, "osOne", 64),
                                        ("built in / decompals/ultralib 0123456789ab", "mips", 1, "osTwo", 48)]
    table.write_text("# decompals/ultralib fedcba9876543210, test\n" f"{'c' * 40}\t40\tosThree\t2.0K/libultra\n")
    sdk_builtin.ensure(net)
    assert [tuple(r) for r in net.store.all("SELECT corpus,name FROM sdk_signatures")] == [
        ("built in / decompals/ultralib fedcba987654", "osThree")]
    # Settings lists it as built in, and indexing (or clearing) the user's own SDK folders never removes it
    sdk.scan(net, [])
    libraries = sdk.state(net)["libraries"]
    assert [(r["corpus"], r["builtin"]) for r in libraries] == [("built in / decompals/ultralib fedcba987654", True)]


def test_a_game_finds_libultra_with_no_sdk_of_the_users(tmp_path, monkeypatch):
    rows = _rows()
    by_name = {name: (fingerprint, int(size)) for fingerprint, size, name, _ in rows}
    once = {fingerprint for fingerprint, _, _, _ in rows if sum(f == fingerprint for f, _, _, _ in rows) == 1}
    name = next(n for n in ("osRecvMesg", "osSendMesg", "osCreateThread") if by_name[n][0] in once)
    fingerprint, size = by_name[name]
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    assert not net.store.one("SELECT 1 FROM sdk_signatures")  # nothing indexed: a fresh install
    proj = _project(net, tmp_path)
    monkeypatch.setattr(net.twins, "prints", lambda p: {7: SimpleNamespace(key=fingerprint, size=size, asm="")})
    found = identify(net, proj, {})
    assert found[7]["names"] == [name] and found[7]["corpora"][0].startswith("built in / decompals/ultralib")
    # a little-endian MIPS game (a PS1 or PS2 one) with the same bytes isn't the N64's library
    other = _project(net, tmp_path, "other", big_endian=0)
    assert identify(net, other, {}) == {}


@pytest.mark.skipif(not os.environ.get("ULTRALIB"), reason="set ULTRALIB to a decompals/ultralib clone to rebuild")
def test_the_table_is_what_the_tool_rebuilds(tmp_path):
    """Rebuilding the table from ultralib gives the shipped one (slow: twenty archives with period compilers)."""
    out = tmp_path / "rebuilt.tsv"
    subprocess.run(["python", str(TOOL), os.environ["ULTRALIB"], "--out", str(out)], check=True)
    assert out.read_text(encoding="utf-8") == TABLE.read_text(encoding="utf-8")
