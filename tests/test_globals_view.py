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
    with store.tx() as db:  # relocations are [offset, type, absolute target address]
        db.execute("UPDATE functions SET relocs=?, vram=? WHERE id=1", (json.dumps([[0, 5, 0x800A4DFC], [8, 6, 0x800A4DFC]]), 0x80001000))
        db.execute("INSERT INTO functions(id,project_id,name,target,status,unit,relocs) VALUES(2,1,'other',x'00','open','',?)",
                   (json.dumps([[0, 5, 0x800A4E00]]),))
    data = global_users(net, "g", "sDirectKey")
    assert [(u["name"], u["references"], u["inside"]) for u in data["users"]] == [("main", 2, 0)]
    assert global_users(net, "g", "D_800A4E00")["users"][0]["name"] == "other"
    store.close_readers(); store.conn.close()


def test_the_global_drawer_is_wired():
    js = (STATIC / "js/globals.js").read_text()
    assert "/api/global-users" in js and "openGlobal" in js


def test_a_use_inside_a_table_or_struct_counts(tmp_path):
    from decompnet.app.globals_view import global_users

    net, store = _net(tmp_path)
    with store.tx() as db:
        db.execute("UPDATE symbols SET size=0x40 WHERE name='sDirectKey'")  # a table of 16 words
        db.execute("INSERT INTO functions(id,project_id,name,target,status,unit,relocs) VALUES(3,1,'indexes',x'00','open','',?)",
                   (json.dumps([[0, 5, 0x800A4DFC], [4, 5, 0x800A4E08], [8, 6, 0x800A4E3C]]),))
    users = {u["name"]: u for u in global_users(net, "g", "sDirectKey")["users"]}
    assert users["indexes"]["references"] == 2 and users["indexes"]["inside"] == 1  # 0x...E3C is the table's end

    with store.tx() as db:  # no recorded size: only its exact address
        db.execute("UPDATE symbols SET size=0 WHERE name='sDirectKey'")
    users = {u["name"]: u for u in global_users(net, "g", "sDirectKey")["users"]}
    assert users["indexes"]["references"] == 1 and users["indexes"]["inside"] == 0  # members past the first byte need a recorded size
    store.close_readers(); store.conn.close()


def test_declarations_give_types_and_sizes():
    from decompnet.app.global_types import declarations

    found = declarations("""
extern s32 gScore;            // a word
extern u8 gBuf[0x10];
extern struct Actor *gActor;
f32 gTable[4][2] = { 0 };
extern u16 gMacro[COUNT];
s32 func(void);
void f(void) {
    s32 local;
    return x;
    extern s16 gInside;
}
""")
    assert found["gScore"] == ("s32", 4) and found["gBuf"] == ("u8[0x10]", 16)
    assert found["gActor"] == ("struct Actor *", 4) and found["gTable"] == ("f32[4][2]", 32)
    assert found["gMacro"] == ("u16[COUNT]", None) and found["gInside"] == ("s16", 2)
    assert "local" not in found and "x" not in found and "func" not in found


def test_a_match_or_header_types_the_global_but_never_over_a_users_type(tmp_path):
    from decompnet.app.global_types import record

    net, store = _net(tmp_path)
    with store.tx() as db:
        db.execute("UPDATE symbols SET size=0 WHERE name='sDirectKey'")  # splat recorded none
    assert record(store, 1, {"sDirectKey": ("u8[16]", 16), "gNotASymbol": ("s32", 4), "main": ("s32", 4)}) == 1
    row = store.one("SELECT c_type, type_source, size FROM symbols WHERE name='sDirectKey'")
    assert (row["c_type"], row["type_source"], row["size"]) == ("u8[16]", "match", 16)
    assert store.one("SELECT c_type FROM symbols WHERE name='main'")["c_type"] is None  # a function, not data
    assert record(store, 1, {"sDirectKey": ("u8[16]", 16)}) == 0  # nothing new
    with store.tx() as db:
        db.execute("UPDATE symbols SET c_type='f32', type_source='user' WHERE name='sDirectKey'")
    record(store, 1, {"sDirectKey": ("s32", 4)})
    row = store.one("SELECT c_type, type_source, size FROM symbols WHERE name='sDirectKey'")
    assert (row["c_type"], row["type_source"], row["size"]) == ("f32", "user", 16)  # a size once known stays
    store.close_readers(); store.conn.close()


def test_declarations_are_rewritten_with_the_new_type():
    from decompnet.app.global_types import declaration_text, rewrite

    assert declaration_text("u8[16]", "gBuf") == "u8 gBuf[16]"
    assert declaration_text("struct Actor *", "gActor") == "struct Actor *gActor"
    text = "extern s32 gScore;\nextern s32 gOther;\nvoid f(void) {\n    gScore = 1;\n    s32 gScore2;\n}\n"
    assert rewrite(text, "gScore", "f32") == ("extern f32 gScore;\nextern s32 gOther;\nvoid f(void) {\n    gScore = 1;\n    s32 gScore2;\n}\n", 1)
    assert rewrite("static const s16 gTab[4] = {1,2,3,4};\n", "gTab", "u8[8]") == ("static u8 gTab[8] = {1,2,3,4};\n", 1)  # `const` is part of the type
    assert rewrite("extern s32 gScore;", "gMissing", "f32") == ("extern s32 gScore;", 0)


def _changing_net(tmp_path, broken=()):
    import time

    net, store = _net(tmp_path)
    with store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) VALUES(1,1,?,0,'',?,'')",
                   ("extern s32 sDirectKey;\n", time.time()))
        db.execute("UPDATE functions SET status='matched', matched_source=?, best_source=?, relocs=? WHERE id=1",
                   ("void main(void) { sDirectKey = 1; }", "void main(void) { sDirectKey = 1; }", json.dumps([[0, 5, 0x800A4DFC]])))
    ran = []

    def regression_test(proj, header, sources=None, only=None, header_for=None, progress=None):
        ran.append(([r["name"] for r in only], header_for(only[0]), sources))
        return [("main", "compiles but no longer matches")] if broken and header_for(only[0]).count("f32") else []

    from types import SimpleNamespace
    net.is_rom = lambda proj: True
    net.local_agent = lambda who: (SimpleNamespace(id=0), True)
    net._unit_contexts = lambda pid: set()
    net._context = lambda pid, unit: store.one("SELECT * FROM context_versions WHERE project_id=? AND unit=? ORDER BY version DESC LIMIT 1", pid, unit)
    net._effective_unit = lambda fn, units: ""
    net.regression_test = regression_test
    net._latest_version = lambda db, pid, unit: db.execute("SELECT MAX(version) AS v FROM context_versions WHERE project_id=?", (pid,)).fetchone()["v"]
    net._next_version = lambda db, pid: net._latest_version(db, pid, "") + 1
    net._event = lambda *a: None
    net.progress = SimpleNamespace(sync=lambda *a, **k: None)
    return net, store, ran


def test_changing_a_type_rechecks_its_users_and_saves(tmp_path):
    from decompnet.app.global_types import change_type

    net, store, ran = _changing_net(tmp_path)
    done = change_type(net, "g", "sDirectKey", "f32", SimpleLog())
    assert ran[0][0] == ["main"] and "f32" in ran[0][1] and done["changed_headers"] == 1 and done["changed_sources"] == 0
    assert store.one("SELECT header FROM context_versions WHERE version=2")["header"] == "extern f32 sDirectKey;\n"
    row = store.one("SELECT c_type, type_source, size FROM symbols WHERE name='sDirectKey'")
    assert (row["c_type"], row["type_source"], row["size"]) == ("f32", "user", 4)
    store.close_readers(); store.conn.close()


def test_a_type_that_breaks_a_match_is_refused_and_nothing_is_saved(tmp_path):
    import pytest

    from decompnet.app.global_types import change_type
    from decompnet.network import NetworkError

    net, store, ran = _changing_net(tmp_path, broken=True)
    # (the stub fails the "before" check for nobody, so main is blamed)
    net_before = net.regression_test
    net.regression_test = lambda *a, **k: net_before(*a, **k) if k.get("header_for") and "f32" in k["header_for"](k["only"][0]) else []
    with pytest.raises(NetworkError, match="breaks 1 matched function"):
        change_type(net, "g", "sDirectKey", "f32", SimpleLog())
    assert store.one("SELECT COUNT(*) AS n FROM context_versions")["n"] == 1
    assert store.one("SELECT c_type FROM symbols WHERE name='sDirectKey'")["c_type"] is None
    with pytest.raises(NetworkError, match="not a C type"):
        change_type(net, "g", "sDirectKey", "s32; evil", SimpleLog())
    store.close_readers(); store.conn.close()


class SimpleLog:
    def progress(self, _):
        pass
