"""regions.py (the match-function skill): a large function's diff grouped by the target's basic blocks, so an agent
works one region at a time and sees which already match."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from decompnet import hosted_judge
from tests.test_hosted import project

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("skill_regions", SCRIPTS / "regions.py")
regions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regions)


def same(text):
    return (" ", text, text)


def test_blocks_start_at_branch_targets_and_after_a_delay_slot_but_not_after_calls():
    targets = ["addiu $sp, $sp, -0x18", "jal func_1", "nop", "beqz $a0, 0x14", "nop", "addu $v0, $a0, $a1",
               "jr $ra", "nop"]
    assert regions.block_starts(targets, "mips") == [0, 5]  # 0x14 / 4 = 5, and after beqz's delay slot; jr ends last


def test_regions_merge_blocks_count_rows_by_kind_and_point_at_the_first_to_fix():
    rows = [same(f"addu $t{i % 8}, $a0, $a1") for i in range(10)]
    rows += [same("beqz $a0, 0x40"), same("nop")]  # a block starts at 12 (after the slot) and at 16 (0x40)
    rows += [("r", "addu $t0, $a0, $a1", "addu $t1, $a0, $a1"), (">", "", "move $v0, $zero"), same("nop"),
             same("nop"), same("nop")]
    rows += [("s", "lw $a0, 0x10($sp)", "lw $a0, 0x18($sp)")] + [same("nop")] * 6
    found = regions.regions(rows, "mips", size=4)
    assert [(r["first"], r["end"]) for r in found] == [(0, 12), (12, 16), (16, 23)]
    assert [r["differ"] for r in found] == [0, 2, 1]
    assert found[1]["kinds"] == {"r": 1, ">": 1} and found[1]["estimate"] == 105 and found[1]["where"] == "0x30-0x40"
    text = regions.table(found)
    assert "3 regions, 1 already match" in text and text.splitlines()[2].endswith("<- first to fix")
    assert "move $v0, $zero" in regions.show(found[1])


def test_the_worker_returns_every_raw_row_on_request(tmp_path, monkeypatch):
    net = project(tmp_path)
    rows = [("r", "jr $ra", "jr $v0")] * 300
    diff = SimpleNamespace(score=12, rows=rows, render=lambda **kw: "rendered")
    monkeypatch.setattr(net, "_judge_with", lambda proj, fn, toolchain, flags, source: (False, diff, ""))
    request = {"slug": "game", "function": {"name": "f1", "vram": 0x80001100, "target": bytes([1, 3, 0xE0, 8]).hex()},
               "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}, "source": "int f1(void){return 0;}"}
    assert "rows" not in hosted_judge.judge(net, request)
    reply = hosted_judge.judge(net, request | {"raw_rows": True})
    assert len(reply["rows"]) == 300 and reply["rows"][0] == ["r", "jr $ra", "jr $v0"]
