"""Large functions (the largest open function is 2021 instructions) must not dump their whole assembly
into claim_function/get_function — the agent client truncates the oversized reply, so most of the target is lost.
scoring.asm_page slices compacted assembly by line so the brief shows a first page and read_asm fetches the rest;
read_asm part="draft" pages the m2c draft / best-attempt C the same way."""
import functools

from decompnet import scoring
from decompnet.network import Network

ASM = "l0\nl1\nl2\nl3\nl4"  # 5 lines


def test_first_page():
    page, total, nxt = scoring.asm_page(ASM, 0, 2)
    assert page == "l0\nl1" and total == 5 and nxt == 2


def test_middle_page():
    page, total, nxt = scoring.asm_page(ASM, 2, 2)
    assert page == "l2\nl3" and total == 5 and nxt == 4


def test_last_page_has_no_next():
    page, total, nxt = scoring.asm_page(ASM, 4, 2)
    assert page == "l4" and total == 5 and nxt is None


def test_whole_fits_no_next():
    page, total, nxt = scoring.asm_page("a\nb\nc", 0, 100)
    assert page == "a\nb\nc" and total == 3 and nxt is None


def test_negative_start_clamped():
    page, total, nxt = scoring.asm_page(ASM, -5, 2)
    assert page == "l0\nl1" and nxt == 2


def test_start_past_end_empty():
    page, total, nxt = scoring.asm_page(ASM, 99, 2)
    assert page == "" and total == 5 and nxt is None


# --- read_asm (engine method) paging asm and draft, with a stubbed minimal engine ---

def _net(fn, asm_lines=None):
    net = Network.__new__(Network)
    net._function = lambda function, project=None: fn
    net._asm_text = lambda f, p: "\n".join(asm_lines or [])
    net.fork_slug = lambda s: None

    class _Store:
        def one(self, sql, *a):
            return {"id": fn["project_id"], "slug": "proj"}
    net.store = _Store()
    return net


def test_read_asm_pages_target_assembly():
    fn = {"name": "big", "project_id": 1, "best_source": None, "seed": None}
    net = _net(fn, [f"insn{i}" for i in range(10)])
    out = net.read_asm("big", start=0, count=4, part="asm")
    assert "lines 0" in out and "of 10" in out
    assert "insn0" in out and "insn3" in out and "insn4" not in out
    assert 'read_asm("big", start=4)' in out


def test_read_asm_pages_the_draft():
    fn = {"name": "big", "project_id": 1, "best_source": "a\nb\nc\nd", "seed": None}
    net = _net(fn, ["x"])
    out = net.read_asm("big", start=0, count=2, part="draft")
    assert "a\nb" in out and "c\nd" not in out      # only the first two draft lines
    assert 'part="draft"' in out and "start=2" in out


def test_read_asm_draft_when_none():
    fn = {"name": "big", "project_id": 1, "best_source": None, "seed": None}
    net = _net(fn, ["x"])
    out = net.read_asm("big", part="draft")
    assert "no draft" in out.lower()
