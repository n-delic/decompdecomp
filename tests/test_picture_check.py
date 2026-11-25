"""Check pictures (app/picture_check.py, port/frame_compare.py): the comparison engine's parts that need no ROM or
emulator, the choice of frames, and what the Porting tab is told. The run itself needs a game and the reversing emulator."""
from __future__ import annotations

import json
from pathlib import Path

from decompnet.app import picture_check
from decompnet.port import frame_compare


def test_points_are_windows_of_consecutive_tasks_from_after_the_save_is_read():
    chosen = picture_check.points(6000)
    assert chosen[0] == 600 and chosen[-1] < 6000 and chosen == sorted(set(chosen))
    assert len(chosen) == picture_check.POINTS * picture_check.WINDOW  # windows of consecutive tasks
    assert all(n + 1 in chosen for n in chosen[:picture_check.WINDOW - 1])
    assert picture_check.points(10)[0] >= 1


def _saved(root: Path, pictures: list[dict], tolerance: int = 3) -> None:
    path = picture_check.results_file(root)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"at": 1.0, "settings": {"tasks": 6000, "input": "", "tolerance": tolerance, "pace": True},
                                "pictures": pictures}))


def test_summary_counts_identical_shifted_and_differing_pictures(tmp_path):
    assert picture_check.summary(tmp_path) is None
    _saved(tmp_path, [
        {"task": 600, "image": 1, "differ_pct": 0.0, "beyond_pct": 0.0, "near_pct": 0.0, "file": "a.png"},
        {"task": 700, "image": 1, "differ_pct": 4.0, "beyond_pct": 1.0, "near_pct": 0.0, "file": "b.png"},
        {"task": 800, "image": 1, "differ_pct": 30.0, "beyond_pct": 20.0, "near_pct": 12.5, "file": "c.png"},
        {"task": 900, "image": 1, "differ_pct": 9.0, "beyond_pct": 2.0, "near_pct": 2.0, "file": "d.png"},
        {"task": 1000, "missing": "port"},
    ])
    s = picture_check.summary(tmp_path)
    assert (s["compared"], s["exact"], s["shifted"], s["same"], s["differ"], s["missing"]) == (4, 1, 1, 2, 2, 1)
    assert [p["task"] for p in s["differing"]] == [800, 900]  # worst first
    assert "frame 800" in s["examples"][0]


def test_summary_without_a_tolerance_judges_by_exact_pixels(tmp_path):
    _saved(tmp_path, [{"task": 600, "image": 1, "differ_pct": 4.0, "beyond_pct": 1.0, "near_pct": None, "file": "a.png"}], 0)
    s = picture_check.summary(tmp_path)
    assert s["differ"] == 1 and s["same"] == 0


def test_a_kept_picture_is_found_by_its_name_only_inside_the_pictures_folder(tmp_path):
    (picture_check.folder(tmp_path)).mkdir(parents=True)
    (picture_check.folder(tmp_path) / "a.png").write_bytes(b"x")
    (tmp_path / "secret.png").write_bytes(b"x")
    assert picture_check.image_path(tmp_path, "a.png") is not None
    assert picture_check.image_path(tmp_path, "../secret.png") is None
    assert picture_check.image_path(tmp_path, "a.txt") is None


def test_the_engine_keeps_the_tools_names():
    # tools/n64_frame_compare.py re-exports these; the app's job and the tool are one engine
    for name in ("build", "draw", "frames", "with_task", "console_save", "prime_save", "run_port", "run_console",
                 "unmatched", "side_by_side", "compare_run", "graphics_tasks"):
        assert callable(getattr(frame_compare, name))


def test_a_save_is_found_in_the_console_ram_past_its_clock_header(tmp_path):
    body = bytes(range(64, 96))
    port_save = bytes(16) + body + bytes(32)
    ram = bytearray(0x1000)
    ram[0x200:0x200 + 80] = b"\x11" * 16 + body + b"\x22" * 32
    dump = tmp_path / "ram.bin"
    dump.write_bytes(bytes(ram))
    assert frame_compare.console_save(dump, port_save) == b"\x11" * 16 + body + b"\x22" * 32


def test_a_picture_is_drawn_over_what_the_tasks_before_it_drew(tmp_path, monkeypatch):
    """The emulator draws nothing, so a buffer's earlier pictures are carried into the next task's RAM before it is drawn."""
    drawn = []

    def fake_draw(program, dump, task, work, kept=None):
        drawn.append(dump.read_bytes()[0x100:0x104])  # (the copy with the task and the carried pictures)
        if kept is not None:
            kept[0x100] = bytes([len(drawn)] * 4)
        return {}

    monkeypatch.setattr(frame_compare, "draw", fake_draw)
    dump = tmp_path / "ram.bin"
    dump.write_bytes(bytes(0x1000))
    dump.with_name(dump.name + ".task").write_bytes(bytes(64))  # the task the console handed over
    carry: dict[int, bytes] = {}
    frame_compare.frames(Path("x"), dump, tmp_path, carry)
    frame_compare.frames(Path("x"), dump, tmp_path, carry)
    assert drawn[0] == bytes(4) and drawn[1] == bytes([1] * 4) and carry[0x100] == bytes([2] * 4)


def test_a_task_that_draws_fewer_lines_leaves_the_rest_of_the_carried_picture(tmp_path, monkeypatch):
    """A menu's first task draws 240 lines and its second 480: the first task's shorter picture must not cut the lower
    half (seeded or drawn before) out of what is carried on."""
    sizes = iter([8, 4])

    def fake_draw(program, dump, task, work, kept=None):
        kept[0x100] = bytes([7]) * next(sizes)
        return {}

    monkeypatch.setattr(frame_compare, "draw", fake_draw)
    dump = tmp_path / "ram.bin"
    dump.write_bytes(bytes(0x1000))
    dump.with_name(dump.name + ".task").write_bytes(bytes(64))
    carry = {0x100: bytes([9]) * 16}  # a seeded buffer of 16 bytes
    frame_compare.frames(Path("x"), dump, tmp_path, carry)
    frame_compare.frames(Path("x"), dump, tmp_path, carry)
    assert carry[0x100] == bytes([7]) * 4 + bytes([7]) * 4 + bytes([9]) * 8  # the second task's 4 bytes, then the first's, then the seed


def test_a_seeding_override_is_found_by_the_function_that_replays_the_seed(tmp_path):
    src = tmp_path / "port" / "wasm" / "src"
    src.mkdir(parents=True)
    (src / "other.c").write_text("void helper(int x) { x++; }\n")
    (src / "rand_seed.c").write_text("extern u32 port_seed_replay(u32 fallback);\nvoid rand_seed(s32 seed) {\n"
                                     "    *(s32 *)0 = (s32)port_seed_replay((u32)seed);\n}\n")
    assert picture_check.seeding_function(tmp_path) == "rand_seed"
    assert picture_check.seeding_function(tmp_path / "nothing") is None


def test_the_seed_offset_is_the_ports_extra_calls_at_the_same_task():
    task_vi = {n: 100 + n for n in range(200, 700, 50)}  # the console's retrace at each task
    seed_vis = list(range(300, 800, 10))  # its calls, one every ten retraces
    import bisect
    console = {n: bisect.bisect_right(seed_vis, task_vi[n]) for n in task_vi}
    assert frame_compare.seed_offset(console, task_vi, seed_vis) == 0
    ahead = {n: c + 1 for n, c in console.items()}  # an extra call at the port's boot
    assert frame_compare.seed_offset(ahead, task_vi, seed_vis) == 1
    drift = {n: c + 1 + (n > 450) for n, c in console.items()}  # then a frame ahead later: the smallest gap counts
    assert frame_compare.seed_offset(drift, task_vi, seed_vis) == 1
    assert frame_compare.seed_offset({}, task_vi, seed_vis) == 0


def test_the_seed_offset_ignores_the_tasks_before_either_side_has_called():
    task_vi = {n: n for n in range(200, 700, 50)}
    seed_vis = [420]  # the console's first call
    console = {200: 0, 250: 0, 300: 0, 350: 0, 400: 0, 450: 1, 500: 1, 550: 1, 600: 1, 650: 1}
    port = {n: c + (1 if n >= 450 else 0) + (0 if n < 450 else 0) for n, c in console.items()}
    port[400] = 1  # the port called a little before the console did
    assert frame_compare.seed_offset(port, task_vi, seed_vis) == 1
