"""Zero-filled targets stay rebuildable without becoming work for agents."""

import json
from pathlib import Path

import pytest

from decompnet import consoles
from decompnet.app.engine import RomNetwork
from decompnet.rom import Picked, split


@pytest.mark.parametrize("platform", ["n64", "psx", "ps2", "psp"])
def test_only_complete_nop_words_are_set_aside_on_every_mips_console(platform):
    console = consoles.get(platform)
    order = "big" if console.big_endian else "little"
    for size in (4, 8, 64):
        assert "only NOPs" in console.asm_reason(bytes(size))
        assert console.boundary_reason(bytes(size)) is None
    # A NOP at entry or in a return's delay slot is still ordinary compiled code.
    for words in ((0x03E00008, 0), (0, 0x03E00008, 0, 0)):
        assert console.asm_reason(b"".join(w.to_bytes(4, order) for w in words)) is None
    assert console.asm_reason(b"") is None
    assert console.asm_reason(bytes(5)) is None
    assert console.asm_reason((0x27BDFFE8).to_bytes(4, order)) is None


@pytest.mark.parametrize("platform", ["n64", "psx", "ps2", "psp"])
def test_import_keeps_padding_bytes_and_symbols_but_never_queues_them(tmp_path, monkeypatch, platform):
    console = consoles.get(platform)
    order = "big" if console.big_endian else "little"
    image = bytes(4) + (0x03E00008).to_bytes(4, order) + bytes(4)
    functions = [
        split.RomFunction("padding", 0x80001000, 0, "main",
                          [split.AsmLine(0, 0x80001000, 0, "nop", "")], Path()),
        split.RomFunction("empty", 0x80001004, 4, "main",
                          [split.AsmLine(4, 0x80001004, 0x03E00008, "jr", "$ra"),
                           split.AsmLine(8, 0x80001008, 0, "nop", "")], Path()),
    ]

    def fake_split(self, workdir, picked, **kwargs):
        return split.SplitResult(functions, [], [split.Segment("main", 0, len(image), 0x80001000)],
                                 {}, None, "", "")

    monkeypatch.setattr(type(console), "split_game", fake_split)
    monkeypatch.setattr(type(console), "after_import", lambda *args, **kwargs: None)
    monkeypatch.setattr(type(console), "debug_info", lambda *args, **kwargs: None)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    picked = Picked(console.id, image, "rom.bin", {
        "title": "Padding demo", "size": len(image), "game_code": "", "region": "US",
        "compiler_guess": "KMC", "sha1": "demo",
    }, console.big_endian)
    slug = net.import_split(console, picked, toolchain=console.compilers[0].id, flags=["-O2"], log=lambda _: None)
    padding = net._function("padding", slug)
    assert padding["status"] == "asm" and padding["target"] == bytes(4)
    assert "only NOPs" in json.loads(padding["meta"])["asm_code"]
    assert net.symbols(padding["project_id"]).resolve("padding") == 0x80001000
    agent, _ = net.local_agent("demo-agent")
    assert net.claim_row(agent, slug)["name"] == "empty"

    # Older imports get the same decision through Check again's shared classifier.
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',meta=NULL WHERE id=?", (padding["id"],))
    other, _ = net.local_agent("padding-agent")
    net.claim_row(other, slug, "padding")
    assert net.twins.set_aside_asm(net.project_row(slug)) == 1
    assert net._function("padding", slug)["target"] == bytes(4)
    assert not net.store.one("SELECT 1 FROM claims WHERE function_id=?", padding["id"])
    assert net.store.one("SELECT 1 FROM claims WHERE agent_id=?", agent.id)
    # An explicit operator override survives both classification and identical reimport.
    net.reopen("padding", slug)
    assert net.twins.set_aside_asm(net.project_row(slug)) == 0
    net.import_split(console, picked, reimport_slug=slug, log=lambda _: None)
    assert net._function("padding", slug)["status"] == "open"
    assert json.loads(net._function("padding", slug)["meta"])["reopened"]
