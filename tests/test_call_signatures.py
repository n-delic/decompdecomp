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


def test_return_width_from_the_header_against_the_matched_definition():
    found = cs.check("f", "void f(void) {\n    x = get();\n}\n", "s32 get(void);\nu32 put(void);",
                     {"get": "s16 get(void) {\n    return 1;\n}\n"})
    assert [f["kind"] for f in found] == ["return-type"] and "Declare `s16`" in found[0]["text"]
    assert not cs.return_differs("s32", "u32") and not cs.return_differs("f32", "float")
    assert not cs.return_differs("M2C_UNK", "s16") and not cs.return_differs("s32", "void")
    assert cs.return_differs("s32", "u8") and cs.return_differs("int", "f32")


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
