"""The map's layout and a cell's status (app/tui/mapgrid.py): plain functions, no terminal involved."""

import pytest

from decompnet.app.tui.mapgrid import ORDER, apply_claims, cell_of, cell_status, cells, layout, rows_used, status_of


def test_fewer_functions_than_cells_one_each_and_the_map_shrinks():
    assert cells(3, 80) == [range(0, 1), range(1, 2), range(2, 3)]  # one row of three
    assert rows_used(3, 80) == 1
    assert rows_used(80, 80) == 1  # exactly a row
    assert rows_used(81, 80) == 2  # spills into a second row
    assert rows_used(400, 80) == 5  # the full five, one function per cell (n == C)
    assert cells(0, 80) == []


def test_more_functions_than_cells_every_cell_full():
    layout = cells(401, 80)  # one over: 400 cells, one of them holds two functions
    assert len(layout) == 400
    assert [len(held) for held in layout].count(2) == 1 and [len(held) for held in layout].count(1) == 399
    assert layout[0].start == 0 and layout[-1].stop == 401
    for a, b in zip(layout, layout[1:]):  # runs of neighbours, in order, none skipped or doubled
        assert a.stop == b.start


def test_a_big_game_on_an_80_column_terminal():
    layout = cells(15_000, 80)
    assert len(layout) == 400
    sizes = {len(held) for held in layout}
    assert sizes == {37, 38}  # floor(15000/400) or ceil: every cell full, all functions placed
    assert sum(len(held) for held in layout) == 15_000


def test_a_cells_status_is_the_most_functions_ties_first_in_order():
    assert cell_status(["open", "open", "matched"]) == "open"
    for winner, loser in zip(ORDER, ORDER[1:]):  # a tie between two: the earlier status in ORDER wins
        assert cell_status([winner, loser]) == winner
        assert cell_status([loser, winner]) == winner
    assert cell_status([*["matched"] * 2, *["tried"] * 2, *["open"] * 2]) == "matched"
    assert cell_status(["polished", "matched"]) == "polished"  # polished outranks plain matched


def test_a_functions_status_in_the_windows_order():
    assert status_of({"status": "matched", "polished": 1}) == "polished"
    assert status_of({"status": "matched"}) == "matched"
    assert status_of({"status": "working"}) == "in progress"
    assert status_of({"status": "asm"}) == "not C"  # before tried, as cellColor checks it
    assert status_of({"status": "open", "best": 400}) == "tried"
    assert status_of({"status": "open", "best": None}) == "open"
    # as the window's cellColor: a set-aside function an agent holds still shows as set aside
    assert status_of({"status": "asm", "agent": "claude-3"}) == "not C"


def test_claims_are_laid_on_and_taken_off_as_the_window_does():
    rows = [{"name": "a", "status": "open"}, {"name": "b", "status": "working", "agent": "x"},
            {"name": "c", "status": "matched"}, {"name": "d", "status": "asm"}]
    apply_claims(rows, [{"name": "a", "agent": "claude-3"}, {"name": "c", "agent": "claude-3"},
                        {"name": "d", "agent": "claude-3"}])
    assert [status_of(r) for r in rows] == ["in progress", "open", "matched", "not C"]  # b's claim ended
    assert [r["agent"] for r in rows] == ["claude-3", None, "claude-3", "claude-3"]


def test_the_cursor_stays_on_its_function_across_a_resize():
    many = cells(15_000, 80)
    assert cell_of(many, 0) == 0 and cell_of(many, 14_999) == 399
    assert cell_of(many, 3_700) == 3_700 * 400 // 15_000  # the formula's own cell for function 3700
    narrower = cells(15_000, 60)  # 300 cells now: the same function lands in another
    again = cell_of(narrower, 3_700)
    assert 3_700 in narrower[again]
    assert again != cell_of(many, 3_700)
    one_each = cells(3, 80)
    assert [cell_of(one_each, i) for i in range(3)] == [0, 1, 2]


def test_odd_widths():
    assert len(cells(1_000, 37)) == 5 * 37
    assert sum(len(held) for held in cells(1_001, 13)) == 1_001
    with pytest.raises(ZeroDivisionError):
        rows_used(10, 0)  # a terminal with no width has no map; the caller never asks (mapview checks its size)


def test_a_game_with_room_to_spare_gets_blocks_that_fill_the_screen():
    grid = layout(95, 100, 36)  # Demo Quest 64 on a 41-line terminal
    assert (grid.cols, grid.bw, grid.bh) == (11, 9, 4) and len(grid.cells) == 95
    assert grid.cols * grid.bw <= 100 and -(-95 // grid.cols) * grid.bh <= 36  # it fits...
    assert -(-95 // grid.cols) * grid.bh == 36  # ...and uses every row
    for n, width, rows in ((8, 45, 6), (400, 80, 36), (1, 100, 36), (3600, 100, 36)):
        g = layout(n, width, rows)
        assert g.bh <= g.bw <= 3 * g.bh and g.cols * g.bw <= width and -(-n // g.cols) * g.bh <= rows


def test_a_game_bigger_than_the_screen_keeps_one_character_a_cell():
    grid = layout(15_000, 80, 19)
    assert (grid.cols, grid.bw, grid.bh, len(grid.cells)) == (80, 1, 1, 80 * 19)
