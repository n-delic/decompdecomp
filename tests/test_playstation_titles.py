"""Display metadata does not change a game's serial, bytes or matching identity."""
import struct

import pytest

from decompnet import consoles
from decompnet.consoles.playstation_titles import title
from decompnet.consoles.ps2 import elf

from .test_overlays import _iso
from .test_ps1 import _exe


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
    import runpy
    from pathlib import Path

    extract = runpy.run_path(str(Path(__file__).parents[1] / "tools/update_playstation_titles.py"))["extract"]
    source = '''game (
 name "Demo Quest (Europe)"
 serial "SLES-99998"
)
game (
 name "Demo Quest (Europe) (Rev 1)"
 serial "SLES-99998"
)
game (
 name "Different game"
 serial "SLES-99997"
)
game (
 name "Another game"
 serial "SLES-99997"
)
'''
    assert extract(source) == {"SLES-99998": "Demo Quest (Europe)"}
