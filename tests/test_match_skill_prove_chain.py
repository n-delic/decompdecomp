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
