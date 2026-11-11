"""The ROM's data as the port's resources (port/asset_map.py) and the N64 kit's Torch YAML for them, on a homemade
image: no game, no compiler, no Torch."""
import hashlib
import json

import pytest
import yaml

from decompnet.app import rom_explorer
from decompnet.app.engine import RomNetwork
from decompnet.consoles.n64.port import torch_files
from decompnet.network import NetworkError
from decompnet.port import asset_map
from decompnet.port.readiness import describe, readiness

SIZE = 0x3000
CODE = [(0x1000, 0x1800), (0x1900, 0x2000)]  # two code segments with a gap between them


def region(start, end, kind, **more):
    return {"start": start, "end": end, "size": end - start, "kind": kind, "evidence": [], **more}


def member(start, end, kind="compressed"):
    return region(start, end, kind, evidence=[{"archive": {"name": "a/b.bin"}, "label": "ZIP member: a/b.bin"}])


REGIONS = [
    region(0, 0x1000, "code"), region(0x1000, 0x1800, "code"),
    region(0x1800, 0x1880, "strings"), region(0x1880, 0x1900, "repeated bytes"),  # padding joins the strings
    region(0x1900, 0x2000, "code"),
    region(0x2000, 0x2400, "audio", direct_function_refs=["load_bank"]),
    region(0x2400, 0x2408, "unknown"),  # alignment between files
    member(0x2408, 0x2800), member(0x2800, 0x2900, "image"), region(0x2900, 0x2940, "container"),
    # 0x2940-0x2A00: a hole the scan says nothing of
    region(0x2A00, 0x2C00, "unknown"),
    region(0x2C00, SIZE, "repeated bytes"),  # the cartridge's trailing fill
]


def test_every_byte_outside_the_code_is_in_one_resource():
    result = asset_map.plan(REGIONS, CODE, SIZE, boot=0x1000)
    got = [(e["name"], e["offset"], e["size"]) for e in result["entries"]]
    assert got == [("data_1800", 0x1800, 0x100), ("audio_2000", 0x2000, 0x408),
                   ("archive_2408", 0x2408, 0x538), ("data_2940", 0x2940, 0x2C0)]
    assert result["entries"][0]["holds"] == {"strings": 0x80, "repeated bytes": 0x80}
    assert result["entries"][1]["loaders"] == ["load_bank"]
    assert result["entries"][2]["holds"] == {"compressed": 0x3F8, "image": 0x100, "container": 0x40}
    assert (result["fill"], result["code"], result["unknown"]) == (0x400, 0xF00, 0x2C8)  # the hole counts as unknown
    assert asset_map.check(result, CODE, SIZE) == []


def test_fill_between_two_code_segments_is_kept_and_a_broken_map_is_caught():
    regions = [region(0x1800, 0x1900, "repeated bytes"), region(0x2000, SIZE, "audio")]
    result = asset_map.plan(regions, CODE, SIZE, boot=0x1000)
    assert [(e["name"], e["size"]) for e in result["entries"]] == [("data_1800", 0x100), ("audio_2000", 0x1000)]
    broken = {**result, "entries": [dict(result["entries"][1], offset=0x1FF0)]}
    problems = asset_map.check(broken, CODE, SIZE)
    assert "0x1800-0x1900 is in no resource" in problems
    assert any("audio_2000 at 0x1FF0 overlaps" in p for p in problems)


def test_the_torch_config_is_yaml_torch_reads():
    result = asset_map.plan(REGIONS, CODE, SIZE, boot=0x1000)
    files = torch_files({"title": 'A "homemade" game', "slug": "game", "sha1": "ab" * 20,
                         "graphics": [{"name": "S2DEX"}, {"name": "F3DEX2"}]}, result["entries"])
    config = yaml.safe_load(files["config.yml"])["ab" * 20]
    assert config["name"] == 'A "homemade" game' and config["path"] == "game"
    assert config["config"]["gbi"] == "F3DEX2" and config["config"]["output"]["binary"] == "game.o2r"
    assets = yaml.safe_load(files["game/rom.yml"])
    assert assets["archive_2408"] == {"type": "BLOB", "offset": 0x2408, "size": 0x538, "symbol": "archive_2408"}
    assert list(assets) == [e["name"] for e in result["entries"]]


@pytest.fixture
def project(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    image = bytes(SIZE)
    (tmp_path / "game.z64").write_bytes(image)
    cfg = dict(kind="rom", platform="n64", rom=dict(file="game.z64", title="Homemade", sha1=hashlib.sha1(image).hexdigest()),
               segments=[dict(name=f"s{i}", rom_start=lo, rom_end=hi, vram=0x80000400 + lo) for i, (lo, hi) in
                         enumerate(CODE)], compiler={"toolchain": "gcc-2.7.2-kmc", "flags": []})
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                   ("game", "game", "mips", 1, str(tmp_path), json.dumps(cfg)))
    yield net, tmp_path
    if hasattr(net, "autosave"):
        net.autosave.close()


def test_the_map_is_written_into_the_project_and_readiness_follows_it(project, monkeypatch):
    net, root = project
    with net.progress.view():
        with pytest.raises(NetworkError, match="hasn't scanned this ROM"):
            asset_map.write(net, "game")
        assert readiness(net, "game")["assets"] is None
    monkeypatch.setattr(rom_explorer, "listing", lambda net, slug: {
        "available": True, "stale": False, "size": SIZE, "regions": REGIONS})
    with net.progress.view():
        report = readiness(net, "game")
    assert report["assets"]["resources"] == 4 and not report["assets"]["written"]
    assert any("no asset map for the kit's extractor yet (4 resources" in b for b in report["blockers"])
    with net.progress.view():
        written = asset_map.write(net, "game")
    assert written["files"] == ["config.yml", "game/rom.yml"]
    assert (root / "port/assets/game/rom.yml").read_text(encoding="utf-8").count("type: BLOB") == 4
    with net.progress.view():
        report = readiness(net, "game")
    assert report["assets"]["written"] and not any("asset map" in b for b in report["blockers"])
    assert "Asset map (written): 4 resources" in describe(report)
    # a new scan that sees more makes the written map out of date
    monkeypatch.setattr(rom_explorer, "listing", lambda net, slug: {
        "available": True, "stale": False, "size": SIZE, "regions": REGIONS[:-2]})
    with net.progress.view():
        assert any("older than ROM Explorer's scan" in b for b in readiness(net, "game")["blockers"])


def test_a_real_scan_maps_the_homemade_image(project):
    from tests.test_rom_explorer import Log
    net, root = project
    rom_explorer.run(net, "game", Log())
    with net.progress.view():
        result = asset_map.build(net, "game")
    assert asset_map.check(result, CODE, SIZE) == [] and result["entries"]
