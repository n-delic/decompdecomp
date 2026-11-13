"""Large functions (the largest open function is 2021 instructions) must not dump their whole assembly
into claim_function/get_function — the agent client truncates the oversized reply, so most of the target is lost.
scoring.asm_page slices compacted assembly by line so the brief shows a first page and read_asm fetches the rest;
read_asm part="draft" pages the m2c draft / best-attempt C the same way."""
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


def test_count_nonpositive_is_capped_not_unbounded():
    # count <= 0 must mean "one capped page", not "everything past the cap" (which floods context).
    big = "\n".join(f"l{i}" for i in range(2000))
    page, total, nxt = scoring.asm_page(big, 0, 0)
    assert total == 2000
    assert len(page.splitlines()) <= 1500  # a capped page, not all 2000
    assert nxt is not None and nxt <= 1500  # more remains to page


def test_count_above_the_cap_is_clamped():
    big = "\n".join(f"l{i}" for i in range(2000))
    page, _total, nxt = scoring.asm_page(big, 0, 99999)
    assert len(page.splitlines()) == scoring.ASM_PAGE_MAX and nxt == scoring.ASM_PAGE_MAX


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


def test_read_asm_draft_pages_the_shown_draft_not_the_raw_seed():
    # For a C++ method the brief shows Class::method(...), not the raw m2c `func(void *arg0, ...)`. read_asm must
    # page that SAME shown draft, or page 2 would not continue the page 1 the agent saw in the brief.
    fn = {"name": "big", "project_id": 1, "best_source": None,
          "seed": "void unk(void *a) {\n  raw0;\n  raw1;\n  raw2;\n  raw3;\n}"}
    shown = "void Thing::run(Thing *this) {\n  shown0;\n  shown1;\n  shown2;\n  shown3;\n}"
    net = _net(fn, ["x"])
    net._shown_draft = lambda f, p: shown  # the engine renders the method draft; the brief shows exactly this
    p1 = net.read_asm("big", start=0, count=2, part="draft")  # lines 0-1: signature + shown0
    assert "Thing::run" in p1 and "shown0" in p1 and "shown1" not in p1
    assert "raw0" not in p1  # the raw m2c seed is not what gets paged
    p2 = net.read_asm("big", start=2, count=2, part="draft")  # lines 2-3: shown1 + shown2
    assert "shown1" in p2 and "shown2" in p2 and "shown0" not in p2  # page 2 continues the shown draft
    assert "raw" not in p2


def test_read_asm_header_does_not_print_a_past_the_end_start():
    fn = {"name": "big", "project_id": 1, "best_source": None, "seed": None}
    net = _net(fn, [f"insn{i}" for i in range(3)])
    out = net.read_asm("big", start=99, count=4, part="asm")
    assert "of 3" in out
    assert "99–99" not in out and "99-99" not in out  # not "lines 99-99 of 3"
    assert "lines 3–3 of 3" in out  # the out-of-range start is clamped to the end


def test_every_handler_offered_to_the_api_agent_has_a_schema():
    from decompnet.agents.api import api_tool_schemas
    from decompnet.app import tools
    for fork in (False, True):
        offered = set(tools.tool_names(fork))
        have = {t["function"]["name"] for t in api_tool_schemas(fork=fork, names=list(offered))}
        missing = offered - have
        assert not missing, f"fork={fork}: tools offered to the API agent with no schema: {sorted(missing)}"
    everything = {t["function"]["name"] for t in api_tool_schemas(names=list(tools.HANDLERS))} | \
                 {t["function"]["name"] for t in api_tool_schemas(fork=True, names=list(tools.HANDLERS))}
    assert not (set(tools.HANDLERS) - everything), \
        f"handlers with no schema: {sorted(set(tools.HANDLERS) - everything)}"
