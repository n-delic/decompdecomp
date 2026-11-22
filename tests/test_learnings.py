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
