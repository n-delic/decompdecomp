"""The PS4 demo through the same import, map and refactoring calls the app uses.

Only features exercised here or in console conformance are claimed in ROADMAP.md.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.consoles.ps4 import scratch
from decompnet.forks import csource
from decompnet.limits import capped

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ps4_demo(tmp_path_factory):
    if toolchains.current_platform() != toolchains.LINUX:
        pytest.skip("PS4 builds need the Linux x86-64 app")
    if scratch.clang_path() is None or toolchains.installed("objdiff-cli") is None:
        pytest.skip("needs pinned public Clang 5.0.2 and objdiff")
    folder = ROOT / "projects" / "demo-ps4"
    subprocess.run(capped([sys.executable, str(folder / "build.py")]), check=True, timeout=120)
    built = json.loads((folder / "build" / "demo.json").read_text())
    home = tmp_path_factory.mktemp("ps4-workflows")
    net = RomNetwork(home / "app.db", home)
    slug = net.import_rom(folder / "build" / built["game"], confirmed=True, log=lambda *_: None)
    return net, slug, built["symbols"]


def test_original_addresses_reach_the_database_and_project_map(ps4_demo):
    net, slug, symbols = ps4_demo
    pid = net.project_row(slug)["id"]
    rows = net.store.all("SELECT name,vram FROM functions WHERE project_id=?", pid)
    assert {r["name"]: r["vram"] for r in rows} == symbols
    assert {r["name"]: r["vram"] for r in net.function_map(slug)} == symbols


def test_renaming_a_matched_demo_function_keeps_its_address_and_match(ps4_demo):
    net, slug, symbols = ps4_demo
    agent, _ = net.local_agent("ps4-workflow-agent")
    name = "abs_s32"
    source = csource.get((ROOT / "projects/demo-n64/original/game.c").read_text(), name)
    net.claim(agent, slug, name)
    assert net.attempt(agent, name, source, slug)["matched"]
    assert net.rename_symbol(agent, slug, name, "absolute_value").startswith("✅")
    fn = net._function("absolute_value", slug)
    assert fn["vram"] == symbols[name] and fn["status"] == "matched"
    assert net.forks.get(slug)[1].report()  # the renamed target is still readable by objdiff


def test_sync_repairs_missing_addresses_in_existing_projects(ps4_demo):
    net, slug, _ = ps4_demo
    pid = net.project_row(slug)["id"]
    before = {r["name"]: r["vram"] for r in net.function_map(slug)}
    with net.store.tx() as db:
        db.execute("UPDATE functions SET vram=NULL WHERE project_id=?", (pid,))
    net.forks.sync(slug, log=lambda *_: None)
    assert {r["name"]: r["vram"] for r in net.function_map(slug)} == before
