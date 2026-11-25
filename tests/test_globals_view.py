"""The Globals tab: data symbols listed beside the address name they started as."""
import json
from pathlib import Path

from decompnet.app.globals_view import globals_list
from decompnet.store import Store

STATIC = Path(__file__).parents[1] / "src/decompnet/app/static"


def _net(tmp_path, fork=False):
    store = Store(tmp_path / "catalog.db")
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,root,config) VALUES(1,'g','g',?,?)",
                   (str(tmp_path), json.dumps({"kind": "rom", "platform": "n64"})))
        db.execute("INSERT INTO functions(id,project_id,name,target,status,unit) VALUES(1,1,'main',x'00',?,'')", ("open",))
        for name, addr, size, kind, preview in [("sDirectKey", 0x800A4DFC, 4, "data", "0x00000001"),
                                                ("D_800A4E00", 0x800A4E00, 8, "bss", ""),
                                                ("main", 0x80001000, 4, "func", None)]:
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind,preview) VALUES(1,?,?,?,?,?)",
                       (name, addr, size, kind, preview))

    class Net:
        def fork_slug(self, _):
            return "g" if fork else None
        def _project(self, slug):
            return store.one("SELECT * FROM projects WHERE slug=?", slug)
        def project_store(self, _):
            return store
    return Net(), store


def test_lists_data_symbols_with_their_address_names(tmp_path):
    net, store = _net(tmp_path)
    data = globals_list(net, "g")
    rows = [dict(zip(data["keys"], r)) for r in data["rows"]]
    assert [r["name"] for r in rows] == ["sDirectKey", "D_800A4E00"]  # functions are the Functions tab's
    assert rows[0]["original"] == "D_800A4DFC" and rows[0]["preview"] == "0x00000001"
    assert data["named"] == 1
    store.close_readers(); store.conn.close()


def test_a_fork_says_why_it_has_no_list(tmp_path):
    net, store = _net(tmp_path, fork=True)
    data = globals_list(net, "g")
    assert data["supported"] is False and data["rows"] == [] and data["reason"]
    store.close_readers(); store.conn.close()


def test_the_tab_is_wired_into_the_page():
    html = (STATIC / "index.html").read_text()
    assert 'data-ptab="globals"' in html and 'data-pane="globals"' in html and "/assets/js/globals.js" in html
    assert '"globals"' in (STATIC / "js/base.js").read_text()


def test_a_type_is_recorded_listed_and_cleared(tmp_path):
    import pytest

    from decompnet.app.globals_view import set_type
    from decompnet.network import NetworkError
    net, store = _net(tmp_path)
    assert "✅" in set_type(net, "g", "D_800A4DFC", "s32 ", "agent")  # by its address name, though renamed
    row = [dict(zip(globals_list(net, "g")["keys"], r)) for r in globals_list(net, "g")["rows"]][0]
    assert (row["name"], row["type"], row["type_source"]) == ("sDirectKey", "s32", "agent")
    for bad in ("s32; rm", "struct {int a;}", "x" * 90):
        with pytest.raises(NetworkError):
            set_type(net, "g", "sDirectKey", bad)
    with pytest.raises(NetworkError):
        set_type(net, "g", "main", "s32")  # a function is not a global
    set_type(net, "g", "0x800A4E00", "u8[8]", "emulator")
    set_type(net, "g", "sDirectKey", "")
    rows = {r[0]: r for r in globals_list(net, "g")["rows"]}
    assert rows["sDirectKey"][6] == "" and rows["D_800A4E00"][6] == "u8[8]"
    store.close_readers(); store.conn.close()


def test_an_emulator_run_types_a_variable_its_stores_agree_on(tmp_path):
    from types import SimpleNamespace

    from decompnet.app import emulator_watch
    net, store = _net(tmp_path)
    proj = net._project("g")
    proj = dict(proj) | {"big_endian": 1}
    sym = SimpleNamespace(size=4)
    net.symbols = lambda _: SimpleNamespace(by_addr={0x800A4DFC: sym})
    swc1 = (0x39 << 26).to_bytes(4, "big")
    net.reader = lambda _: (lambda addr, n: swc1)
    run = SimpleNamespace(hits=[SimpleNamespace(kind="watch", pc=0x80001000)] * 2)
    assert emulator_watch.infer_type(net, proj, run, 0x800A4DFC) == "f32"
    text = emulator_watch.record_type(net, "g", proj, run, 0x800A4DFC)
    assert "f32" in text
    assert store.one("SELECT type_source FROM symbols WHERE name='sDirectKey'")["type_source"] == "emulator"
    from decompnet.app.globals_view import set_type
    set_type(net, "g", "sDirectKey", "Vec3f", "agent")
    assert emulator_watch.record_type(net, "g", proj, run, 0x800A4DFC) == ""  # an agent's type stays
    sym.size = 8  # a store of another width than the symbol says nothing
    assert emulator_watch.infer_type(net, proj, run, 0x800A4DFC) is None
    store.close_readers(); store.conn.close()


def test_a_global_lists_the_functions_that_reference_it(tmp_path):
    from decompnet.app.globals_view import global_users

    net, store = _net(tmp_path)
    with store.tx() as db:
        db.execute("UPDATE functions SET relocs=?, vram=? WHERE id=1", (json.dumps([[0, 5, "D_800A4DFC", 0], [8, 6, "D_800A4DFC", 0]]), 0x80001000))
        db.execute("INSERT INTO functions(id,project_id,name,target,status,unit,relocs) VALUES(2,1,'other',x'00','open','',?)",
                   (json.dumps([[0, 5, "D_800A4E00", 0]]),))
    data = global_users(net, "g", "sDirectKey")
    assert [(u["name"], u["references"]) for u in data["users"]] == [("main", 2)]  # reached by its D_ name, counted twice
    assert global_users(net, "g", "D_800A4E00")["users"][0]["name"] == "other"
    store.close_readers(); store.conn.close()


def test_the_global_drawer_is_wired():
    js = (STATIC / "js/globals.js").read_text()
    assert "/api/global-users" in js and "openGlobal" in js
