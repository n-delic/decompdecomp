"""Problems a user moving a project hit: leaked file handles, a function of no bytes, a compiler asked for too late."""
import json
import os

from decompnet import store
from decompnet.app import progress, saves


def test_without_pread_the_wal_index_is_never_opened(monkeypatch, tmp_path):
    opened = store.Store(tmp_path / "d.db")
    monkeypatch.delattr(os, "pread", raising=False)  # Windows
    monkeypatch.setattr(store.os, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened a file")))
    assert opened._wal_stamp() is None


def test_an_empty_function_without_saved_code_is_left_out():
    manifest = {"format": saves.FORMAT, "game": {"sha1": "s"}, "headers": {"": "common.h"},
                "layout": [{"name": "sub_08000000", "vram": 0x08000000, "size": 4},
                           {"name": "sub_0801569A", "vram": 0x0801569A, "size": 0}]}
    files = {saves.MANIFEST: json.dumps(manifest), "include/common.h": ""}
    read = progress._manifest(files, {"rom": {"sha1": "s"}})
    assert [entry["name"] for entry in read["layout"]] == ["sub_08000000"]


def test_the_saves_compilers_are_installed_first(monkeypatch):
    wanted = []
    monkeypatch.setattr(saves.toolchains, "installed", lambda tid: None)
    monkeypatch.setattr(saves.toolchains, "ensure", lambda tid, log=print: wanted.append(tid))
    saves._install_compilers({"compiler": {"toolchain": "agbcc"},
                              "compiler_ranges": [{"toolchain": "gcc-2.7.2-kmc"}, {"toolchain": "not-one"}]},
                             lambda _: None)
    assert wanted == ["agbcc", "gcc-2.7.2-kmc"]
