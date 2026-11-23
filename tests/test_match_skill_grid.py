"""grid.py (the match-function skill) scores every combination of marked alternatives and statement orders with the
app's judge; a stub judge stands in for it here."""
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


grid = load("grid")

SOURCE = """void f(void) {
    x = /*{*/a + b/*|*/b + a/*|*/a - -b/*}*/;
    /*permute{*/
    gA = 1;
    gB = 2;
    gC = 3;
    /*}permute*/
    y = /*{*//*|*/z/*}*/;
}
"""


class Stub:
    """Scores a source by how many of the wanted fragments it holds in order; 0 when all are there."""

    closed = 0
    calls = []

    def __init__(self, *_, **__):
        pass

    def score(self, source):
        Stub.calls.append(source)
        if "syntax" in source:
            return None
        order = [source.index(w) for w in ("gC", "gA", "gB")]
        return (0 if order == sorted(order) else 10) + (0 if "b + a" in source else 5) + (0 if "z" in source else 1)

    def judge(self, source, diff=False, rows=None):
        return {"score": self.score(source), "diff": "rows differ"}

    def close(self):
        Stub.closed += 1


def test_the_grid_is_every_alternative_times_every_statement_order():
    template = grid.Template(SOURCE)
    total, where = grid.breakdown(template)
    assert total == 3 * 6 * 2 and "permute block of 3 lines = 6 orders" in where
    sources = [source for _, source in grid.candidates(template)]
    assert len(sources) == len(set(sources)) == 36
    assert all("/*" not in s for s in sources)
    assert any("x = b + a;\n    gC = 3;\n    gA = 1;\n    gB = 2;\n    y = z;" in s for s in sources)


def test_equal_lines_are_not_counted_twice_and_the_first_order_is_the_file_as_written():
    template = grid.Template("/*permute{*/\na;\na;\nb;\n/*}permute*/\n")
    assert grid.breakdown(template)[0] == 3
    assert [s for _, s in grid.candidates(template)][0] == "a\na\nb\n".replace("a", "a;").replace("b", "b;")


def test_alternatives_can_sit_inside_a_permuted_line():
    template = grid.Template("/*permute{*/\np = /*{*/1/*|*/2/*}*/;\nq = 3;\n/*}permute*/")
    sources = {s for _, s in grid.candidates(template)}
    assert sources == {"p = 1;\nq = 3;", "q = 3;\np = 1;", "p = 2;\nq = 3;", "q = 3;\np = 2;"}


def test_unbalanced_nested_and_tiny_blocks_are_refused_with_a_sentence():
    for bad, message in (("/*{*/a/*|*/b", "no matching"), ("/*{*/a/*{*/b/*|*/c/*}*/ d/*}*/", "don't nest"),
                         ("/*permute{*/\na;\n/*}permute*/", "at least two"), ("/*permute{*/\na;\nb;\n", "no matching"),
                         ("a;\n/*}permute*/", "no matching")):
        with pytest.raises(grid.GridError, match=message):
            grid.Template(bad)


def test_search_ranks_by_score_with_none_last_and_closes_every_worker():
    Stub.closed = 0
    template = grid.Template("/*{*/gC syntax/*|*/ok gC gA gB b + a z/*|*/gA gB gC b + a z/*}*/")
    results, error = grid.search(template, Stub, jobs=2)
    assert error is None and len(results) == 3
    assert [s for s, _ in results[:2]] == [0, 10] and results[-1][0] is None
    assert Stub.closed in (1, 2)


def test_a_failing_worker_is_reported_and_the_rest_still_score():
    class Flaky(Stub):
        def score(self, source):
            if "b + a" in source:
                raise SystemExit("worker died")
            return 1

    results, error = grid.search(grid.Template("/*{*/a/*|*/b + a/*}*/"), Flaky, jobs=1)
    assert error == "worker died" and sorted(s for s, _ in results if s is not None) == [1] and results[-1][0] is None


def test_stop_on_match_skips_the_remaining_candidates():
    Stub.calls = []
    template = grid.Template("/*{*/gC gA gB b + a z/*|*/q/*|*/r/*|*/s/*}*/")
    results, _ = grid.search(template, Stub, jobs=1, stop_on_match=True)
    assert results[0][0] == 0 and len(Stub.calls) == 1


def test_main_refuses_a_grid_over_the_cap_and_names_the_cause(tmp_path, capsys):
    path = tmp_path / "c.c"
    path.write_text("/*permute{*/\n" + "".join(f"s{i};\n" for i in range(8)) + "/*}permute*/\n")
    assert grid.main([str(path), "--slug", "s", "--function", "f"], make_judge=Stub) == 2
    err = capsys.readouterr().err
    assert "refusing 40320 candidates" in err and "8 lines" in err and "--max 5000" in err
    assert grid.main([str(path), "--count"]) == 0 and capsys.readouterr().out.strip() == "40320"


def test_main_prints_the_top_candidates_writes_the_best_and_diffs_it(tmp_path, capsys):
    path = tmp_path / "c.c"
    path.write_text(SOURCE)
    out = tmp_path / "best.c"
    code = grid.main([str(path), "--slug", "s", "--function", "f", "--top", "3", "--out", str(out), "--diff", "--jobs", "2"],
                     make_judge=Stub)
    text = capsys.readouterr().out
    assert code == 0 and len(text.strip().splitlines()) == 3 + 1 and "rows differ" in text
    best = out.read_text()
    assert "x = b + a;" in best and best.index("gC = 3") < best.index("gA = 1") and "y = z" in best
    assert grid.main([str(path), "--base"]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "    x = a + b;"


def test_main_says_what_a_bad_marker_is(tmp_path, capsys):
    path = tmp_path / "c.c"
    path.write_text("/*{*/a/*|*/b")
    assert grid.main([str(path), "--count"]) == 2 and "grid:" in capsys.readouterr().err


def test_candidates_compiling_to_identical_code_get_the_invariant_spelling_hint(capsys, tmp_path):
    invariant = load("invariant")
    assert invariant.groups([("a", "x"), ("b", "x"), ("c", "x"), ("d", "y"), ("e", None)]) == [["a", "b", "c"]]
    assert invariant.hint([("a", "x"), ("b", "x")]) == []  # two can be one cosmetic edit
    line = invariant.hint([(str(i), "x") for i in range(6)])[0]
    assert line.startswith("hint: 6 distinct candidates (0, 1, 2, 3 and 2 more) compiled to identical instructions")
    assert "signatures.py" in line and "explain_regs.py" in line

    class Same(Stub):
        def judge(self, source, diff=False, rows=None):
            return {"score": 7, "code": "same"}
    path = tmp_path / "g.c"
    path.write_text(SOURCE)
    assert grid.main([str(path), "--slug", "s", "--function", "f", "--jobs", "1"], make_judge=Same) == 0
    assert "36 distinct candidates" in capsys.readouterr().err



class RowStub(Stub):
    """The same stub with rows: a row differs for each misplaced fragment, whatever its score says."""

    def rows(self, source):
        score = self.score(source)
        if score is None:
            return None, []
        # the score weighs the missing `b + a` heavily; the row count sees only the statement order
        order = [source.index(w) for w in ("gC", "gA", "gB")]
        return score, [("o", "gx", "gy")] * (0 if order == sorted(order) else 1)


def test_by_rows_ranks_the_fewest_differing_rows_first_and_the_score_breaks_ties():
    template = grid.Template(SOURCE)
    results, error = grid.search(template, RowStub, jobs=1, by_rows=True)
    assert error is None
    best_score, best = results[0]
    assert grid.search.bad[best] == 0
    rows = [grid.search.bad[d] for _, d in results]
    assert rows == sorted(rows)
    zero_row = [(s, d) for s, d in results if grid.search.bad[d] == 0]
    assert [s for s, _ in zero_row] == sorted(s for s, _ in zero_row)
    # ranked by score alone, a candidate with a misplaced statement and a better score could come first
    by_score, _ = grid.search(template, RowStub, jobs=1)
    assert by_score[0][0] == 0


def test_differing_counts_rows_with_a_marker():
    assert grid.differing([("", "a", "a"), ("o", "b", "c"), (" ", "d", "d"), ("r", "e", "f"), ("...", "", "")]) == 2
