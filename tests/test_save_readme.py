"""Saving progress refreshes generated sections while keeping people's project guide and notes."""

import xml.etree.ElementTree as ET
from collections import Counter

import pytest

from decompnet.app import save_readme, saves
from decompnet.app.engine import RomNetwork


@pytest.fixture
def project(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    root = tmp_path / "workspace" / "project"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text('{"units": []}')
    net.forks._register("p", "Project", "fork", root, {"kind": "fork", "platform": "nds", "ready": True}, "x")
    with net.store.tx() as db:
        for name, status, meta in [("matched", "matched", "{}"), ("open<&", "open", "{}"),
                                   ("assembly", "asm", "{}"), ("library", "matched", '{"set_aside":"library"}'),
                                   ("gone", "open", '{"gone":true}')]:
            db.execute("INSERT INTO functions(project_id,name,status,size,n_insns,meta) VALUES(1,?,?,4,1,?)",
                       (name, status, meta))
    return net, root / "progress"


def test_a_save_creates_a_guide_and_refreshes_only_its_tagged_sections(project):
    net, folder = project
    saves.save(net, "p")
    path = folder / "README.md"
    first = path.read_text(encoding="utf-8")
    assert first.startswith("# Project") and "closest C" in first
    assert "1 / 2 functions (50.0%)" in first and "2 set aside" in first
    before, after = "# My project\n\n[Chat](https://example.com)\n\n", "\n## Notes\nKeep my research: café.\n"
    path.write_text(before + first + after, encoding="utf-8")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='open<&'")
    saves.save(net, "p")
    updated = path.read_text(encoding="utf-8")
    assert updated.startswith(before) and updated.endswith(after)
    assert "2 / 2 functions (100.0%)" in updated and "50.0%" not in updated
    assert updated.count("decompdecomp:progress:start") == 1
    assert updated.count("decompdecomp:visuals:start") == 1
    saves.save(net, "p")
    assert path.read_text(encoding="utf-8") == updated


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("counter", ["- **Progress:** 2,920 matching functions; 3,760 best attempts",
                                      "- **Progress:** 1 matching function(s), 2 best attempt(s)",
                                      "- **Progress:** 1 matching function, 2 best attempts — my notes"])
def test_an_existing_guide_is_preserved_when_adopting_its_progress_counter(project, newline, counter):
    net, folder = project
    folder.mkdir()
    prefix = "# My guide\n\n[Discord](https://example.com)\nCompiler notes: café.\n"
    suffix = "\n\n## My notes\nKeep all of this.\n"
    path = folder / "README.md"
    path.write_bytes((prefix + counter + suffix).replace("\n", newline).encode("utf-8"))
    saves.save(net, "p")
    updated = path.read_bytes().decode("utf-8")
    assert updated.startswith(prefix.replace("\n", newline))
    assert suffix.replace("\n", newline) in updated
    if "my notes" in counter:
        assert counter in updated
    else:
        assert counter not in updated
    saves.save(net, "p")
    assert path.read_bytes().decode("utf-8") == updated
    if newline == "\r\n":
        assert "\r\r\n" not in updated
        assert "\n" not in updated.replace("\r\n", "")


def test_the_map_shows_actual_function_states_and_escapes_names(project):
    net, folder = project
    saves.save(net, "p")
    image = (folder / save_readme.CARD).read_text(encoding="utf-8")
    svg = ET.fromstring(image)
    assert "1 / 2 functions matched (50.0%)" in image
    assert "open&lt;&amp;" in image and "gone" not in image
    assert "nds" in image
    tiles = svg.findall(".//{*}g/{*}g")
    assert len(tiles) == 4
    assert "set-aside" in image


def test_an_empty_project_and_a_large_map_have_valid_bounded_images(project):
    net, folder = project
    with net.store.tx() as db:
        db.execute("DELETE FROM functions")
    saves.save(net, "p")
    image = (folder / save_readme.CARD).read_text(encoding="utf-8")
    assert "0 / 0 functions matched (0.0%)" in image
    ET.fromstring(image)
    states = [("name", "matched"), ("other", "open")] * save_readme.MAX_TILES
    image = save_readme._card(states, Counter(state for _, state in states), len(states), 50)
    svg = ET.fromstring(image)
    assert len(svg.findall(".//{*}g/{*}g")) == save_readme.MAX_TILES
    assert "2 function(s) per tile" in image


def test_incomplete_tags_do_not_swallow_project_notes(project):
    net, folder = project
    folder.mkdir()
    original = "# My guide\n<!-- decompdecomp:progress:start -->\nMy notes must survive.\n"
    path = folder / "README.md"
    path.write_text(original, encoding="utf-8")
    saves.save(net, "p")
    assert path.read_text(encoding="utf-8").startswith(original)


def test_a_rom_save_updates_tagged_compiler_details_and_preserves_notes(project, monkeypatch):
    net, folder = project
    cfg = {"platform": "n64", "compiler": {"toolchain": "original-compiler"}}
    monkeypatch.setattr(net, "is_rom", lambda proj: True)
    monkeypatch.setattr(net, "cfg", lambda proj: cfg)
    monkeypatch.setattr(net, "_unit_contexts", lambda pid: set())
    monkeypatch.setattr(net, "_context", lambda pid, unit: {"header": "/* Shared declarations */"})
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='void matched(void) {}' WHERE name='matched'")
    saves.save(net, "p")
    path = folder / "README.md"
    first = path.read_text(encoding="utf-8")
    assert "**Compiler:** original-compiler" in first
    assert "**Progress:** 1 matching function(s), 0 best attempt(s)" in first
    assert "src/<function>.c" in first and "decompdecomp.json" in first
    path.write_text(first + "\n## My research\nKeep this.\n", encoding="utf-8")
    cfg["compiler"]["toolchain"] = "another-compiler"
    saves.save(net, "p")
    updated = path.read_text(encoding="utf-8")
    assert "**Compiler:** another-compiler" in updated and "original-compiler" not in updated
    assert updated.endswith("\n## My research\nKeep this.\n")
