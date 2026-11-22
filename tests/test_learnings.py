"""tools/learnings.py: compiler-matching evidence as one file per entry, so no two pull requests touch the same lines."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("learnings_tool", ROOT / "tools" / "learnings.py")
learnings = importlib.util.module_from_spec(spec)
sys.modules["learnings_tool"] = learnings
spec.loader.exec_module(learnings)


def test_the_entries_in_the_repository_are_all_well_formed():
    assert learnings.problems(ROOT) == []


def test_new_writes_a_skeleton_in_the_kind_folder_and_refuses_a_taken_name(tmp_path):
    path = learnings.new(tmp_path, "function", "one-shot-before-loop", compiler="KMC GCC 2.7.2 -O2", today="2026-11-22")
    assert path == tmp_path / "docs" / "learnings" / "function" / "one-shot-before-loop.md"
    meta, body = learnings.parse(path)
    assert meta == {"title": "one shot before loop", "kind": "function", "compiler": "KMC GCC 2.7.2 -O2",
                    "evidence": "observation", "date": "2026-11-22"}
    assert body.strip() and learnings.problems(tmp_path) == []
    with pytest.raises(SystemExit, match="already uses that name"):
        learnings.new(tmp_path, "finding", "one-shot-before-loop")
    with pytest.raises(SystemExit):
        learnings.new(tmp_path, "function", "Bad Name")
    with pytest.raises(SystemExit):
        learnings.new(tmp_path, "novel", "fine-name")


def test_check_names_each_problem_and_the_file_it_is_in(tmp_path):
    folder = tmp_path / "docs" / "learnings"
    (folder / "finding").mkdir(parents=True)
    (folder / "finding" / "no-front.md").write_text("just text\n")
    (folder / "finding" / "wrong-kind.md").write_text("---\ntitle: t\nkind: function\ncompiler: c\nevidence: guess\n"
                                                      "date: 22 Nov\n---\nbody\n<<<<<<< HEAD\n")
    (folder / "finding" / "Bad_Name.md").write_text("---\ntitle: t\nkind: finding\ncompiler: c\nevidence: verified\n"
                                                    "date: 2026-11-22\n---\n\n")
    text = "\n".join(learnings.problems(tmp_path))
    for expected in ("no-front.md: starts with no front matter", "wrong-kind.md: kind 'function' but the file is in finding/",
                     "evidence 'guess' is not one of", "date '22 Nov' is not YYYY-MM-DD", "conflict markers",
                     "Bad_Name.md: name must be lowercase", "no text after the front matter"):
        assert expected in text


def test_index_filters_by_every_word_and_render_joins_the_entries(tmp_path):
    learnings.new(tmp_path, "finding", "mask-operand-order", title="A mask goes first", compiler="KMC", today="2026-11-22")
    learnings.new(tmp_path, "experiment", "grid-search-failed", title="Grid of declarations", compiler="IDO", today="2026-11-22")
    assert len(learnings.index_lines(tmp_path)) == 2
    assert [line.split("  ")[0] for line in learnings.index_lines(tmp_path, ["mask"])] == ["docs/learnings/finding/mask-operand-order.md"]
    assert learnings.index_lines(tmp_path, ["mask", "ido"]) == []
    document = learnings.render(tmp_path)
    assert "## finding" in document and "### A mask goes first" in document and "## experiment" in document


LEGACY = """# Compiler matching learning log

### KMC GCC 2.7.2: a swapped pair of saved registers

Two saved registers came out exchanged. Giving the loop counter one more use raised its priority and the
registers swapped back; the family is `guarded_offset_index` and `case_temporary_split`.

Evidence: one function.

### KMC GCC 2.7.2: a branch delay slot left empty

The target filled the delay slot with a store. Moving the constant into its own scope filled it.

## Not a section of entries

### Unrelated frame note

The stack frame grew by eight bytes.
"""


def make_corpus(tmp_path):
    folder = tmp_path / "docs" / "learnings" / "finding"
    folder.mkdir(parents=True)
    (folder / "frame-size-from-spills.md").write_text(
        "---\ntitle: Frame size follows the spill slots\nkind: finding\ncompiler: KMC\nevidence: verified\n"
        "date: 2026-11-22\n---\nThe stack frame is 8 bytes too big. Declare the spilled `scratch_pair` local last and "
        "the frame shrinks.\n")
    (tmp_path / "docs" / "COMPILER_MATCHING_LEARNINGS.md").write_text(LEGACY)


def test_search_ranks_by_symptom_words_with_synonyms_across_both_kinds_of_entry(tmp_path):
    make_corpus(tmp_path)
    hits = learnings.search(tmp_path, "register swap")
    assert hits[0]["title"] == "KMC GCC 2.7.2: a swapped pair of saved registers"  # "swapped" is a synonym of "swap"
    assert hits[0]["groups"] >= 2 and "Giving the loop counter one more use" in hits[0]["fix"]
    assert learnings.search(tmp_path, "delay slot")[0]["title"].endswith("a branch delay slot left empty")
    frame = learnings.search(tmp_path, "frame size")
    assert frame[0]["where"] == "docs/learnings/finding/frame-size-from-spills.md" and frame[0]["note"] == "[verified] KMC"
    assert {h["title"] for h in frame} == {"Frame size follows the spill slots", "Unrelated frame note"}
    assert learnings.search(tmp_path, "quantum flux") == [] and learnings.search(tmp_path, "the of") == []


def test_search_names_the_families_an_entry_mentions(tmp_path):
    make_corpus(tmp_path)
    plain = learnings.search(tmp_path, "register swap")[0]
    assert plain["families"] == ["case_temporary_split", "guarded_offset_index"]  # no library: backticked names
    known = learnings.search(tmp_path, "register swap", known={"guarded_offset_index", "unrelated"})[0]
    assert known["families"] == ["guarded_offset_index"]
    assert learnings.search(tmp_path, "frame size", known={"scratch_pair"})[0]["families"] == ["scratch_pair"]


def test_search_command_prints_title_fix_and_families_and_says_when_nothing_matches(tmp_path, capsys):
    make_corpus(tmp_path)
    assert learnings.main(["--root", str(tmp_path), "search", "register", "swap", "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert "1. KMC GCC 2.7.2: a swapped pair" in out and "2. " not in out
    assert "fix: " in out and "families: case_temporary_split, guarded_offset_index" in out
    assert learnings.main(["--root", str(tmp_path), "search", "nonsense"]) == 0
    assert "no entry matches" in capsys.readouterr().out


def test_search_finds_the_repositorys_own_entries_by_their_symptom():
    hits = learnings.search(ROOT, "hoisted constant", top=3)
    assert hits and all(h["groups"] >= 1 for h in hits)
    assert any("hoist" in h["title"].lower() for h in hits)
