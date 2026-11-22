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
