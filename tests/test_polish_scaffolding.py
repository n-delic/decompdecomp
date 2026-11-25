"""What polishing keeps: the m2c prelude and declarations a rewrite drops but still uses are put back before it's
compiled, the brief names the constructs there for the match and a refusal names the ones a rewrite changed; a rename
isn't refused for functions it can't change or that were broken already; hand-outs that never had a rewrite judged
give a function one fresh start; a huge function's C is paged."""

from decompnet.app import polish_scaffolding as ps

from .test_readable import matched  # noqa: F401
from .test_rom import built  # noqa: F401

PRELUDE = """#ifndef M2C_DEFINES
#define M2C_DEFINES
typedef s32 M2C_UNK;
#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((s8 *)(expr) + (offset)))
#endif
"""
ORIGINAL = PRELUDE + """
extern u32 D_800E1550;
extern s8 D_800E1554;

void func_8002E7A0(void *arg0, s32 arg1) {
    M2C_UNK temp;
    do {
        temp = M2C_FIELD(arg0, s32 *, 4);
        D_800E1550 = temp;
    } while (0);
loop:
    if (arg1-- != 0) goto loop;
}
"""


def test_the_prelude_and_used_declarations_are_put_back():
    body = ORIGINAL[ORIGINAL.index("void func_8002E7A0"):]
    restored, put = ps.restore(ORIGINAL, "/* Copies a field. */\n" + body, "func_8002E7A0")
    assert "#ifndef M2C_DEFINES" in restored and "extern u32 D_800E1550;" in restored
    assert "D_800E1554" not in restored  # not used by the body: nothing to put back
    assert len(put) == 2
    assert ps.restore(ORIGINAL, ORIGINAL, "func_8002E7A0") == (ORIGINAL, [])


def test_a_renamed_global_is_not_declared_again_under_its_old_name():
    body = ORIGINAL[ORIGINAL.index("void func_8002E7A0"):].replace("D_800E1550", "gCounter")
    restored, _ = ps.restore(ORIGINAL, "extern u32 gCounter;\n" + body, "func_8002E7A0")
    assert "D_800E1550" not in restored


def test_the_brief_and_a_refusal_name_the_match_scaffolding():
    brief = ps.brief(ORIGINAL)
    assert "m2c prelude" in brief and "while (0)" in brief and "goto loops" in brief
    unwrapped = ORIGINAL.replace("    do {\n", "").replace("    } while (0);\n", "")
    assert "do { ... } while (0) wrappers: 1 -> 0" in ps.changed(ORIGINAL, unwrapped)
    assert ps.changed(ORIGINAL, ORIGINAL.replace("temp", "value")) == ""
    assert ps.brief("s32 f(void) { return 0; }") == ""


def test_a_rename_skips_functions_whose_compiler_is_down_only_when_they_dont_use_the_name(matched, monkeypatch):  # noqa: F811
    from decompnet import toolchains
    net, slug, agent = matched
    proj = net._project(slug)
    monkeypatch.setattr(toolchains, "runtime_error", lambda tid: "its runner is stopped")
    matched_rows = net.project_store(slug).all("SELECT * FROM functions WHERE project_id=? AND status='matched'", proj["id"])
    current = {"": {"header": "extern s32 gOther;"}}
    clamp = next(r["id"] for r in matched_rows if r["name"] == "clamp_s32")
    assert clamp not in net._rename_unverifiable(proj, matched_rows, "clamp_s32", "clampValue", current)
    assert clamp in net._rename_unverifiable(proj, matched_rows, "gOther", "gLimit", current)
    macro = {"": {"header": "#define LIMIT gOther"}}
    assert net._rename_unverifiable(proj, matched_rows, "gOther", "gLimit", macro) == {}


def test_given_up_functions_without_a_judged_failure_get_one_fresh_start(matched):  # noqa: F811
    net, slug, agent = matched
    proj = net._project(slug)
    store = net.project_store(slug)
    with store.tx() as db:
        for _ in range(10):
            net._event(db, proj["id"], agent.id, "polish", "clamp_s32")
    from decompnet.app.readable import TODO
    todo = f"SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND name='clamp_s32' AND {TODO}"
    assert store.one(todo, proj["id"], proj["id"])["n"] == 0
    net.readability._retried.clear()
    net.readability._retry_unread(proj)
    assert store.one(todo, proj["id"], proj["id"])["n"] == 1


def test_a_matched_functions_long_c_is_paged_on_request(monkeypatch):
    from decompnet.app import agent_tokens
    text = "```c\n" + "x;\n" * 30000 + "\n```"
    monkeypatch.setattr(agent_tokens, "_function_view", lambda *a: text)
    page = agent_tokens.function_view(None, None, "s", "f", "matched", start=0, limit=12000)
    assert page.startswith("Reference page 0:12000") and len(page) < 13000
    assert agent_tokens.function_view(None, None, "s", "f", "candidate") == text


def test_a_claim_shows_a_huge_functions_c_in_part_with_where_the_rest_is(matched, monkeypatch):  # noqa: F811
    from decompnet.app import readable
    net, slug, agent = matched
    monkeypatch.setattr(readable, "BRIEF_C", 60)
    brief = net.readability.claim(agent, slug)
    assert "continues" in brief and 'section="matched", start=' in brief
