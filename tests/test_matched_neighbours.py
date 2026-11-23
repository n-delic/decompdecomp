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


SHAPE_ASM = ("glabel clamp\n/* 0 80000000 */ slti $at, $a0, 0x10\n/* 4 80000004 */ bnez $at, .L1\n"
             "/* 8 80000008 */ nop\n/* C 8000000C */ addiu $a0, $zero, 0x10\n.L1:\n/* 10 80000010 */ sw $a0, 0($a1)\n"
             "/* 14 80000014 */ addiu $a1, $a1, 4\n/* 18 80000018 */ jr $ra\n/* 1C 8000001C */ nop\n")
SHAPE_ROWS = [(" ", "lw $a0, 0($a2)", "lw $a0, 0($a2)"), (" ", "slti $at, $a0, 0x10", "slti $at, $a0, 0x10"),
              (" ", "bnez $at, .L1", "bnez $at, .L1"), (" ", "nop", "nop"),
              ("r", "addiu $a0, $zero, 0x10", "addiu $v0, $zero, 0x10"), ("r", "sw $a0, 0($a1)", "sw $v0, 0($a1)"),
              (" ", "addiu $a1, $a1, 4", "addiu $a1, $a1, 4"), (" ", "jr $ra", "jr $ra")]


def test_a_matched_function_with_the_mismatched_regions_shape_is_ranked_and_shown_there():
    net = Net()
    db = net.store.db
    db.execute("ALTER TABLE functions ADD COLUMN asm")
    db.execute("INSERT INTO functions VALUES (1,'clamp',2048,'z',8,'matched',?,?)",
               ("void clamp(s32 v, s32 *p) {\n    if (v >= 16) {\n        v = 16;\n    }\n    *p++ = v;\n}\n", SHAPE_ASM))
    db.execute("INSERT INTO functions VALUES (1,'other',4096,'z',8,'matched','void other(void) {}\n',"
               "'glabel other\n/* 0 */ lui $v0, 1\n/* 4 */ jr $ra\n/* 8 */ nop\n')")
    assert mn.mnemonics(SHAPE_ASM)[:3] == ["slti", "bnez", "nop"]
    assert mn.region(SHAPE_ROWS)[0] == "lw" and len(mn.region(SHAPE_ROWS)) == 8
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    alike = mn.shape_alike(net, {"id": 1}, fn, SHAPE_ROWS)
    assert [row["name"] for row, _, _ in alike] == ["clamp"]
    items = mn.gather(net, {"id": 1}, fn, limit=6, rows=SHAPE_ROWS)
    clamp = next(item for item in items if item["name"] == "clamp")
    assert clamp["relations"][0].startswith("same instruction shape as the mismatched region")
    assert "if (v >= 16) {" in "\n".join(mn.render([clamp]))
    assert all(item["name"] != "clamp" for item in mn.gather(net, {"id": 1}, fn, limit=6))  # no rows: no shape search
