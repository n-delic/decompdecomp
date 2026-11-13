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
    assert report["assets"]["written"] and not any("no asset map" in b for b in report["blockers"])
    assert any("hasn't been extracted from your ROM yet" in b for b in report["blockers"])
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


def stand_in_torch(corrupt=""):
    """Writes an .o2r as Torch does (a ZIP of resources: a 0x40-byte header, "OBLB" at 4, then size and bytes) from
    the asset map's BLOBs, without Torch."""
    import re
    import struct
    import zipfile

    def extract(assets, image, out, log=print):
        out.mkdir(parents=True, exist_ok=True)
        data = image.read_bytes()
        text = (assets / "game" / "rom.yml").read_text(encoding="utf-8")
        archive = out / "game.o2r"
        with zipfile.ZipFile(archive, "w") as z:
            for name, off, size in re.findall(r"^(\w+):\n  type: BLOB\n  offset: 0x(\w+)\n  size: 0x(\w+)", text, re.M):
                body = data[int(off, 16):int(off, 16) + int(size, 16)]
                if name == corrupt:
                    body = body[:-1] + b"!"
                z.writestr(f"rom/{name}", bytes(4) + b"BLBO" + bytes(0x38) + struct.pack("<I", len(body)) + body)
        return archive
    return extract


def test_extracting_checks_every_resource_against_the_rom(project, monkeypatch):
    import dataclasses

    from decompnet import consoles
    from decompnet.consoles.n64.port import KIT
    net, root = project
    monkeypatch.setattr(rom_explorer, "listing", lambda net, slug: {
        "available": True, "stale": False, "size": SIZE, "regions": REGIONS})
    n64 = consoles.get("n64")
    monkeypatch.setattr(n64, "port_kit", dataclasses.replace(KIT, extract=stand_in_torch("audio_2000")))
    report = asset_map.extract(net, "game", print)
    assert (report["mapped"], report["total"], report["different"]) == (3, 4, ["audio_2000"])
    assert (root / "build/port/game.o2r").is_file() and (root / "port/assets/config.yml").is_file()
    with net.progress.view():
        blockers = readiness(net, "game")["blockers"]
    assert any("1 of 4 resources didn't come out of the extractor as the ROM's bytes: audio_2000" in b for b in blockers)
    monkeypatch.setattr(n64, "port_kit", dataclasses.replace(KIT, extract=stand_in_torch()))
    assert asset_map.extract(net, "game", print)["mapped"] == 4
    with net.progress.view():
        report = readiness(net, "game")
    assert not any("asset map" in b or "come out of the extractor" in b for b in report["blockers"])
    assert "Extracted: 4 of 4 are the ROM's bytes" in describe(report)
    # a new map (a new scan) needs extracting again
    monkeypatch.setattr(rom_explorer, "listing", lambda net, slug: {
        "available": True, "stale": False, "size": SIZE, "regions": REGIONS[:-2]})
    asset_map.write(net, "game")
    with net.progress.view():
        assert any("hasn't been extracted from your ROM since it changed" in b
                   for b in readiness(net, "game")["blockers"])


def test_the_pinned_torch_extracts_the_map(project, monkeypatch):
    from decompnet import toolchains
    if not toolchains.installed("torch"):
        pytest.skip("The pinned Torch is not installed; never built here.")
    net, root = project
    image = bytes(range(256)) * (SIZE // 256)  # bytes worth comparing; Torch picks the config by their SHA-1
    (root / "game.z64").write_bytes(image)
    proj = net.project_row("game")
    cfg = net.cfg(proj)
    cfg["rom"]["sha1"] = hashlib.sha1(image).hexdigest()
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
    monkeypatch.setattr(rom_explorer, "listing", lambda net, slug: {
        "available": True, "stale": False, "size": SIZE, "regions": REGIONS})
    report = asset_map.extract(net, "game", print)
    assert (report["mapped"], report["total"]) == (4, 4), report
