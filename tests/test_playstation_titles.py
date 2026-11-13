"""Display metadata does not change a game's serial, bytes or matching identity."""
import struct

import pytest

from decompnet import consoles
from decompnet.consoles import playstation_titles
from decompnet.consoles.playstation_titles import title
from decompnet.consoles.ps2 import elf

from .test_overlays import _iso
from .test_ps1 import _exe


def _dat(*games):
    return "".join(f'game (\n name "{name}"\n serial "{serial}"\n)\n' for serial, name in games)


@pytest.fixture(autouse=True)
def titles(tmp_path, monkeypatch):
    """Small lists in libretro's format, in place of the pinned download."""
    folder = tmp_path / "titles"
    folder.mkdir()
    (folder / "ps.dat").write_text(_dat(("SLUS-00001", "Air Combat (USA)")), encoding="utf-8")
    (folder / "ps2.dat").write_text(_dat(("SLES-50906", "Master Rallye (Europe)")), encoding="utf-8")
    monkeypatch.setenv("DECOMPNET_PLAYSTATION_TITLES", str(folder))
    monkeypatch.setattr(playstation_titles, "_cache", {})
    return folder


def _ps2_program():
    data = bytearray(4096)
    data[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HHI", data, 16, 2, 8, 1)
    struct.pack_into("<I", data, 24, 0x100000)
    return bytes(data)


@pytest.mark.parametrize("platform,serial,program,prefix", [
    ("ps", "SLUS_000.01", _exe(), b"BOOT = cdrom:\\"),
    ("ps2", "SLES_509.06", _ps2_program(), b"BOOT2 = cdrom0:\\"),
])
def test_known_titles_are_offline_and_keep_the_disc_identity(platform, serial, program, prefix, tmp_path):
    console = consoles.get(platform)
    standalone = console.read_game(program, serial)
    image = bytearray(_iso({"SYSTEM.CNF": prefix + serial.encode() + b";1\r\n", serial: program}))
    image[16 * 2048 + 40:16 * 2048 + 72] = b"WRONG_LABEL".ljust(32, b" ")
    path = tmp_path / "disc.iso"
    path.write_bytes(image)
    for picked in (standalone, console.read_game(image), console.read_game(path)):
        assert picked.info["title"].startswith("Air Combat" if platform == "ps" else "Master Rallye")
        assert picked.info["game_code"] == serial.replace("_", "-").replace(".", "")
        assert picked.data == program
        assert picked.info["sha1"] == standalone.info["sha1"]


@pytest.mark.parametrize("platform,program,prefix", [
    ("ps", _exe(), b"BOOT = cdrom:\\"),
    ("ps2", _ps2_program(), b"BOOT2 = cdrom0:\\"),
])
@pytest.mark.parametrize("label,expected", [(b"HOMEBREW_QUEST", "HOMEBREW QUEST"), (b"", "SLES-99998"),
                                          (b"CDROM", "SLES-99998"), (b"SLES_999.98", "SLES-99998"),
                                          (b"BAD\xffLABEL", "SLES-99998")])
def test_unknown_discs_use_only_useful_labels(platform, program, prefix, label, expected):
    image = bytearray(_iso({"SYSTEM.CNF": prefix + b"SLES_999.98;1\r\n", "SLES_999.98": program}))
    image[16 * 2048 + 40:16 * 2048 + 72] = label.ljust(32, b" ")
    picked = consoles.get(platform).read_game(image)
    assert picked.info["title"] == expected
    assert picked.info["game_code"] == "SLES-99998"


def test_unknown_executables_keep_their_original_names():
    assert elf.read_elf(_ps2_program(), "SLES_999.98")[1]["title"] == "SLES-99998"
    assert title("ps", "SLES-50906", "Unknown") == "Unknown"  # consoles have separate catalogs


@pytest.mark.parametrize("platform,code,expected", [("ps", "SLUS-00001", "Air Combat"),
                                                   ("ps2", "SLES-50906", "Master Rallye")])
def test_old_serial_titles_resolve_without_overwriting_custom_names(platform, code, expected):
    console = consoles.get(platform)
    info = {"title": code, "game_code": code}
    assert console.display_title(code, info).startswith(expected)
    assert info["title"] == code
    assert console.display_title("My custom name", info) == "My custom name"
    assert console.display_title("SLES-99998", {"game_code": "SLES-99998"}) == "SLES-99998"
    assert console.display_title("Untitled", {}) == "Untitled"


def test_other_consoles_keep_stored_titles():
    assert consoles.get("n64").display_title("My game", {"game_code": "SLUS-00001"}) == "My game"


def test_existing_project_views_resolve_titles_without_writing_the_database(tmp_path):
    import json

    from decompnet.app.engine import RomNetwork
    from decompnet.app.project_labels import projects
    from tests.test_loading import tiny_game

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    tiny_game(net)
    row = net.store.one("SELECT * FROM projects WHERE slug='game'")
    cfg = json.loads(row["config"])
    cfg["rom"].update(title="SLES-50906", game_code="SLES-50906")
    with net.store.tx() as db:
        db.execute("UPDATE projects SET name=?, config=? WHERE slug='game'", ("SLES-50906", json.dumps(cfg)))
    before = dict(net.store.one("SELECT * FROM projects WHERE slug='game'"))
    assert net.summary("game")["title"].startswith("Master Rallye")
    with net.summary_batch():
        assert net.summary("game")["title"].startswith("Master Rallye")
    assert projects(net)[0]["name"].startswith("Master Rallye")
    overview = net._overview("game", before, room_members=False, quick=True)
    assert overview["title"].startswith("Master Rallye")
    assert overview["rom"]["title"].startswith("Master Rallye")
    assert dict(net.store.one("SELECT * FROM projects WHERE slug='game'")) == before


def test_catalog_generation_omits_ambiguous_serials_and_combines_revisions():
    source = _dat(("SLES-99998", "Demo Quest (Europe)"), ("SLES-99998", "Demo Quest (Europe) (Rev 1)"),
                  ("SLES-99997", "Different game"), ("SLES-99997", "Another game"))
    assert playstation_titles.extract(source) == {"SLES-99998": "Demo Quest (Europe)"}


def test_parsed_lists_are_kept_beside_the_download(titles):
    assert title("ps2", "SLES-50906", "?") == "Master Rallye (Europe)"
    assert (titles / "ps2.json").is_file()
    (titles / "ps2.dat").unlink()  # a later run reads the parsed copy
    playstation_titles._cache.clear()
    assert title("ps2", "SLES-50906", "?") == "Master Rallye (Europe)"


def test_without_the_lists_titles_fall_back_and_showing_one_never_downloads(tmp_path, monkeypatch):
    from decompnet import toolchains

    monkeypatch.delenv("DECOMPNET_PLAYSTATION_TITLES")
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "tools"))
    monkeypatch.setattr(playstation_titles, "_tried", False)
    asked = []
    monkeypatch.setattr(toolchains, "ensure", lambda tid, log=print: asked.append(tid) or (_ for _ in ()).throw(OSError("offline")))
    assert title("ps2", "SLES-50906", "SLES-50906") == "SLES-50906"
    assert consoles.get("ps2").display_title("SLES-50906", {"game_code": "SLES-50906"}) == "SLES-50906"
    assert asked == []
    assert playstation_titles.import_title("ps2", "SLES-50906", "LABEL") == "LABEL"  # offline: the import goes on
    playstation_titles.import_title("ps2", "SLES-50906", "LABEL")
    assert asked == ["playstation-titles"]  # once per run


def test_the_pinned_lists_install_as_plain_files(tmp_path, monkeypatch):
    from decompnet import toolchains

    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "tools"))
    tc = toolchains.get("playstation-titles")
    assert {p["copy"] for p in tc.parts} == {"ps.dat", "ps2.dat", "LICENSE"} and all(p["sha256"] for p in tc.parts)
    bodies = {p["url"]: p["copy"].encode() for p in tc.parts}
    monkeypatch.setattr(toolchains, "_download", lambda url, dest, log: dest.write_bytes(bodies[url]))
    monkeypatch.setattr(toolchains, "_sha256", lambda p: next(q["sha256"] for q in tc.parts if q["file"] == p.name))
    folder = toolchains.install("playstation-titles", log=lambda *_: None)
    assert (folder / "ps2.dat").read_bytes() == b"ps2.dat" and (folder / "LICENSE").is_file()
