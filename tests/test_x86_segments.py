"""Narrow DOS fixups need symbolic proof, even when a wrong literal has identical instruction bytes."""

import json
import os
import struct
from pathlib import Path

import pytest

from decompnet import kits, toolchains
from decompnet.arch import x86_segments
from decompnet.arch.x86_omf import OMFError, convert
from decompnet.consoles.pc import scratch
from decompnet.forks import objdiff
from tests.test_pc_linear import demo_le
from tests.test_x86_omf import module


def selector_le():
    data = bytearray(demo_le())
    struct.pack_into("<I", data, 0x80 + 28, 0)
    struct.pack_into("<I", data, 0x80 + 196, 5)
    struct.pack_into("<3I", data, 0x80 + 256, 0, 5, 5)
    data[0x80 + 272:0x80 + 277] = struct.pack("<BBhB", 2, 0, 2, 2)
    data[0x1000:0x1005] = bytes.fromhex("66b80000c3")
    return bytes(data)


def test_target_proofs_reject_an_identical_object_without_a_loader_fixup(tmp_path):
    pe = scratch.read_pe(selector_le())
    fn = scratch._entry(pe, scratch.Function(0x10000, 5, "_func_00010000"))
    delink = scratch._template_tool("delink")
    target, base = tmp_path / "target.obj", tmp_path / "base.obj"
    target.write_bytes(delink.coff([fn], pe.data))
    base.write_bytes(delink.coff([{k: v for k, v in fn.items() if k != "segments"}], pe.data))
    assert fn["name"] in x86_segments.conflicts(target, base)
    cfg = {"units": [{"name": "unit", "target_path": "target.obj", "base_path": "base.obj"}]}
    (tmp_path / "objdiff.json").write_text(json.dumps(cfg))
    report = {"units": [{"name": "unit", "functions": [{"name": fn["name"], "size": 5, "fuzzy_match_percent": 100}]}]}
    assert objdiff.subtract_conflicts(tmp_path, report)["units"][0]["functions"][0]["fuzzy_match_percent"] < 100
    base.write_bytes(target.read_bytes())
    assert not x86_segments.conflicts(target, base)


def test_resolver_checks_object_identity_full_offsets_and_missing_fixups():
    pe = scratch.read_pe(selector_le())
    image = scratch._template_tool("linear").Linear(selector_le())
    fn = scratch._entry(pe, scratch.Function(0x10000, 5, "_func_00010000"))
    info = {"units": {"unit": [fn]}}
    names = scratch._template_tool("names")
    resolver = x86_segments.Resolver(info, image, names)
    with pytest.raises(ValueError, match="literal"):
        resolver.finish(["func_00010000_"])
    with pytest.raises(ValueError, match="different LE object"):
        resolver.resolve("func_00010000_", 2, 2, "_D_00010000", 0)
    assert resolver.resolve("func_00010000_", 2, 2, "_D_00020000", 0) == 0
    assert len(resolver.finish(["func_00010000_"])) == 1
    fn["segments"] = [[2, 5, 0x20000, 0x10001, 7]]
    resolver = x86_segments.Resolver(info, image, names)
    with pytest.raises(ValueError, match="offset"):
        resolver.resolve("func_00010000_", 2, 1, "_D_00020001", 0)
    assert resolver.resolve("func_00010000_", 2, 1, "_D_00030001", 0) == 7


def test_omf_16_bit_offset_is_resolved_with_its_full_target_and_proof():
    image = scratch._template_tool("linear").Linear(selector_le())
    fn = {"name": "_func_00010000", "addr": 0x10000, "segments": [[0, 5, 0x20000, 7, 0xABCD]]}
    info = {"units": {"unit": [fn]}, "names": {"func_00010000": "function", "D_00020000": "outside"}}
    resolver = x86_segments.Resolver(info, image, scratch._template_tool("names"))
    data = convert(module(bytes.fromhex("c4 00 16 01 01")), segmented=resolver)
    rebuild = scratch._template_tool("rebuild")
    sections, _ = rebuild.read_coff(data)
    assert sections[0]["data"] == bytes.fromhex("cdab0000c3")
    assert not sections[0]["relocs"]
    assert x86_segments.proofs(data)[fn["name"]] == {x86_segments.marker(fn["name"], fn["segments"][0])}
    fn["segments"][0][3] = 0x10007  # same low bits, different actual offset: must not pass
    resolver = x86_segments.Resolver(info, image, scratch._template_tool("names"))
    with pytest.raises(OMFError, match="offset"):
        convert(module(bytes.fromhex("c4 00 16 01 01")), segmented=resolver)


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_real_selector_compile_judge_export_and_existing_project_migration(tmp_path, monkeypatch, language):
    own = os.environ.get("DECOMP_TEST_WATCOM_C")
    if not own or not all(toolchains.installed(t) for t in ("ghidra", "jdk", "wibo", "objdiff-cli")):
        pytest.skip("needs the local Watcom copy and pinned Ghidra/JDK/wibo/objdiff")
    monkeypatch.setenv("HOME", str(tmp_path))
    original = toolchains.local_folders
    monkeypatch.setattr(toolchains, "local_folders", lambda kid: [Path(own)] if kid == "watcom" else original(kid))
    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    game = tmp_path / "selector.exe"
    game.write_bytes(selector_le())
    compiler = next(c for c in kits.compilers(("watcom",)) if f"-{language}-" in c.version)
    slug = net.import_rom(game, confirmed=True, toolchain=f"Watcom/{compiler.version}", log=lambda *_: None)
    root = Path(net.project_row(slug)["root"])
    fn, agent = "_func_00010000", net.local_agent("segments-test")[0]
    net.claim(agent, slug, fn)
    assert "DOS symbolic fixups" in net.get_function(fn, slug)
    source = ('extern char D_00020000;\nunsigned short loadseg(void);\n'
              '#pragma aux loadseg = 0x66 0xb8 __seg D_00020000 value [ax];\n'
              'unsigned short func_00010000(void) { return loadseg(); }')
    bad = net.attempt(agent, fn, source.replace("D_00020000", "D_00010000"), slug)
    assert not bad["matched"]
    literal = source.replace("__seg D_00020000", "0x00 0x00")
    assert not net.attempt(agent, fn, literal, slug)["matched"]
    assert net.attempt(agent, fn, source, slug)["matched"]
    assert scratch.check_code(root, "selector", log=lambda *_: None)["identical"]
    assert (root / "build/selector/selector.exe").read_bytes() == game.read_bytes()
    # Simulate the prior automatic set-aside and old target metadata, then run the app's actual update path.
    config = root / "config/selector/functions.json"
    info = json.loads(config.read_text())
    entry = next(f for fs in info["units"].values() for f in fs)
    entry.pop("segments")
    entry["generated"] = scratch.LEGACY_SEGMENTS
    config.write_text(json.dumps(info))
    project = net.project_row(slug)
    net.store.conn.execute("UPDATE functions SET status='asm', meta=? WHERE project_id=? AND name=?",
                           (json.dumps({"symbol": fn, "asm_code": scratch.LEGACY_SEGMENTS}), project["id"], fn))
    net.store.conn.commit()
    manifest = json.loads((root / ".decomp.wtf.json").read_text())
    manifest["tools"] = "old"
    (root / ".decomp.wtf.json").write_text(json.dumps(manifest))
    from decompnet.consoles.pc import WindowsPC
    WindowsPC().on_start(net, slug, log=lambda *_: None)
    migrated = json.loads(config.read_text())
    assert all("generated" not in f and f.get("segments") for fs in migrated["units"].values() for f in fs)
    row = net.store.one("SELECT status,meta FROM functions WHERE project_id=? AND name=?", project["id"], fn)
    assert row["status"] == "matched" and "asm_code" not in json.loads(row["meta"])
    assert scratch.check_code(root, "selector", log=lambda *_: None)["identical"]
