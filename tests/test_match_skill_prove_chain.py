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


def one_family(text):
    """The toy children as one family: a step's survey then takes only a few of them."""
    return [("fix", child) for _, child in generate(text)]


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
    prove_chain.prove("xyz", generate, lambda t: calls.append(t) or score(t), None, steps=1, max_judged=1, log=lambda _: None)
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
    assert "allocation_lever" in {family for family, _ in children}
    # the arm collapse is labelled by whichever library family proposes it first (identical_arm_collapse until
    # float_block_normal_form wrote the same text); scoring by length makes a family that collapses it the first step
    collapsed = next(family for family, child in children if "else" not in child and "do" not in child)
    final, best, chain = prove_chain.prove(source, prove_chain.generator(judge, "f"), len, {collapsed},
                                           steps=1, log=lambda _: None)
    assert chain and chain[0][0] == collapsed and best < len(source)


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


def test_children_left_out_by_the_cap_are_reported_when_a_batch_improves():
    lines = []
    prove_chain.prove("xyz", one_family, score, None, steps=1, children=1, log=lines.append)
    assert any("left out" in line and "--families" in line for line in lines)
    lines.clear()
    prove_chain.prove("xyz", generate, score, None, steps=1, children=50, log=lines.append)
    assert not any("left out" in line for line in lines)


def test_a_batch_with_no_improving_child_goes_on_to_the_children_left_out():
    # the improving child sits behind four of its own family that do not, past the survey's few
    def late(text):
        return [("noop", text + str(i)) for i in range(4)] + [("noop", "a" + text[1:])]
    lines = []
    final, best, chain = prove_chain.prove("xyz", late, lambda t: score(t[:3]), None,
                                           steps=1, children=2, log=lines.append)
    assert chain and chain[0][0] == "noop" and best == 2
    assert any("judging the other" in line for line in lines)


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


# A two-step whose first step is worse alone and whose second exists only after it (a family that fires on the first
# child's shape): no merge of one step's children finds it; the lookahead over the best non-improving child does.
def test_lookahead_takes_a_second_step_that_only_exists_after_a_worse_first():
    def generate(text):
        if text == "s0":
            return [("first", "s1"), ("noise", "n1")]
        if text == "s1":
            return [("second", "s2")]
        return []
    scores = {"s0": 10, "s1": 15, "n1": 30, "s2": 0}
    final, best, chain = prove_chain.prove("s0", generate, scores.get, None, steps=2, combine=4, log=lambda _: None)
    assert (final, best) == ("s2", 0) and chain == [("first>second", 0)]
    _, best, _ = prove_chain.prove("s0", generate, scores.get, None, steps=2, combine=4, lookahead=0, log=lambda _: None)
    assert best == 10


def test_a_combined_step_that_ties_the_score_with_fewer_differing_rows_is_taken():
    want = ["a = 1;", "b = 2;"]

    def bad_rows(text):
        return [("|", "x", "y")] * (3 - sum(w in text for w in want))

    def generate(text):
        lines = text.split(" ; ")
        return [(f"fix_{i}", " ; ".join(lines[:i] + [want[i]] + lines[i + 1:])) for i in range(2) if lines[i] != want[i]]
    start = "a = 0; ; b = 0;"
    rows = lambda t: (10, bad_rows(t))  # noqa: E731  (the total never moves, the rows do)
    final, best, chain = prove_chain.prove(start, generate, lambda t: 10, None, steps=1, combine=4, rows=rows,
                                           lookahead=0, log=lambda _: None)
    assert final == "a = 1; ; b = 2;" and best == 10 and chain == [("fix_0+fix_1", 10)]


def test_a_rank_signal_demotes_a_child_from_the_combinations_but_never_blocks_an_improvement():
    calls = []

    def signal(parent, child):
        calls.append(child)
        return (1, "makes a block-local value global") if "dead" in child else (0, "")
    generate = lambda t: [("dead_end", t + " dead"), ("good", t + " good")] if t == "s" else []  # noqa: E731
    scores = {"s": 10, "s dead": 5, "s good": 12}
    final, best, _ = prove_chain.prove("s", generate, scores.get, None, steps=1, combine=4, rank_signal=signal,
                                       log=lambda _: None)
    assert (final, best) == ("s dead", 5) and not calls  # an improving child is taken; the signal is not even asked
    lines = []
    scores["s dead"] = 11
    prove_chain.prove("s", generate, scores.get, None, steps=1, combine=1, rank_signal=signal, lookahead=0,
                      log=lines.append)
    assert any("dead_end demoted" in line for line in lines)


def test_the_run_stops_at_the_cap_on_judged_variants_and_says_how_many_were_left():
    def many(text):
        return [("noop", f"{text}{i}") for i in range(1000)]
    judged, lines = [], []
    prove_chain.prove("xyz", many, lambda t: judged.append(t) or 3, None, children=80, log=lines.append)
    assert len(judged) == 1 + prove_chain.DEFAULT_MAX_JUDGED  # the start, then the cap
    assert any("cap of 400 judged variants: 600 of 1000 children left unjudged" in line for line in lines)
    assert not any("(scored " in line for line in lines) and lines[-1] == "final score 3"
    for cap, expected in ((0, 1000), (None, 1000), (2000, 1000), (30, 30)):
        judged.clear()
        prove_chain.prove("xyz", many, lambda t: judged.append(t) or 3, None, max_judged=cap, log=lines.append)
        assert len(judged) == 1 + expected


def test_the_cap_covers_all_steps_and_keeps_the_improvement_it_already_found():
    lines = []
    # each step: an improving fix first. Batch size 1 and one family, so one variant judged per step.
    final, best, chain = prove_chain.prove("xyz", one_family, score, None, children=1, max_judged=2, log=lines.append)
    assert (final, best) == ("abz", 1) and len(chain) == 2
    assert any(line.startswith("step 3") and "cap of 2" in line and "2 of 2 children" in line for line in lines)
    lines.clear()
    final, best, _ = prove_chain.prove("xyz", generate, score, None, max_judged=1, steps=1, log=lines.append)
    assert best == 2 and any("3 of 4 children left unjudged" in line for line in lines)


def test_merges_and_lookahead_children_count_against_the_cap():
    def generate_pairs(text):
        return [("a", text + " a"), ("b", text + " b"), ("c", text + " c")] if len(text) < 6 else []
    for cap in (3, 4, 8):
        judged, lines = [], []
        prove_chain.prove("s", generate_pairs, lambda t, seen=judged: seen.append(t) or 10, None, steps=1, combine=4,
                          max_judged=cap, log=lines.append)
        assert len(judged) <= 1 + cap
        assert any(f"cap of {cap} judged variants" in line and "lookahead" in line for line in lines), lines


def test_the_generator_gives_the_families_the_assembly_under_the_sources_names(monkeypatch):
    """The app's families pair the C's calls with the target's `jal`s (#4304): a renamed callee must read the same."""
    from types import SimpleNamespace

    from decompnet.app import diff_gated_repairs, draft_repairs
    seen = []
    monkeypatch.setattr(draft_repairs, "variants", lambda source, name, arch, asm, *a, **k: seen.append(asm) or [])
    monkeypatch.setattr(diff_gated_repairs, "proposals",
                        lambda source, name, header, arch, toolchain, rows, asm, **k: seen.append(asm) or [])
    judge = SimpleNamespace(arch="mips", asm="jal func_8005BB50\n", asm_names={"func_8005BB50": "sprintf"},
                            compiler={"flags": []}, header="", request={})
    prove_chain.generator(judge, "f")("void f(char *b) { sprintf(b); }")
    assert seen == ["jal sprintf\n", "jal sprintf\n"]
    seen.clear()
    prove_chain.generator(judge, "f")("void f(char *b) { func_8005BB50(b); }")
    assert seen == ["jal func_8005BB50\n", "jal func_8005BB50\n"]


def test_a_capped_step_sees_every_family_before_it_takes_a_child():
    def many(text):
        return [("small", f"s{i}") for i in range(20)] + [("large", "big")] if text == "start" else []
    scores = {"start": 1030, "big": 330} | {f"s{i}": 1000 for i in range(20)}
    judged = []
    final, best, chain = prove_chain.prove("start", many, lambda t: judged.append(t) or scores[t], None, children=3,
                                           max_judged=10, log=lambda _: None)
    assert (final, best) == ("big", 330) and chain == [("large", 330)] and len(judged) <= 1 + 10
