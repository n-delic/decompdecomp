"""matched_neighbours: the closest matched functions with the start of their C, in the brief and the skill's orient."""
import sqlite3

from decompnet.app import matched_neighbours as mn

SOURCES = {0x100: "int caller(void) {\n    return f() + 1;\n}\n", 0x200: "int g;\nvoid before(void) { g = 1; }\n",
           0x400: "void shares(void) { a = b; }\n", 0x500: "void alike(void) {\n" + "    x();\n" * 20 + "}\n"}


class Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE functions(project_id, name, vram, unit, n_insns, status, matched_source)")
        rows = [("caller", 0x100, "a", "matched"), ("before", 0x200, "u", "matched"), ("f", 0x300, "u", "open"),
                ("shares", 0x400, "b", "matched"), ("alike", 0x500, "c", "matched"), ("after", 0x600, "u", "open")]
        for name, vram, unit, status in rows:
            self.db.execute("INSERT INTO functions VALUES (1,?,?,?,10,?,?)", (name, vram, unit, status, SOURCES.get(vram)))

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()

    def all(self, sql, *args):
        return self.db.execute(sql, args).fetchall()


class Net:
    def __init__(self):
        self.store = Store()

    def project_store(self, _):
        return self.store

    def xrefs(self, _):
        return {"calls": {}, "called_by": {0x300: {0x100}}, "uses": {0x300: {0x9000, 0x9004}},
                "used_by": {0x9000: {0x300, 0x400}, 0x9004: {0x300, 0x400}}}

    def similar(self, proj, fn, top=6):
        return [({"vram": 0x500}, 0.9), ({"vram": 0x600}, 0.95)]


def test_neighbours_are_matched_ranked_and_shown_with_the_start_of_their_c():
    net = Net()
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    items = mn.gather(net, {"id": 1}, fn)
    assert [item["name"] for item in items] == ["alike", "caller", "before", "shares"]  # 'after' is open: never shown
    assert items[0]["relations"] == ["90% similar code"] and items[1]["relations"] == ["calls it"]
    assert items[2]["relations"] == ["just before it"] and items[3]["relations"] == ["uses 2 of the same globals"]
    text = "\n".join(mn.render(items, lines=4))
    assert "### Matched neighbours" in text and "int caller(void) {" in text
    assert 'more lines: get_function(function="alike", section="matched")' in text
    brief = "\n".join(mn.brief(net, {"id": 1}, fn, shown=["alike"], limit=2))
    assert "alike" not in brief and "caller" in brief and "before" in brief


def test_a_function_with_no_matched_neighbour_gets_no_section():
    net = Net()
    net.store.db.execute("UPDATE functions SET status='open'")
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    assert mn.gather(net, {"id": 1}, fn) == [] and mn.brief(net, {"id": 1}, fn) == []
