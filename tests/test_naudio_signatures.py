"""The N64's n_audio signatures ship with the app, so a game's n_audio is found without the user's SDK.

ultralib doesn't rebuild n_audio (libgn_audio.a), so a racing game with no SDK indexed had its 50 n_audio functions
unfound. Its table is fingerprints and names of the SDK's own archives, made by tools/naudio_signatures.py.
"""
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import consoles
from decompnet.app import sdk_builtin
from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_identity import identify

N64 = consoles.get("n64")
TABLE = Path(N64.sdk_tables[0])
TOOL = Path(__file__).resolve().parent.parent / "tools" / "naudio_signatures.py"


def _tool():
    spec = importlib.util.spec_from_file_location("naudio_signatures", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows():
    return [line.split("\t") for line in TABLE.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]


def test_the_shipped_table_is_hashes_and_names_of_n_audio_only():
    header = TABLE.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("# n_audio 2.0J-2.0L,") and "no code" in header
    rows = _rows()
    names = {name for _, _, name, _ in rows}
    # the synthesizer, and the sequence and sound players no public C rebuilds as the SDK shipped them
    assert {"n_alSynNew", "n_alAudioFrame", "n_alSndpNew", "n_alSndpPlay", "n_alCSPNew", "n_alSeqpNew"} <= names
    assert len(rows) > 200  # (less the fingerprints the ultralib table names)
    for fingerprint, size, name, builds in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", fingerprint) and int(size) >= 32 and name
        assert all(re.fullmatch(r"2\.0[JKL]/libgn_audio(_d)?", b) for b in builds.split(","))
    # a static function in two objects keeps both copies (the sequence players' __n_setUsptFromTempo)
    assert len({(f, s) for f, s, n, _ in rows if n == "__n_setUsptFromTempo"}) >= 2


def test_both_tables_load_as_corpora_of_their_own(tmp_path, monkeypatch):
    first, second = tmp_path / "ultralib.tsv", tmp_path / "naudio.tsv"
    first.write_text(f"# decompals/ultralib 0123456789abcdef, test\n{'a' * 40}\t64\tosOne\t2.0L/libgultra\n")
    second.write_text(f"# n_audio 2.0J-2.0L, test\n{'b' * 40}\t48\tn_alOne\t2.0L/libgn_audio\n")
    monkeypatch.setattr(N64, "sdk_table", str(first))
    monkeypatch.setattr(N64, "sdk_tables", (str(second),))
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    sdk_builtin.ensure(net)
    assert sorted(tuple(r) for r in net.store.all("SELECT corpus,name FROM sdk_signatures")) == [
        ("built in / decompals/ultralib 0123456789ab", "osOne"), ("built in / n_audio 2.0J-2.0L", "n_alOne")]
    assert sdk_builtin.corpora(net) == {"built in / decompals/ultralib 0123456789ab", "built in / n_audio 2.0J-2.0L"}
    # a new n_audio table replaces only its own rows
    second.write_text(f"# n_audio 2.0J-2.0L, test\n{'c' * 40}\t40\tn_alTwo\t2.0L/libgn_audio\n")
    net.__dict__.pop("_sdk_builtin")
    sdk_builtin.ensure(net)
    assert sorted(r["name"] for r in net.store.all("SELECT name FROM sdk_signatures")) == ["n_alTwo", "osOne"]


def test_a_game_finds_n_audio_with_no_sdk_of_the_users(tmp_path, monkeypatch):
    rows = _rows()
    counts = {}
    for f, _, _, _ in rows:
        counts[f] = counts.get(f, 0) + 1
    fingerprint, size, name, _ = next(r for r in rows if r[2] == "n_alSndpNew" and counts[r[0]] == 1)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES('game','game','mips',1,?,'{\"kind\":\"rom\"}')",
                   (str(tmp_path),))
    monkeypatch.setattr(net.twins, "prints", lambda p: {7: SimpleNamespace(key=fingerprint, size=int(size), asm="")})
    found = identify(net, net.project_row("game"), {})
    assert found[7]["names"] == [name] and found[7]["corpora"] == ["built in / n_audio 2.0J-2.0L"]


def test_the_tool_labels_archives_by_their_sdk_folder():
    tool = _tool()
    assert tool.label(Path("/sdk/NINTENDO 64 OS2.0K/usr/lib/libgn_audio.a")) == "2.0K/libgn_audio"
    assert tool.label(Path("/sdk/Nintendo_64_OS-PC_v2.0L/lib/LIBGN_AUDIO_D.A")) == "2.0L/libgn_audio_d"
    for wrong in ("/sdk/lib/libgn_audio.a", "/sdk/2.0K/libgultra.a"):
        with pytest.raises(SystemExit):
            tool.label(Path(wrong))


def test_the_tool_keeps_every_objects_copy(monkeypatch, tmp_path):
    tool = _tool()
    archive = tmp_path / "2.0K" / "libgn_audio.a"
    archive.parent.mkdir()
    archive.write_bytes(b"!<arch>\n")
    monkeypatch.setattr(tool, "prints", lambda path: {("a" * 40, 52, "__n_static"), ("b" * 40, 52, "__n_static")})
    lines = tool.table([archive])
    assert lines[0].startswith("# n_audio 2.0K-2.0K,") and len(lines) == 4
    # what the ultralib table names already stays out: one fingerprint, two names, would be no identity
    assert len(tool.table([archive], {"a" * 40})) == 3


def test_the_tool_labels_libultra_archives_and_leaves_out_what_other_tables_name():
    tool = _tool()
    assert tool.label(Path("/sdk/2.0I Nintendo_64_Developers_OS-Library_IRIX_v2.0I/usr/lib/libultra_d.a"), "libultra") == "2.0I/libultra_d"
    assert tool.label(Path("/sdk/NINTENDO 64 OS2.0L/lib/libgultra_rom.a"), "libultra") == "2.0L/libgultra_rom"
    for wrong in ("/sdk/2.0K/libgn_audio.a", "/sdk/2.0K/libultrahost.a", "/sdk/lib/libultra.a"):
        with pytest.raises(SystemExit):
            tool.label(Path(wrong), "libultra")


def test_the_libultra_table_is_the_sdks_own_builds_beyond_the_other_tables():
    table = next(Path(t) for t in N64.sdk_tables if Path(t).name == "libultra_signatures.tsv")
    lines = table.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# libultra 2.0H-2.0L,") and "no code" in lines[0]
    rows = [line.split("\t") for line in lines if not line.startswith("#")]
    assert len(rows) > 300
    names = {name for _, _, name, _ in rows}
    assert {"__ll_mod", "__osDequeueThread", "alUnlink", "alSeqFileNew"} <= names
    others = {line.split("\t")[0] for other in (Path(N64.sdk_table), Path(N64.sdk_tables[0]))
              for line in other.read_text(encoding="utf-8").splitlines() if not line.startswith("#")}
    for fingerprint, size, name, builds in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", fingerprint) and int(size) >= 32 and name and fingerprint not in others
        assert all(re.fullmatch(r"2\.0[H-L]/libg?ultra(_d|_rom)?", b) for b in builds.split(","))


def test_no_fingerprint_is_in_both_tables():
    ultralib = {line.split("\t")[0] for line in Path(N64.sdk_table).read_text(encoding="utf-8").splitlines()
                if not line.startswith("#")}
    assert not ultralib & {f for f, _, _, _ in _rows()}


def test_the_2_0d_table_names_library_code_no_archive_has_and_overlaps_no_other_table():
    table = next(Path(t) for t in N64.sdk_tables if Path(t).name == "libultra_2_0d_signatures.tsv")
    lines = table.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# libultra 2.0D,") and "no code" in lines[0]
    rows = [line.split("\t") for line in lines if not line.startswith("#")]
    assert len(rows) >= 40
    others = {line.split("\t")[0] for other in (Path(N64.sdk_table), *(Path(t) for t in N64.sdk_tables if Path(t) != table))
              for line in other.read_text(encoding="utf-8").splitlines() if not line.startswith("#")}
    assert len({r[0] for r in rows}) == len(rows)  # one name per fingerprint
    for fingerprint, size, name, builds in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", fingerprint) and int(size) >= 32 and name and fingerprint not in others
        assert builds.startswith("2.0D games")
    assert {"__osViInit", "osSpTaskLoad", "__osSpSetPc"} <= {r[2] for r in rows}
