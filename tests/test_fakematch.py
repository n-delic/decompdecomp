"""Fakematches (fakematch.py): C that reaches the game's bytes through a trick that steers the compiler is named where
a match is announced and in the polishing brief, and the permuter doesn't judge a rewrite that adds one. The checks
are narrow: the genuine idioms they look like (hardware `volatile`, empty loop bodies, a macro's `do { } while (0)`)
pass."""

import pytest

from decompnet import fakematch
from decompnet.app import tools
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def body(*lines: str) -> str:
    return "s32 f(s32 a, s32 b) {\n" + "".join(f"    {line}\n" for line in lines) + "    return a;\n}\n"


@pytest.mark.parametrize("line, what", [
    ("volatile s32 v = a;", fakematch.VOLATILE),
    ("s32 * volatile p = 0;", fakematch.VOLATILE),
    ("do { a++; } while (0);", fakematch.DO_WHILE_0),
    ("if (0) { a = 2; }", fakematch.CONSTANT_IF),
    ("if (a) { }", fakematch.EMPTY_IF),
    ("if (a) ;", fakematch.EMPTY_IF),
    ("if (a) b++; else ;", fakematch.EMPTY_IF),
    ("a++; { }", fakematch.EMPTY_BLOCK),
    ("a++; ;", fakematch.BARE_SEMICOLON),
    ("a = a;", fakematch.SELF_ASSIGN),
    ("b = a + 0;", fakematch.PLUS_ZERO),
    ("b = a * 1;", fakematch.TIMES_ONE),
    ('asm("nop");', fakematch.ASM),
])
def test_each_trick_is_named_on_its_line(line, what):
    found = fakematch.findings(body("b = 1;", line))
    assert [(f.line, f.what) for f in found] == [(3, what[0])]


@pytest.mark.parametrize("line", [
    "*(volatile u8 *)0xA4600010 = a;",  # a hardware register, through a pointer
    "volatile u32 *reg = (volatile u32 *)0xA4600010;",
    "b = (volatile s32)a;",  # a cast, not a local
    "while (*(u8 *)b++) ;",  # an empty loop body
    "for (;;) { if (a) break; }",
    "switch (a) { case 1: ; default: break; }",
    "b = a + 0x10 + 0.5f;",  # neither is `+ 0`
    "b = a * 10 * 1.5f;",
    "b = (a == a);",
    "s32 t[2] = { 0 };",
    'register s32 r asm("r4") = a;',  # a register variable, where the project uses them
    "/* volatile s32 x; if (0) { } */",  # comments and strings aren't code
    'b = (s32)"x = x;";',
])
def test_the_genuine_idioms_they_look_like_pass(line):
    assert fakematch.findings(body(line)) == []


def test_only_function_bodies_are_looked_at():
    """A macro's own `do { } while (0)` is the idiom; a struct's `volatile` member and data initialisers aren't a
    function's code."""
    source = ("#define CALL(x) do { g(x); } while (0)\n"
              "#define TWICE(x) \\\n    do { g(x); g(x); } while (0)\n"
              "typedef struct { volatile s32 busy; } Dma;\n"
              "static s32 table[] = { 1, 2 };\n"
              "void f(void) { CALL(1); }\n")
    assert fakematch.findings(source) == []


def test_a_variant_adds_a_trick_only_when_it_has_more_than_its_base():
    base = body("b = a + 0;")
    assert not fakematch.added(base, body("b = a + 0;", "a++;"))
    assert fakematch.added(base, body("b = a + 0;", "b = b + 0;"))  # one more of the same kind
    assert fakematch.added(base, body("volatile s32 v = b;", "b = a + 0;"))
    assert not fakematch.added(body("volatile s32 v = b;"), body("s32 v = b;"))  # taking one out is the goal


def test_a_match_note_names_the_tricks_and_says_how_to_replace_them():
    assert fakematch.note(body("b++;"), "AGAIN") == ""
    text = fakematch.note(body("volatile s32 v = a;", "a = a;"), "AGAIN")
    assert "line 2: `volatile` on a local" in text and "line 3: a self-assignment" in text
    assert 'guide("rules")' in text and text.endswith("AGAIN")
    many = fakematch.note(body(*(["a = a;", "b++;"] * (fakematch.SHOWN + 2))), "AGAIN")
    assert "and 2 more" in many


def test_every_helper_reads_the_rules_guide_up_front():
    assert "rules" in tools.GUIDES and "## Fakematches" in tools.guide_text("rules")
    for platform, kind in (("n64", "rom"), ("ps2", "rom"), ("gc", "fork"), ("pc", "fork")):
        assert "## Fakematches" in tools.up_front(platform, kind, ""), (platform, kind)


@pytest.fixture
def rom(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    return net, slug, agent


def test_a_match_through_a_trick_is_saved_and_named(rom):
    """The judge decides the bytes, so the match is kept; the helper is told what its C hides and how to replace it."""
    net, slug, agent = rom
    trick = CLAMP.replace("    return v;", "    v = v;\n    return v;")
    text = net.compile_and_diff(agent, "func_800007D0", trick, slug)
    assert text.startswith("✅ MATCH") and "a self-assignment" in text and "make_readable" in text
    assert net._function("func_800007D0", slug)["status"] == "matched"
    brief = net.readability.claim(agent, slug)
    assert "tricks that steer the compiler" in brief and "a self-assignment" in brief
    assert net.readability.submit(agent, slug, "func_800007D0", CLAMP).startswith("✅")  # the real form still matches


def test_polishing_refuses_a_rewrite_that_adds_a_trick(rom):
    net, slug, agent = rom
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert "tricks that steer" not in net.readability.claim(agent, slug)
    trick = CLAMP.replace("    return v;", "    volatile s32 kept = v;\n    return kept;")
    with pytest.raises(NetworkError, match="adds a trick"):
        net.readability.submit(agent, slug, "func_800007D0", trick)
