"""prove_chain.py (the match-function skill): the greedy chain of repair-family children, scored by the judge."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"skill_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prove_chain = load("prove_chain")

# A toy search space: the source is a string of letters, the score is how many differ from "abc".
TARGET = "abc"


def score(text):
    return None if text == "BROKEN" else sum(a != b for a, b in zip(text.ljust(3, "?"), TARGET))


def generate(text):
    out = []
    for i, want in enumerate(TARGET):
        if text[i] != want:
            out.append((f"fix_{i}", text[:i] + want + text[i + 1:]))
    out.append(("breaks", "BROKEN"))
    out.append(("noop", text))
    return out


def test_the_chain_applies_the_best_child_each_step_until_it_reaches_zero():
    lines = []
    final, best, chain = prove_chain.prove("xyz", generate, score, None, log=lines.append)
    assert (final, best) == ("abc", 0)
    assert [family for family, _ in chain] == ["fix_0", "fix_1", "fix_2"] and [s for _, s in chain] == [2, 1, 0]
    assert lines[0] == "start  score 3" and lines[-1] == "matched: score 0"


def test_only_the_named_families_are_applied_and_the_chain_stops_when_nothing_improves():
    lines = []
    final, best, chain = prove_chain.prove("xyz", generate, score, {"fix_2"}, log=lines.append)
    assert (final, best) == ("xyc", 2)
    assert [family for family, _ in chain] == ["fix_2"]
    assert any("stopped" in line for line in lines) and lines[-1] == "final score 2"


def test_the_steps_and_children_bounds_hold():
    calls = []
    prove_chain.prove("xyz", generate, lambda t: calls.append(t) or score(t), None, steps=1, children=1, log=lambda _: None)
    assert len(calls) == 2  # the start, and one child at the cap
    with pytest.raises(SystemExit, match="doesn't compile"):
        prove_chain.prove("BROKEN", generate, score, None, log=lambda _: None)


def test_the_generator_labels_children_with_the_apps_family_names(monkeypatch):
    from types import SimpleNamespace

    source = """\
void f(int *p, int a, int b)
{
  int x;
  int y;
  x = a + b;
  y = b * 2;
  if (a) { p[0] = x; } else { p[0] = x; }
  p[1] = y;
}
"""
    judge = SimpleNamespace(arch="mips", asm="", compiler={"flags": ["-O2"]}, header="")
    children = prove_chain.generator(judge, "f")(source)
    assert {"allocation_lever", "identical_arm_collapse"} <= {family for family, _ in children}
    # scoring by length makes the arm collapse the chain's first step
    final, best, chain = prove_chain.prove(source, prove_chain.generator(judge, "f"), len, {"identical_arm_collapse"},
                                           steps=1, log=lambda _: None)
    assert chain and chain[0][0] == "identical_arm_collapse" and best < len(source)


def test_a_source_saved_before_a_rename_is_proposed_under_the_current_name():
    from types import SimpleNamespace

    source = "void func_80001000(int *p, int a, int b)\n{\n  int x;\n  x = a + b;\n  if (a) { p[0] = x; } else { p[0] = x; }\n}\n"
    judge = SimpleNamespace(arch="mips", asm="", compiler={"flags": ["-O2"]}, header="",
                            request={"function": {"vram": 0x80001000}})
    children = prove_chain.generator(judge, "storePair")(source)
    assert children and all("void storePair(" in child for _, child in children)
    assert prove_chain.current_name("void f(void) {}", "f", 0x80001000) == "void f(void) {}"


def test_the_generator_passes_the_functions_jump_tables_and_context_to_the_repair_families(monkeypatch):
    """Families such as switch_gap_goto read the tables; without them they never fired from this script."""
    from types import SimpleNamespace

    from decompnet.app import draft_repairs
    seen = {}

    def variants(source, name, arch, asm, flags, labels, **kwargs):
        seen.update(kwargs)
        labels["child"] = "switch_gap_goto"
        return ["child"]
    monkeypatch.setattr(draft_repairs, "variants", variants)
    judge = SimpleNamespace(arch="mips", asm="", compiler={"flags": []}, header="h", request={},
                            tables=[(1, [0x80000010])], callees="int g(void){return 0;}", entry_scope={"language": "c"})
    assert prove_chain.generator(judge, "f")("int f(void){return 1;}") == [("switch_gap_goto", "child")]
    assert seen["tables"] == [(1, [0x80000010])] and seen["callees"] == "int g(void){return 0;}"
    assert seen["entry_scope"] == {"language": "c"} and seen["header"] == "h"


def test_children_cut_off_by_the_cap_are_reported_and_a_larger_cap_scores_them():
    lines = []
    prove_chain.prove("xyz", generate, score, None, steps=1, children=1, log=lines.append)
    assert any("cut off by --children 1" in line for line in lines)
    lines.clear()
    prove_chain.prove("xyz", generate, score, None, steps=1, children=50, log=lines.append)
    assert not any("cut off" in line for line in lines)


def test_a_stalled_step_merges_the_best_children_in_pairs_and_triples():
    # three statements; each fix alone (or two together) is worse than the start; all three together match
    want = ["a = 1;", "b = 2;", "c = 3;"]

    def joint(text):
        fixed = sum(line in text for line in want)
        return 0 if fixed == 3 else 5 if fixed else 1

    def statements(text):
        lines = text.split(" ; ")
        return [(f"fix_{i}", " ; ".join(lines[:i] + [want[i]] + lines[i + 1:])) for i in range(3) if lines[i] != want[i]]
    start = "a = 0; ; b = 0; ; c = 0;"
    final, best, chain = prove_chain.prove(start, statements, joint, None, combine=10, log=lambda _: None)
    assert (final, best) == (" ; ".join(want), 0) and chain == [("fix_0+fix_1+fix_2", 0)]
    _, best, _ = prove_chain.prove(start, statements, joint, None, combine=0, log=lambda _: None)
    assert best == 1


def test_merge_refuses_children_that_touch_the_same_tokens():
    base = "a = 1; b = 2;"
    assert prove_chain.merge(base, ["a = 3; b = 2;", "a = 1; b = 4;"]) == "a = 3; b = 4;"
    assert prove_chain.merge(base, ["a = 3; b = 2;", "a = 5; b = 2;"]) is None


# Edits that are each worse alone: the match needs all three of "A", "B" and "C" (rows 0-2), and while only some are in
# rows 3-5 are wrong too, so every single child (one fixed row, three broken) ranks below 30 harmless ones and a
# score-ranked search never merges them.
ROW_BASE = "a0 b0 c0 n0"


def toy_rows(text):
    has = ["A" in text.split(), "B" in text.split(), "C" in text.split()]
    partial = any(has) and not all(has)
    marks = [" " if h else "|" for h in has] + ["|" if partial else " "] * 3
    return [(m, f"i{k}", f"i{k}") for k, m in enumerate(marks)]


def toy_rows_score(text):
    return sum(1 for m, _, _ in toy_rows(text) if m != " ") * 100


def toy_generate(text):
    out = [("fam_c", text.replace("c0", "C")), ("fam_a", text.replace("a0", "A")), ("fam_b", text.replace("b0", "B"))]
    return out + [(f"noise{i}", text.replace("n0", f"n{i + 1}")) for i in range(30)]


def test_combine_with_rows_finds_edits_that_each_score_worse_alone():
    final, best, _ = prove_chain.prove(
        ROW_BASE, toy_generate, toy_rows_score, None, steps=1, children=40, log=lambda _: None,
        combine=5, combine_limit=60, rows=lambda t: (toy_rows_score(t), toy_rows(t)))
    assert best == 0 and final.split()[:3] == ["A", "B", "C"]
    _, without, _ = prove_chain.prove(ROW_BASE, toy_generate, toy_rows_score, None, steps=1, children=40,
                                      log=lambda _: None, combine=5, combine_limit=60)
    assert without > 0
