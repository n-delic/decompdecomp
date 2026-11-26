"""call_signatures (the skill's signatures.py): a saved best's calls against the callees' matched C, the header and
the target's call sites, printed after a claim."""
import importlib.util
from pathlib import Path

from decompnet import hosted_judge
from decompnet.app import call_signatures as cs
from tests.test_hosted import project

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"

SOURCE = """
s32 helper(s32, void *);
void tick(void);
s16 get(void);
void f(Entity *e) {
    helper(e->id, sCursor);   /* m2c's guess: $a1 still held the cursor */
    tick();
    get();
    if (e) { x = tick(); }
}
"""
ASM = """glabel f
/* 0 80001000 */ addiu $sp, $sp, -0x18
/* 4 80001004 */ lw $a0, 0x0($a0)
/* 8 80001008 */ jal helper
/* C 8000100C */ lui $a1, 0x8000
/* 10 80001010 */ jal tick
/* 14 80001014 */ nop
/* 18 80001018 */ jal get
/* 1C 8000101C */ nop
/* 20 80001020 */ sw $v0, 0x10($sp)
/* 24 80001024 */ jr $ra
/* 28 80001028 */ nop
"""
CALLEES = {"helper": "s32 helper(s32 id) {\n    return id + 1;\n}\n", "tick": "void tick(void) {\n}\n"}


def kinds(findings):
    return {(f["kind"], f["callee"]) for f in findings}


def test_a_stray_argument_the_callees_matched_c_never_takes_is_named_with_the_edit():
    found = cs.check("f", SOURCE, "", CALLEES, {"c": "void c(void) { f(1, 2); }"}, ASM)
    assert ("extra-argument", "helper") in kinds(found)
    stray = next(f for f in found if f["kind"] == "extra-argument")
    assert "drop `sCursor` from the call and the prototype" in stray["text"] and stray["line"] == 2
    assert ("prototype", "helper") in kinds(found)  # the header/source prototype disagrees with the matched C
    assert ("void-result-used", "tick") in kinds(found)
    assert ("result-unused", "get") in kinds(found)  # the target stores $v0 after `jal get`
    assert ("own-prototype", "f") in kinds(found)  # its matched caller passes two


def test_the_target_call_site_reads_argument_registers_and_the_result():
    sites = cs.call_sites(ASM)
    assert [(s["callee"], s["arg_regs"], s["result"]) for s in sites] == [
        ("helper", 2, False), ("tick", 0, False), ("get", 0, True)]  # a delay slot of the jal before never counts
    assert cs.entry_params(ASM) == 1
    missing = cs.check("g", "void h(s32, s32);\nvoid g(void) {\n    h(1);\n}\n", "", {}, {},
                       "glabel g\n/* */ li $a0, 1\n/* */ jal h\n/* */ li $a1, 2\n/* */ jr $ra\n/* */ nop\n")
    assert ("target-argument", "h") in kinds(missing) and "$a1" in missing[0]["text"]


def test_a_result_read_in_the_next_calls_delay_slot_is_read():
    """A collision walk's `start = first(0); end = last(0);`: the first result is copied in the second jal's delay
    slot and the second's in the third's. Both were reported as "overwritten unread" (call it as a statement)."""
    asm = """glabel w
/* */ addu $a0, $zero, $zero
/* */ jal first
/* */ nop
/* */ addu $a0, $zero, $zero
/* */ jal last
/* */ addu $s0, $v0, $zero
/* */ addiu $a0, $zero, 0x12D
/* */ jal find
/* */ sw $v0, 0x30($sp)
/* */ jal other
/* */ nop
/* */ jal again
/* */ nop
/* */ jr $ra
/* */ nop
"""
    assert [(s["callee"], s["result"]) for s in cs.call_sites(asm)] == [
        ("first", True), ("last", True), ("find", False), ("other", False), ("again", None)]
    source = "void w(void) {\n    s = first(0);\n    e = last(0);\n    find(0x12D);\n    other();\n}\n"
    assert not {k for k in kinds(cs.check("w", source, "", {}, {}, asm)) if k[0] == "result-unread"}


def test_return_width_from_the_header_against_the_matched_definition():
    found = cs.check("f", "void f(void) {\n    x = get();\n}\n", "s32 get(void);\nu32 put(void);",
                     {"get": "s16 get(void) {\n    return 1;\n}\n"})
    assert [f["kind"] for f in found] == ["return-type"] and "Declare `s16`" in found[0]["text"]
    assert not cs.return_differs("s32", "u32") and not cs.return_differs("f32", "float")
    assert not cs.return_differs("M2C_UNK", "s16") and not cs.return_differs("s32", "void")
    assert cs.return_differs("s32", "u8") and cs.return_differs("int", "f32")


def test_a_callee_with_an_array_pointer_or_function_pointer_parameter_still_has_its_arity():
    """A loader passed a 4th argument to a matched callee declared `(Model *m, s8 (*box)[4], Tri *tri)`: the
    parenthesised parameter hid the definition, so the stray argument (the whole 120-point difference) went unnamed."""
    callee = {"draw": "void draw(Model *m, s8 (*box)[4], Tri *tri) {\n    m->x = 1;\n}\n",
              "each": "void each(void (*cb)(s32, s32), s32 n) {\n    cb(n, n);\n}\n"}
    source = ("void draw(u8 *, s32, s32, s32);\nvoid each(void *, s32);\n"
              "void f(u8 *buf, s32 count) {\n    draw(buf, 1, 2, count);\n    each(0, 1);\n}\n")
    found = cs.check("f", source, "", callee)
    assert ("extra-argument", "draw") in kinds(found) and ("prototype", "draw") in kinds(found)
    assert "drop `count`" in next(f for f in found if f["kind"] == "extra-argument")["text"]
    assert not any(f["callee"] == "each" for f in found)  # two parameters, two arguments
    assert cs.prototypes(callee["draw"])["draw"]["params"] == 3
    assert "cb" not in cs.prototypes(callee["each"])  # the call inside the body is not a declaration


def test_a_clean_function_says_so():
    found = cs.check("f", "void tick(void);\nvoid f(void) {\n    tick();\n}\n", "", CALLEES)
    assert found == [] and "every call agrees" in cs.render(found)[0]
    assert "not defined" in cs.render(cs.check("zz", SOURCE))[0]


def test_the_worker_reads_callees_and_callers_from_the_project(tmp_path):
    net = project(tmp_path)
    store = net.project_store(1)
    with store.tx() as db:
        db.execute("UPDATE functions SET best_source=?, asm=? WHERE name='f1'",
                   ("void f2(void);\nvoid f1(void) {\n    f2(3);\n}\n", "glabel f1\n/* */ jal func_80001200\n/* */ nop\n"))
        db.execute("UPDATE functions SET status='matched', matched_source='void f2(void) {\n}\n' WHERE name='f2'")
    reply = hosted_judge.signatures_report(net, {"slug": "game", "name": "f1"})
    assert ("extra-argument", "f2") in kinds(reply["findings"]) and "drop `3`" in reply["text"]
    assert "no saved best" in hosted_judge.signatures_report(net, {"slug": "game", "name": "f3"})["error"]


def test_setup_prints_signatures_after_a_claim():
    spec = importlib.util.spec_from_file_location("setup_for_signatures", SCRIPTS / "setup.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    assert "signatures.py" in [script for script, _ in setup.ORIENT_SCRIPTS]
    assert (SCRIPTS / "signatures.py").exists()


def test_a_callee_called_by_its_old_address_name_is_read_by_address(tmp_path):
    # An effect spawner's saved best called `func_80015F0C(0)` after the project had renamed the callee to
    # `allocTextNodeObject(void)`: the source still linked, the lookup by name found no matched C and the stray `0` (its
    # `move $a0, $zero` in the jal's delay slot) went unreported; dropping it was 810 -> 610.
    net = project(tmp_path)
    store = net.project_store(1)
    vram = store.one("SELECT vram FROM functions WHERE name='f2'")["vram"]
    with store.tx() as db:
        db.execute("UPDATE functions SET best_source=? WHERE name='f1'",
                   (f"void func_{vram:08X}(s32);\nvoid f1(void) {{\n    func_{vram:08X}(0);\n}}\n",))
        db.execute("UPDATE functions SET status='matched', matched_source='void f2(void) {\n}\n' WHERE name='f2'")
    reply = hosted_judge.signatures_report(net, {"slug": "game", "name": "f1"})
    assert ("extra-argument", f"func_{vram:08X}") in kinds(reply["findings"]) and "drop `0`" in reply["text"]
