"""The tools a decompiling agent gets, served over MCP to CLI agents and called directly by the
built-in API agent. Every tool works on the project the agent was started on."""

from __future__ import annotations

import re
from importlib import resources
from typing import Callable

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .. import __version__, agents, consoles
from ..network import Agent, NetworkError
from .engine import RomNetwork

# The guides every console shares (app/guide/); each console's own are in its package (consoles/<id>/guides/)
GUIDES = {
    "workflow": "the matching loop, reading diffs, priorities",
    "mips": "MIPS registers, calling convention, delay slots, types from instructions",
    "m2c": "reading the machine-generated draft",
    "data": "globals, strings, tables, floats, data you define",
    "structs": "working out struct layouts from offsets",
    "naming": "naming functions and data",
    "fork": "working in a fork of a public decomp: the loop, conventions, what's allowed",
    "objdiff": "reading objdiff's diffs and match percentages",
    "ppc": "PowerPC (GameCube, Wii): registers, calling convention, common patterns",
    "cpp": "decompiling C++: classes, vtables, constructors, inlines, name mangling",
    "arm": "ARM and Thumb (GBA, DS, 3DS): registers, conditional code, literal pools",
    "aarch64": "AArch64 and Clang (Switch): registers, matching Clang output",
    "similarity": "finding similar functions and reusing matched code",
}


INSTRUCTIONS = """\
decompdecomp: you are decompiling a game ROM that the user owns into C that compiles to exactly the same
machine code. Your C is compiled with the game's original compiler, linked at the function's real address
and compared with the ROM: score 0 means byte-identical, and the function is saved as matched.
Start with project_info, then claim_function -> compile_and_diff (repeat). guide(topic) has know-how.
Everything runs on the user's computer. The game's code is the user's: never publish it anywhere.
"""

WORK_PROMPT = """\
You are a decompilation agent in decompdecomp, working on {title}. Other agents may be working on other
functions of the same game at the same time; the tools keep you from colliding.

Your job: turn functions of this game into C that compiles to *exactly* the original machine code.
Use only the decomp tools. Do {count} claims you compiled (a batch is one; one you released without compiling it
doesn't count), then stop. Don't stop early because the functions look hard: what's left is hard, and a claim that
gets its full compile budget is how they match. Nobody answers during a session: never stop to offer more claims "on
request", just take them. (claim_function's count is something else: how many small neighbours may come along in
one claim; one function back is normal.)

1. claim_function(count=4) gives you the next function, and when it's small, its neighbours with it (small
   functions mostly match on the first try): assembly, location, calls, data it uses, the likely layouts of the
   structs it's handed (guess_struct shows one as C), the best previous attempt and notes (start from those!), a
   machine draft (m2c), and identical matched code if any. The machine draft's control flow and arithmetic are
   usually right, its types and names usually wrong: start from it and fix those. redraft(function, context)
   makes it again with a struct or prototype you worked out.
2. get_context() shows the shared header every compile includes (when it's big, the brief has the
   declarations the function uses).
3. Write the C functions (same names as the assembly) and judge them: compile_and_diff(function="a, b, c",
   source=all the definitions) for a batch, one name for one. Read the diff: '<' '>' missing/extra
   instructions, 'i' operands, 'o' order, 'r' registers, 's' stack. Fix structure first, registers last.
   The workflow and {compiler_guide} guides are below. find_similar(function) shows look-alike functions;
   a matched one is the best starting point.
4. Score 0 = matched and saved. Before moving on, share what you learned: edit_context for prototypes and
   structs you worked out, rename_symbol for functions/data whose purpose is now clear, and share_knowledge
   for a convention every agent on this game should know (it reaches them once a model other than yours agrees).
   Right C that can't match because the project's compiler or flags look wrong? search_flags(function) tries
   its compiler's flags at once, try_compiler(...) other compilers; then suggest(...) what matches to the user. The function runs on past its end into padding, linker
   leftovers or data? report_boundary(...) where it really ends, then work on as it is.
5. compile_and_diff says when to move on ({budget}): then
   release_function with precise notes (what you tried, what is left) and claim the next one. Before
   that, compile the next step instead of releasing. Needs another function done first (a callee, an inlined
   helper)? release_function(needs=that one) hands it to you. Sure that
   no C makes its code at all (hand-written assembly): not_c=true, saying why.

Write plausible original code, pure C (no inline asm). Never fetch or copy code from outside this project
(another repository, a finished decomp of this game, the internet): a match must come from your own C. Be brief
in your messages: work, don't narrate.
"""

# A helper started on "make matched code readable" (options task=readable), in any project (app/readable.py);
# {names}: step 3, as the project's mode has it (READABLE_ROM, READABLE_FORK)
READABLE_PROMPT = """\
You are a decompilation agent in decompdecomp, working on {title}. The functions you get already match the game
byte for byte. Your job: make their C readable for people who will port or mod the game, without changing what
the compiler makes of it. Other agents may work on other functions at the same time. Repeat for up to {count}
functions, then stop:

1. claim_readable() gives you the next matched function that isn't readable yet, with its matching C.
2. Read the function, and get_function on the functions it calls, until you understand what it does.
3. {names}
4. make_readable(function, source): the same function with meaningful parameter and local names, the header's
   types and names, and one comment saying what it does. Keep the statements' shape (order, casts, temporaries):
   it's judged again and must still match byte for byte, or it's refused and the matching C stays. Refused? Undo
   your last change and try a smaller one.

Write C people would write. Be brief in your messages: work, don't narrate.
"""

READABLE_ROM = ("rename_symbol the function, and the globals and functions it uses, once their purpose is clear.\n"
                "   Describe structs, enums and constants in the shared header (get_context shows it) with\n"
                "   edit_context, and use those names.")
READABLE_FORK = ("rename_symbol the function, and the globals and functions it uses, once their purpose is clear\n"
                 "   (the project's config and every file change together). Describe structs, enums and constants in\n"
                 "   the project's headers with edit_file (read_file and search_code show where), and use those names.")

# FORK_PROMPT's {what}: a public decomp, or a project decompdecomp started from the user's copy. Told it was "a
# public matching-decompilation project", a model on a new DS project spent its first 7 minutes looking for the
# project's conventions in config/ and build.ninja; on an N64 game one went to fetch the finished public decomp
FORK_PUBLIC = "a public matching-decompilation project for {platform}"
FORK_SCRATCH = ("a decompilation of a {platform} game that decompdecomp started from the user's own copy. It's new: "
                "there's no upstream project, and no existing code or conventions to find beyond what agents wrote. "
                "The project's config/, build files and tools are the build's, not yours: each function's brief has "
                "what you need, so claim and compile right away")

FORK_PROMPT = """\
You are a decompilation agent in decompdecomp, continuing {title}: {what}. Other agents may be working on
other functions of the same project at the same time; the tools keep you from colliding.

Your job: write functions in the project's own source files so they compile to *exactly* the original
machine code, judged by objdiff. Use only the decomp tools. Do {count} claims you compiled (a batch is one; one you
released without compiling it doesn't count), then stop. Don't stop early because the functions look hard: what's
left is hard, and a claim that gets its full compile budget is how they match. Nobody answers during a session: never
stop to offer more claims "on request", just take them. (claim_function's count is something else: how many small
neighbours may come along in one claim; one function back is normal.)

1. claim_function(count=4) gives you the next function, and when it's small, its neighbours in the same
   file (small functions mostly match on the first try): target assembly, the current diff, its source
   file, the best previous attempt and notes (start from those!), functions already matched in that file,
   and identical matched code elsewhere if there is any.
2. The brief usually has what you need. When it doesn't: read_file on the source file and its headers,
   search_code for the types, structs and helpers it uses. Reuse what exists; never redefine it.
   find_similar(function) finds look-alike functions: start from a matched one.
3. compile_and_diff(function, source) with the complete definition (plus any #include "...", prototypes
   or #pragma lines it needs); several at once: function="a, b, c" with all definitions in source. Each
   goes into the source file in place of the old one, the file is built with the project's compiler and
   diffed. Fix structure first (control flow, calls), then types and expressions, registers last.
4. 100% = matched, committed to git. Helper functions, structs, shared prototypes and globals you need go
   in with edit_file (a header or the top of the source file), never inside compile_and_diff's source
   (it refuses them); an edit that breaks any match is undone. Found a
   project convention every agent should know? share_knowledge(text): it reaches them once a model other
   than yours agrees.
5. The goal is linked files: the game built from the C, byte for byte. When a match finishes a file's
   functions, you're shown its data next to the original's: fix what differs (order, const, statics,
   values) with edit_file until file_status says it matches; decompdecomp then links the file.
6. compile_and_diff says when to move on ({budget}): then
   release_function with precise notes (what you tried, what is left) and claim the next. Before that,
   compile the next step instead of releasing; sure that no C makes its code at all (hand-written
   assembly): not_c=true, saying why. Part of another function the analysis cut (no prologue, registers
   the code before set, a switch's cases in the next symbol)? join_functions(it, the other part, why), so it
   becomes one whole function. Very large functions: get the structure right first; a near miss
   is worth the extra compiles. Another function of the game it needs done first (a callee, an inlined
   helper): release_function(needs=that one) hands it to you.

Rules: C/C++ only (no inline asm, asm functions or asm includes); don't touch targets, configs or build
scripts (the tools refuse). Never fetch or copy code from outside this project (another repository, a
finished decomp of this game, the internet): a match must come from your own C. Write code a human would
have written: match the file's style and names.
Guides: {guides}. Be brief in your messages: work, don't narrate.
"""


def _console_guides() -> dict:
    """topic -> file of the guides consoles keep in their packages (consoles/<id>/guides/<topic>.md)."""
    return {path.stem: path for folder in consoles.guide_dirs() for path in sorted(folder.glob("*.md"))}


def guide_text(topic: str = "", platform: str | None = None) -> str:
    """A guide's text. With `platform` (a project's), the parts its file marks for other platforms are left out
    (for_platform)."""
    topic = topic.strip().lower()
    own = _console_guides()
    if topic in own:
        return for_platform(own[topic].read_text(encoding="utf-8"), platform)
    if topic not in GUIDES:
        # a console's guide is described by its title, the first line of its file
        about = GUIDES | {k: p.read_text(encoding="utf-8").split("\n", 1)[0].lstrip("# ") for k, p in own.items()}
        listing = "\n".join(f"- {k}: {v}" for k, v in about.items())
        return f"Guides (call guide with one of these topics):\n{listing}"
    return for_platform(resources.files("decompnet.app").joinpath("guide", f"{topic}.md").read_text(encoding="utf-8"),
                        platform)


# A guide's lines for some platforms only: `<!-- only: ppc mips -->` up to `<!-- end -->`; `<!-- only: !x86 -->` is
# every platform but those. Words are processor ids (arch/) or console ids. A PC helper's prompt (14 KB) carried
# GameCube's split_file, func_8001234 names and s32 types; a GameCube one would carry x86's conventions.
_ONLY = re.compile(r"^<!--\s*only:\s*([^>]*?)\s*-->\s*\n(.*?)^<!--\s*end\s*-->\s*\n", re.M | re.S)


def for_platform(text: str, platform: str | None) -> str:
    """`text` with its `only:` blocks kept for `platform` and dropped for others; None keeps every block (the
    markers themselves always go)."""
    con = consoles.get(platform) if platform else None
    ids = {con.id, con.arch} - {""} if con else set()

    def block(m: re.Match) -> str:
        if con is None:
            return m.group(2)
        words = m.group(1).split()
        yes = [w for w in words if not w.startswith("!")]
        no = {w[1:] for w in words if w.startswith("!")}
        keep = (not yes or bool(ids & set(yes))) and not (ids & no)
        return m.group(2) if keep else ""
    return _ONLY.sub(block, text)


def up_front(platform: str | None, kind: str, toolchain: str) -> str:
    """The know-how a helper gets before its first claim, for its project's platform: the loop's guide (a fork's
    and objdiff's, or ROM projects' workflow) and its console's compiler guide."""
    guide = consoles.get(platform).up_front_guide(toolchain)
    topics = ["fork", "objdiff"] if kind == "fork" else ["workflow"]
    topics += [guide] if guide else []
    return "\n\n".join(guide_text(t, platform) for t in topics)


def _fork(net: RomNetwork, slug: str, what: str) -> str:
    if not net.fork_slug(slug):
        raise NetworkError(f"{what} is for forks of public decomps; in a ROM project the shared header is get_context")
    return slug


def project_info(net: RomNetwork, slug: str) -> str:
    wish = net.instructions(slug)
    text = _project_info(net, slug)
    open_ = net.overview(slug).get("suggestions") or []
    if open_:  # so agents agree with one (suggest the same) rather than repeat it in other words
        text += "\nOpen suggestions to the user (suggest the same compiler and flags to agree):\n" + "\n".join(
            f"- {s['toolchain'] or '(setup)'} {' '.join(s['flags'])}"
            + "".join(f" 0x{a:X}-0x{b:X}" for a, b in s.get("ranges", [])) + f": {s['text'][:200]} [{len(s['agents'])} agree"
            + (f"; {s['check']}" if s["check"] else "") + "]" for s in open_)
    if not net.fork_slug(slug) and (known := net.knowledge.for_prompt(slug)):
        text += "\n" + known + "\n"
    return text + (f"\nThe user's instructions for this project (they come first):\n{wish}" if wish else "")


def _project_info(net: RomNetwork, slug: str) -> str:
    if net.fork_slug(slug):
        return net.forks.info(slug)
    ov = net.overview(slug)
    pct = 100 * ov["matched_bytes"] / (ov["bytes"] or 1)
    ranges = "".join(f"\n  0x{r['start']:X}-0x{r['end']:X}: {r['name']} {' '.join(r['flags'])}" for r in ov["compiler_ranges"])
    return (f"{ov['title']} ({ov['rom']['game_code']}, {ov['rom']['region']})\n"
            f"compiler: {ov['compiler']['name']} {' '.join(ov['compiler']['flags'])}"
            + (f"; these address ranges are built with another:{ranges}" if ranges else "") + "\n"
            + (f"this platform's compilers (for try_compiler): {', '.join(ov['compilers'])}\n" if len(ov["compilers"]) > 1 else "")
            + f"progress: {ov['matched']}/{ov['functions']} functions matched ({pct:.1f}% of code), "
            f"{ov['attempts']} compiles so far, context.h v{ov['context_version']}\n"
            f"Next: claim_function(). Guides: guide().")


# name -> handler(net, agent, slug, **args); every handler returns text for the model
def _claim(net: RomNetwork, agent: Agent, slug: str, function: str, strategy: str, count: int = 1) -> str:
    """A claim; one asked for that's matched, set aside (no C makes it) or someone else's is the next free function
    instead (an agent asking for those has nothing better to do than the next one, and is told why). count > 1: a
    batch of small ones."""
    strategy = strategy or ("finish" if net.fork_slug(slug) else "easiest")
    if not function and int(count or 1) > 1:
        return net.claim_many(agent, slug, int(count), strategy)
    try:
        return net.claim(agent, slug, function or None, strategy)
    except NetworkError as e:
        if not function or not re.search(r"already matched|is set aside|is claimed by", str(e)):
            raise
        return f"({e}. Here's the next free function instead.)\n\n" + net.claim(agent, slug, None, strategy)


def _compile(net: RomNetwork, agent: Agent, slug: str, function: str, source: str) -> str:
    """compile_and_diff; several names ("a, b, c") judge several definitions sent together."""
    names = [n.strip() for n in function.split(",") if n.strip()]
    if len(names) <= 1:
        return net.compile_and_diff(agent, names[0] if names else function, source, slug)
    return net.attempt_many(agent, [net.resolve_function(agent, slug, n) for n in names], source, slug)


def _add_note(net: RomNetwork, agent: Agent, slug: str, text: str, function: str = "") -> str:
    """A note for the next agent on one function. #25: a note without one used to become a project-wide knowledge
    proposal, which read like agreed knowledge although one model had only guessed it; that's share_knowledge's
    job when an agent means it."""
    if not function.strip():
        raise NetworkError("add_note needs the function the note is for; something every agent on this game should "
                           "know goes through share_knowledge")
    return net.add_note(agent, slug, text, function)


HANDLERS: dict[str, Callable[..., str]] = {
    "project_info": lambda net, agent, slug: project_info(net, slug),
    "claim_function": lambda net, agent, slug, function="", strategy="", count=1:
        _claim(net, agent, slug, function, strategy, count),
    "get_function": lambda net, agent, slug, function: net.get_function(function, slug, agent),
    "compile_and_diff": lambda net, agent, slug, function, source: _compile(net, agent, slug, function, source),
    "last_diff": lambda net, agent, slug, function, context=2: net.last_diff(agent, function, slug, context),
    "release_function": lambda net, agent, slug, function, notes, needs="", not_c=False:
        net.release_function(agent, function, notes, slug, needs or "", bool(not_c)),
    "get_context": lambda net, agent, slug, function="": net.get_context(slug, function or None),
    "edit_context": lambda net, agent, slug, old, new, message, function="":
        net.edit_context(agent, slug, old, new, message, function or None),
    "rename_symbol": lambda net, agent, slug, old, new, message="": net.rename_symbol(agent, slug, old, new, message),
    "split_function": lambda net, agent, slug, function, at, new_name="":
        net.split_function(agent, slug, function, at, new_name),
    "report_boundary": lambda net, agent, slug, function, end, reason:
        net.request_boundary(agent, slug, function, end, reason),
    "read_data": lambda net, agent, slug, what, length=64: (net.forks.read_data(agent, slug, what, length)
                                                            if net.fork_slug(slug) else net.read_data(slug, what, length)),
    "get_class": lambda net, agent, slug, what="": net.get_class(slug, what),
    "guess_struct": lambda net, agent, slug, function, argument="arg0": net.guess_struct(slug, function, argument),
    "redraft": lambda net, agent, slug, function, context: net.redraft(slug, function, context),
    "compiler_map": lambda net, agent, slug, survey=False, check_ranges=False:
        net.compiler_map_text(slug, bool(survey), bool(check_ranges)),
    "list_functions": lambda net, agent, slug, status="open", limit=40: net.list_functions(slug, status, limit),
    "add_note": lambda net, agent, slug, text, function="": _add_note(net, agent, slug, text, function),
    "share_knowledge": lambda net, agent, slug, text="", supports=0: net.knowledge.share(agent, slug, text, supports),
    "search_flags": lambda net, agent, slug, function, compiler="", source="":
        net.search_flags(agent, slug, function, compiler, source),
    "claim_readable": lambda net, agent, slug: net.readability.claim(agent, slug),
    "make_readable": lambda net, agent, slug, function, source: net.readability.submit(agent, slug, function, source),
    "suggest": lambda net, agent, slug, text, compiler="", flags="", function="", ranges="", source="":
        net.suggest(agent, slug, text, compiler, flags, function or None, ranges, source),
    "try_compiler": lambda net, agent, slug, function, compiler, flags="", source="":
        net.try_compiler(agent, slug, function, compiler, flags, source),
    "search_notes": lambda net, agent, slug, query: net.search_notes(slug, query),
    "find_similar": lambda net, agent, slug, function, limit=5: net.find_similar(slug, function, limit),
    "guide": lambda net, agent, slug, topic="": guide_text(topic, net.overview(slug).get("platform")),
    "read_file": lambda net, agent, slug, path, start=1, count=200:
        net.forks.read_file(_fork(net, slug, "read_file"), path, start, count),
    "search_code": lambda net, agent, slug, pattern, glob="": net.forks.search(_fork(net, slug, "search_code"), pattern, glob),
    "edit_file": lambda net, agent, slug, path, old, new, message="":
        net.forks.edit_file(agent, _fork(net, slug, "edit_file"), path, old, new, message),
    "file_status": lambda net, agent, slug, path: net.forks.file_status(_fork(net, slug, "file_status"), path),
    "split_file": lambda net, agent, slug, path, at, new_name="", data="":
        net.forks.split_file(agent, _fork(net, slug, "split_file"), path, at, new_name, data),
    "join_functions": lambda net, agent, slug, function, with_function, reason:
        net.forks.propose_join(agent, _fork(net, slug, "join_functions"), function, with_function, reason),
}

ROM_ONLY = {"get_context", "edit_context", "split_function", "report_boundary", "get_class",
            "guess_struct", "redraft",
            "compiler_map", "search_flags"}
FORK_ONLY = {"read_file", "search_code", "edit_file", "file_status", "split_file", "join_functions"}


def tool_names(fork: bool) -> list[str]:
    """The tools that make sense for a project: forks and ROM projects share the core loop."""
    skip = ROM_ONLY if fork else FORK_ONLY
    return [n for n in HANDLERS if n not in skip]


def _named(net: RomNetwork, agent: Agent, slug: str, args: dict) -> dict:
    """Function names as agents write them (a plain symbol that repeats across files, symbol@unit, Class::method)
    turned into the stored name, preferring the function the agent holds."""
    function = args.get("function")
    if isinstance(function, str) and not function.strip():
        args = {**args, "function": ""}  # a blank name is no name: add_note says so, the other tools get none
    elif function and "," not in function:  # (several are resolved one by one, see _compile)
        args = {**args, "function": net.resolve_function(agent, slug, function)}
    return args


def _invoke(net: RomNetwork, agent: Agent, slug: str, name: str, args: dict) -> str:
    """Every helper sees pulled changes before acting, and its completed work is on disk before it gets an answer."""
    net.progress.sync(slug, force=True)
    result = HANDLERS[name](net, agent, slug, **_named(net, agent, slug, args))
    net.progress.sync(slug, force=True)
    return result


def call(net: RomNetwork, agent: Agent, slug: str, name: str, args: dict) -> str:
    """Run one tool for the built-in agent; errors come back as text the model can act on."""
    if name not in HANDLERS:
        return f"error: unknown tool {name!r}"
    try:
        return _invoke(net, agent, slug, name, args)
    except NetworkError as e:
        return f"error: {e}"
    except TypeError as e:
        return f"error: bad arguments for {name}: {e}"


# What agents read on each tool's definition (Agent.tool_meta): Antigravity CLI otherwise finds MCP tools on demand,
# an extra step each, and decompdecomp's tools are all there is to the job, so they are loaded up front.
ALWAYS_LOAD = agents.TOOL_META


def build_mcp(net: RomNetwork, agent_of: Callable[[Context], tuple[Agent, str]]) -> MCPServer:
    """MCP server for CLI agents. `agent_of` maps a request to (agent, project slug)."""
    mcp = MCPServer(name="decomp", title="decompdecomp", instructions=INSTRUCTIONS, version=__version__)

    async def run(ctx: Context, name: str, **args) -> str:
        try:
            agent, slug = agent_of(ctx)
        except NetworkError as e:
            raise ToolError(str(e)) from None
        try:
            return await anyio.to_thread.run_sync(lambda: _invoke(net, agent, slug, name, args))
        except NetworkError as e:
            raise ToolError(str(e)) from None

    @mcp.tool(meta=ALWAYS_LOAD)
    async def project_info(ctx: Context) -> str:
        """Which game you're working on, its compiler, and progress so far."""
        return await run(ctx, "project_info")

    @mcp.tool(meta=ALWAYS_LOAD)
    async def claim_function(ctx: Context, function: str = "", strategy: str = "", count: int = 1) -> str:
        """Claim the next function to decompile (or a specific one by name or address).

        Returns its assembly, the best previous attempt and notes from earlier agents, plus (ROM projects)
        calls, data and a machine-made draft or (forks) the current diff and its source file. Each compile
        renews the claim.

        Args:
            function: optional name or address (0x80001234) of a specific function
            strategy: finish (forks' default: the files closest to being linked first), easiest (fewest
                instructions; ROM projects' default), closest (nearly matched already), hardest, random
            count: up to 6: small functions come as a batch (neighbours in one file) to write together and
                judge in one compile_and_diff; a big one comes alone
        """
        return await run(ctx, "claim_function", function=function, strategy=strategy, count=count)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def get_function(ctx: Context, function: str) -> str:
        """Show any function (by name or address): assembly, status, best attempt, notes. Read matched
        functions to learn how this codebase is written."""
        return await run(ctx, "get_function", function=function)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def compile_and_diff(ctx: Context, function: str, source: str) -> str:
        """Compile your code for `function` and diff it against the original; score 0 = byte-identical.

        ROM projects: compiled with the game's compiler (context.h included), linked at the function's
        address and compared with the ROM. Forks: send the complete definition (plus any #include "...",
        prototypes, externs or #pragma lines it needs); it replaces the function in its source file, which is
        built with the project's build system and diffed with objdiff. Several functions ("a, b, c"): send all
        their definitions in one source; each is judged on its own."""
        return await run(ctx, "compile_and_diff", function=function, source=source)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def last_diff(ctx: Context, function: str, context: int = 2) -> str:
        """Every differing row of your last compile_and_diff of `function`, where its result was cut ("more rows
        not shown"). Costs no compile and counts for nothing. `context`: equal rows shown around each difference."""
        return await run(ctx, "last_diff", function=function, context=context)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def release_function(ctx: Context, function: str, notes: str, needs: str = "", not_c: bool = False) -> str:
        """Give up a function you're stuck on, once compile_and_diff says to move on. Notes are required: what you
        tried, what is left. Your best attempt is kept for the next agent. Released before that (fewer compiles than
        the move-on rule), it answers once with what to try instead; call it again to release anyway.

        Args:
            function: the function you hold
            notes: what you tried, what is left
            needs: optional: another function of the game this one needs done first (a callee whose convention or
                body it depends on, a helper the original inlines). This one then waits, that one goes first in the
                queue, and you get it when it's free.
            not_c: true when you're sure no C makes its code at all (hand-written assembly), saying why in notes:
                once agents of two models agree, it's set aside and no one is handed it again.
        """
        return await run(ctx, "release_function", function=function, notes=notes, needs=needs, not_c=not_c)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def get_context(ctx: Context, function: str = "") -> str:
        """The shared header (types, structs, prototypes) included before every compile."""
        return await run(ctx, "get_context", function=function)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def edit_context(ctx: Context, old: str, new: str, message: str, function: str = "") -> str:
        """Edit the shared header: replace `old` (must occur exactly once) with `new`, or append `new` when
        old is "". Accepted only if every matched function still matches. Add prototypes and structs here."""
        return await run(ctx, "edit_context", old=old, new=new, message=message, function=function)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def rename_symbol(ctx: Context, old: str, new: str, message: str = "") -> str:
        """Give a function or data address a real name (e.g. func_80001234 -> player_update). Everything that
        uses the name changes with it (ROM: the assembly, matched sources, the header; a project: the game's
        symbols in its config and every source and header file); never breaks a match."""
        return await run(ctx, "rename_symbol", old=old, new=new, message=message)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def split_function(ctx: Context, function: str, at: str, new_name: str = "") -> str:
        """Split a function the disassembler merged with the next one (e.g. after a function that never
        returns). `at` is the address where the second function starts."""
        return await run(ctx, "split_function", function=function, at=at, new_name=new_name)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def report_boundary(ctx: Context, function: str, end: str, reason: str) -> str:
        """Report that a function really ends earlier: the words after `end` are alignment padding, linker
        leftovers or data, not code anyone wrote, so no C can match the whole thing. The user approves it on the
        project page; then the function ends at `end` and the rest is set aside. Say how you can tell."""
        return await run(ctx, "report_boundary", function=function, end=end, reason=reason)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def read_data(ctx: Context, what: str, length: int = 64) -> str:
        """Hex dump of the original's data: an address or symbol (ROM projects), a data symbol such as
        lbl_1_data_10 (forks); text, pointers and floats decoded."""
        return await run(ctx, "read_data", what=what, length=length)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def redraft(ctx: Context, function: str, context: str) -> str:
        """m2c's machine draft of a function made again with your declarations (a struct, a prototype, an extern
        you worked out) added to the shared header's; yours replace the header's where they declare the same
        names. Returns the new draft, recorded nowhere: try it with compile_and_diff, and edit_context what helped."""
        return await run(ctx, "redraft", function=function, context=context)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def get_class(ctx: Context, what: str = "") -> str:
        """C++ classes found in the game's code (ROM projects): give a vtable address or a virtual function,
        constructor or destructor for its class's fields (offset, type, which functions use them) and virtual
        functions by slot; nothing lists them all. A layout to write the class from: the judge still decides."""
        return await run(ctx, "get_class", what=what)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def guess_struct(ctx: Context, function: str, argument: str = "arg0") -> str:
        """The likely struct behind a function's pointer (ROM projects, MIPS), as C with offsets: from the loads and
        stores of every function the same pointer reaches, with how many agree on each field, where else the type
        flows, and the type matched code already uses for it. argument: arg0-arg3, return, or a global; add +0x14
        to follow the pointer at 0x14. The game's own type when its debug information has one. Put what you confirm
        into context.h with edit_context."""
        return await run(ctx, "guess_struct", function=function, argument=argument)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def compiler_map(ctx: Context, survey: bool = False, check_ranges: bool = False) -> str:
        """Which compiler built each part of the game (ROM projects, MIPS): where the code looks built by another
        compiler than the one its address builds with today, from matches, habits that rule compilers out, and the
        judge. survey=true also starts the judge's survey in the background: agents' C for unmatched functions built
        with each compiler the project uses; what it confirms becomes a suggestion. A suggested range is tested on
        its neighbours before it applies (range checks, listed here); check_ranges=true looks at the applied ranges
        again the same way (a report: nothing changes by itself)."""
        return await run(ctx, "compiler_map", survey=survey, check_ranges=check_ranges)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def list_functions(ctx: Context, status: str = "open", limit: int = 40) -> str:
        """List functions by status: open, matched or all."""
        return await run(ctx, "list_functions", status=status, limit=limit)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def add_note(ctx: Context, text: str, function: str) -> str:
        """Leave a note on a function for the next agent on it. Say which function it's for: something every agent
        on this game should know goes through share_knowledge."""
        return await run(ctx, "add_note", text=text, function=function)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def search_flags(ctx: Context, function: str, compiler: str = "", source: str = "") -> str:
        """The function's C (its best attempt, or source) built with each of its compiler's flag variants
        (optimisation levels, small data, code-shape options), judged and ranked. For C that looks right but won't
        match: the flags may be what's wrong. compiler: another one to try instead of the project's."""
        return await run(ctx, "search_flags", function=function, compiler=compiler, source=source)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def claim_readable(ctx: Context) -> str:
        """Take the next matched function that isn't readable yet: rename, type and comment it so people understand
        it, while it still matches byte for byte."""
        return await run(ctx, "claim_readable")

    @mcp.tool(meta=ALWAYS_LOAD)
    async def make_readable(ctx: Context, function: str, source: str) -> str:
        """A readable rewrite of a matched function (names, types, a comment). Kept only if it still matches byte
        for byte; otherwise the matching C stays."""
        return await run(ctx, "make_readable", function=function, source=source)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def share_knowledge(ctx: Context, text: str = "", supports: int = 0) -> str:
        """Propose something every agent on this game should know (a convention, what a struct is, how the code
        is organised), or back an open proposal with supports=its number. It reaches every agent once helpers
        running two different models agree. Not for which compiler built what: that's the compiler map's job."""
        return await run(ctx, "share_knowledge", text=text, supports=supports)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def suggest(ctx: Context, text: str, compiler: str = "", flags: str = "", function: str = "",
                      ranges: str = "", source: str = "") -> str:
        """Suggest a change to the project's setup you can't make yourself, for the user to apply: above all a
        different compiler or flags, when C that is right can't match (the code's style says another compiler built
        it). Test it with try_compiler first: a suggestion whose function doesn't match with it is refused. Say what
        you saw. project_info lists open suggestions: suggesting the same compiler and flags is a vote for it; with
        the user's permission, a proven or agreed one is applied at once.

        Args:
            text: what should change and the evidence
            compiler: the compiler you think built the game (a toolchain id like ee-gcc-2.95.3)
            flags: its flags, if not the defaults
            function: a function whose best attempt should match with that compiler: it's checked right away
            ranges: when only parts of the game were built with it: "0x10BFD0-0x10CD40, 0x1106C0-0x1107C0"
            source: the C to check it with (default: the function's best attempt)
        """
        return await run(ctx, "suggest", text=text, compiler=compiler, flags=flags, function=function, ranges=ranges,
                         source=source)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def try_compiler(ctx: Context, function: str, compiler: str, flags: str = "", source: str = "") -> str:
        """Compile a function's C with another compiler and flags and see the diff, recording nothing: to find out
        which compiler built a part of the game before you suggest it. Cheap: try several.

        Args:
            function: the function to judge it against
            compiler: a toolchain id (project_info lists this platform's); where each source file has its own
                compiler (DS), "all" tries every one, and the one that matches becomes the file's
            flags: its flags (default: that compiler's defaults for this game)
            source: the C (default: the function's best attempt)
        """
        return await run(ctx, "try_compiler", function=function, compiler=compiler, flags=flags, source=source)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def search_notes(ctx: Context, query: str) -> str:
        """Search notes left by agents."""
        return await run(ctx, "search_notes", query=query)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def find_similar(ctx: Context, function: str, limit: int = 5) -> str:
        """Functions whose instructions look like this one's (copy-pasted code, the same inlined helper,
        template instances), matched ones first, with the matching source of the best one. Start there."""
        return await run(ctx, "find_similar", function=function, limit=limit)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def guide(ctx: Context, topic: str = "") -> str:
        """Decompilation know-how. Call without a topic for the list (workflow, mips, gcc-kmc, ido, m2c,
        data, structs, n64, naming)."""
        return await run(ctx, "guide", topic=topic)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def read_file(ctx: Context, path: str = "", start: int = 1, count: int = 200, file_path: str = "",
                        offset: int = 0, limit: int = 0) -> str:
        """Forks only: read a file of the project (path relative to its root), with line numbers."""
        # file_path/offset/limit: the names of Claude Code's own Read tool, which agents passed here hundreds of
        # times a night, each a refused call
        if not (path := path or file_path):
            raise ValueError("read_file needs path: the file, relative to the project's root")
        return await run(ctx, "read_file", path=path, start=offset or start, count=limit or count)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def search_code(ctx: Context, pattern: str, glob: str = "") -> str:
        """Forks only: search the project's C/C++ sources and headers with a regular expression. `glob`
        narrows the files (e.g. "include/**/*.h")."""
        return await run(ctx, "search_code", pattern=pattern, glob=glob)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def edit_file(ctx: Context, path: str, old: str, new: str, message: str = "") -> str:
        """Forks only: change a C/C++ source or header file. Replace `old` (exactly once) with `new`, or
        append `new` when old is "". The project is rebuilt and measured; the edit is undone if any matched
        function stops matching. Use it for structs, prototypes, globals and helpers. `message`: what the
        change is, for its commit."""
        return await run(ctx, "edit_file", path=path, old=old, new=new, message=message)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def file_status(ctx: Context, path: str) -> str:
        """Forks only: how far a source file (or a function's file) is from being linked into the game: which
        functions still differ, its data next to the original's (with what to fix), why the last link failed.
        A file is linked (the game built from its C) once every function and all its data match."""
        return await run(ctx, "file_status", path=path)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def split_file(ctx: Context, path: str, at: str, new_name: str = "", data: str = "") -> str:
        """Forks (GameCube, Wii, DS): divide a source file in two where the game's original files divided (DS: or
        to give a function built by another compiler version a file, and compiler, of its own: split at it and at the
        one after it, then try_compiler): the
        function `at` (name or .text offset) and every one after it in the file go to a new file `new_name`
        (default: text_<offset> next to it), with their C. `data` moves the end of each data section too
        (".rodata:0x88 .data:0x28": where the new file's part starts; file_status shows the ranges). Kept only if
        the game still builds byte for byte and every match still matches. A file links only when its code and
        data are exactly one original file's."""
        return await run(ctx, "split_file", path=path, at=at, new_name=new_name, data=data)

    @mcp.tool(meta=ALWAYS_LOAD)
    async def join_functions(ctx: Context, function: str, with_function: str, reason: str) -> str:
        """Forks: `function` and `with_function` are one function the game's analysis cut in two (or more: the
        symbols between them too). A piece with no prologue that uses registers the code before it set, an epilogue
        alone, a switch whose cases are in the next symbol. Say what in the code shows it. Once agents of two models
        say so of the same two, they're joined, if the game still builds byte for byte; never a matched function."""
        return await run(ctx, "join_functions", function=function, with_function=with_function, reason=reason)

    @mcp.prompt(title="Decompile", description="Work loop: claim functions and match them byte for byte.")
    def work(count: str = "5") -> str:
        return "Call project_info first, then follow the decompdecomp work loop for up to " + count + " functions."

    return mcp
