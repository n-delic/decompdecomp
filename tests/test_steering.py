"""Variants that add dead-looking code are tagged (original or steering only) and never rejected."""
from decompnet import steering
from decompnet.app import readable
from decompnet.app.candidate_changes import describe
from tests.test_agent_workflow_efficiency import game

PARENT = "int f(int *p)\n{\n    int a;\n    a = p[1];\n    return a + 1;\n}\n"
UNUSED_READ = "int f(int *p)\n{\n    int a;\n    int t = p[0];\n    a = p[1];\n    return a + 1;\n}\n"
UNUSED_COPY = "int f(int *p)\n{\n    int a;\n    int keep = 0;\n    a = p[1];\n    return a + 1;\n}\n"


def rows(target, yours):
    out = []
    for i in range(max(len(target), len(yours))):
        t, y = (target[i] if i < len(target) else ""), (yours[i] if i < len(yours) else "")
        out.append((" " if t == y else "i", t, y))
    return out


BODY = ["lw v0, 4(a0)", "jr ra", "addiu v0, v0, 1"]


def test_dead_looking_finds_unread_locals_noops_and_identical_arms():
    assert steering.dead_looking(PARENT, UNUSED_READ) == ["int t = p[0]"]
    assert steering.dead_looking(PARENT, UNUSED_COPY) == ["int keep = 0"]
    assert steering.dead_looking(PARENT, PARENT.replace("int a;", "int a;\n    (void)p;")) == ["(void)p"]
    assert steering.dead_looking(PARENT, PARENT.replace("int a;", "int a;\n    do { } while (0);")) == ["do { } while (0)"]
    assert steering.dead_looking(PARENT, PARENT.replace("return", "if (a) a = 2; else a = 2;\n    return"))


def test_dead_looking_leaves_ordinary_changes_alone():
    used = PARENT.replace("return a + 1;", "int t = a;\n    return t + 1;")
    assert steering.dead_looking(PARENT, used) == []
    assert steering.dead_looking(PARENT, PARENT.replace("int a;", "unsigned a;")) == []
    assert steering.dead_looking(PARENT, PARENT.replace("a + 1", "a + 2")) == []
    assert steering.dead_looking(PARENT, PARENT) == []
    assert steering.dead_looking(PARENT, PARENT.replace("a = p[1];", "a = p[1];\n    g(a);")) == []


def test_an_added_instruction_the_target_has_is_plausibly_original():
    target = ["lw v1, 0(a0)", *BODY]
    parent_rows = rows(target, BODY)
    child_rows = rows(target, ["lw v0, 0(a0)", *BODY])
    found = steering.classify(parent_rows, child_rows, PARENT, UNUSED_READ)
    assert found["tag"] == steering.ORIGINAL and found["added"] == ["int t = p[0]"]
    assert found["insns"] == ["lw v1, 0(a0)"]  # the target's own instruction
    assert steering.LABELS[steering.ORIGINAL] in steering.describe(found)


def test_added_code_that_emits_nothing_is_steering_only():
    parent_rows = rows(BODY, ["lw v1, 4(a0)", "jr ra", "addiu v0, v1, 1"])
    child_rows = rows(BODY, BODY)
    found = steering.classify(parent_rows, child_rows, PARENT, UNUSED_COPY)
    assert found["tag"] == steering.STEERING and found["insns"] == []
    assert "emits no instruction" in steering.describe(found)


def test_an_added_instruction_the_target_lacks_is_steering_only():
    child_rows = rows(BODY, ["move v1, zero", *BODY])
    found = steering.classify(rows(BODY, BODY), child_rows, PARENT, UNUSED_COPY)
    assert found["tag"] == steering.STEERING and found["insns"] == ["move v1, zero"]


def test_a_variant_without_dead_looking_code_is_not_classified():
    assert steering.classify(rows(BODY, BODY), rows(BODY, BODY), PARENT, PARENT.replace("a + 1", "a + 2")) is None
    assert steering.classify([], rows(BODY, BODY), PARENT, UNUSED_COPY) is None


def record(net, fn, agent, source, score, shown):
    net._record(fn, agent, score, source, 1, mismatch_rows=rows(BODY, shown), matched=score == 0)
    return net._function("f", "game")


def test_tags_are_recorded_on_the_attempt_and_nothing_is_rejected(tmp_path):
    net, agent, fn = game(tmp_path)
    net.claim_row(agent, "game", "f")
    fn = record(net, fn, agent, PARENT, 9, ["lw v1, 4(a0)", "jr ra", "addiu v0, v1, 1"])
    fn = record(net, fn, agent, UNUSED_COPY, 5, BODY[:2] + ["addiu v0, v0, 1"])
    assert fn["best_source"] == UNUSED_COPY and fn["best_score"] == 5  # accepted, not blocked
    row = net.store.one("SELECT steering FROM attempts WHERE function_id=? AND score=5", fn["id"])
    assert steering.decode(row["steering"])["tag"] == steering.STEERING
    assert net.store.one("SELECT steering FROM attempts WHERE function_id=? AND score=9", fn["id"])["steering"] is None
    assert "steering only" in describe(net, agent, fn, UNUSED_COPY)
    assert "steering only" in " ".join(steering.lines_for(net, fn, UNUSED_COPY))
    assert "try removing these first" in readable.steering_checklist(net, fn, UNUSED_COPY)
    assert readable.steering_checklist(net, fn, PARENT) == ""  # once it's gone, nothing to say


def test_a_byte_identical_match_with_dead_looking_code_is_always_accepted(tmp_path, monkeypatch):
    net, agent, fn = game(tmp_path)
    monkeypatch.setattr(net, "on_match", lambda *args, **kwargs: None)
    net.claim_row(agent, "game", "f")
    fn = record(net, fn, agent, PARENT, 9, ["lw v1, 4(a0)", "jr ra", "addiu v0, v1, 1"])
    fn = record(net, fn, agent, UNUSED_READ, 0, BODY)
    assert fn["status"] == "matched" and fn["matched_source"] == UNUSED_READ
    tagged = net.store.one("SELECT steering FROM attempts WHERE function_id=? AND score=0", fn["id"])
    assert steering.decode(tagged["steering"]) is not None


def test_a_variant_whose_parent_cannot_be_judged_goes_untagged_not_failed(tmp_path):
    net, agent, fn = game(tmp_path)
    net.claim_row(agent, "game", "f")
    fn = record(net, fn, agent, PARENT, 9, BODY)
    net._parent_rows.clear()
    net.parent_rows = lambda *args: (_ for _ in ()).throw(RuntimeError("no compiler"))
    fn = record(net, fn, agent, UNUSED_COPY, 5, BODY)
    assert fn["best_score"] == 5
    assert net.store.one("SELECT steering FROM attempts WHERE function_id=? AND score=5", fn["id"])["steering"] is None


def test_declarations_preprocessor_lines_and_control_flow_are_not_statements():
    """Measured on a game's matched sources: externs, `#define`s, `} else {`, `} while (c)` and compound assignments
    were all read as dead at first."""
    parent = "int f(int *p)\n{\n    return *p;\n}\n"
    child = ("#ifndef M2C_DEFINES\n#define M2C_DEFINES\n#endif\nextern int g;\ntypedef struct {\n    int v;\n} Node;\n"
             "int f(int *p)\n{\n    do {\n        g += 1;\n        if (*p) {\n            g -= 2;\n        } else {\n"
             "            g |= 4;\n        }\n    } while (g != 0);\nL1:\n    return *p;\n}\n")
    assert steering.dead_looking(parent, child) == []


def test_a_one_line_function_body_is_read_too():
    assert steering.dead_looking("int f(int a) { return a; }", "int f(int a) { int t = 0; return a; }") == ["int t = 0"]


def test_a_local_read_only_by_a_condition_is_not_dead():
    """Measured: reads inside `if (n != 0)` and `switch (n)` headers were missed, so live counters looked unused."""
    parent = "int f(int *p)\n{\n    return *p;\n}\n"
    child = ("int f(int *p)\n{\n    int n;\n    int m;\n    n = *p;\n    m = 1;\n    if (n != 0) {\n        return 1;\n    }\n"
             "    switch (m) {\n    case 1:\n        return 2;\n    }\n    return *p;\n}\n")
    assert steering.dead_looking(parent, child) == []
    assert steering.dead_looking(parent, child.replace("if (n != 0)", "if (*p != 0)")) == ["int n", "n = *p"]


def test_a_returned_local_and_a_declaration_list_are_not_dead():
    parent = "int f(int *p)\n{\n    return *p;\n}\n"
    child = "int f(int *p)\n{\n    int r;\n    int a, b;\n    r = *p;\n    return r;\n}\n"
    assert steering.dead_looking(parent, child) == []


def test_a_pad_local_that_only_sizes_the_frame_says_so():
    pad = PARENT.replace("int a;", "int a;\n    volatile int pad[2];")
    target = ["addiu sp, sp, -8", *BODY, "addiu sp, sp, 8"]
    found = steering.classify(rows(target, BODY), rows(target, ["addiu sp, sp, -8", *BODY, "addiu sp, sp, 8"]), PARENT, pad)
    assert found["tag"] == steering.ORIGINAL and found["frame"]
    assert "sizes the stack frame" in steering.describe(found)
