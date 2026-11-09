"""The tools a decompiling agent gets, served over MCP to CLI agents and called directly by the
built-in API agent. Every tool works on the project the agent was started on."""

from __future__ import annotations

import re
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from importlib import resources
from typing import Callable, Literal

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__, agents, candidate_quality, consoles
from ..network import CLAIM_ORDERS, Agent, Network, NetworkError
from ..release_receipts import operation as release_operation
from . import (
    agent_tokens,
    asset_agents,
    boundary_investigations,
    claim_state,
    compiler_experiments,
    local_experiments,
    polish_automation,
    reviewed_search_seeds,
    search_brief,
)
from .engine import RomNetwork


class NextFamilyTarget(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode: Literal['next']


class AnchorFamilyTarget(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode: Literal['anchor']
    old: str
    occurrence: int = 0


class DeclarationFamilyTarget(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode: Literal['declaration']
    declaration: str


class CompilerTrial(BaseModel):
    model_config = ConfigDict(extra='forbid')
    compiler: str = Field(description='current or an exact compiler ID. Repair families belong in try_local_repairs, not this tool.')
    flags: str = ''
    old: str | None = None
    new: str = ''
    occurrence: int = 0


# The guides every console shares (app/guide/); each console's own are in its package (consoles/<id>/guides/)
TOOL_HELP = {}

GUIDES = {
    "asset-discovery": "advisory region claims, format investigations and evidence-based proposals",
    "agent-experiments": "repair families, candidate revisions, compiler trials and recovery",
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
Use app-provided startup facts and the already claimed function when present; otherwise project_info then claim_function. Compile the first claim before taking another. guide(topic) has know-how.
Everything runs on the user's computer. The game's code is the user's: never publish it anywhere.
"""

WORK_PROMPT = """\
You are a decompilation agent in decompdecomp, working on {title}. Other agents may be working on other
functions of the same game at the same time; the tools keep you from colliding.

Your job: turn functions of this game into C that compiles to *exactly* the original machine code.
Use only the decomp tools. Work on up to {count} compiled claims (a batch is one). Submit reusable matching discoveries with share_knowledge as soon as you have evidence; a second model is required for automatic acceptance, not for proposing. Continue with the next eligible function while session capacity remains, even after a difficult or unsuccessful claim. Difficulty, low expected yield, or a low effort setting alone is not a reason to end an unattended session. The claim count is a maximum, not a quota. Stop earlier when the session turn/context allowance is nearly used; leave precise notes for unfinished work. Do not apologise for missing the maximum. Nobody answers during a session: never stop to offer more claims "on
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
   Read guide("workflow") or guide("{compiler_guide}") when needed. find_similar(function) shows look-alike functions;
   a matched one is the best starting point.
4. Score 0 = matched and saved. Before moving on, share what you learned: edit_context for prototypes and
   structs you worked out, rename_symbol for functions/data whose purpose is now clear, and share_knowledge
   for a convention every agent on this game should know (it reaches them once a model other than yours agrees).
   Right C that can't match because the project's compiler or flags look wrong? search_flags(function) tries
   its compiler's flags at once, try_compiler(...) other compilers; then suggest(...) what matches to the user. The function runs on past its end into padding, linker
   leftovers or data? report_boundary(...) where it really ends, then work on as it is.
5. compile_and_diff says when to move on ({budget}): then
   release_function with precise notes (what you tried, what is left) and claim the next one. Before
   that, compile the next step instead of releasing. A direct callee blocks work?
   release_function(needs=that one) allows one pivot per unchanged input, respecting pauses. Sure that
   no C makes its code at all (hand-written assembly): not_c=true, saying why.
   A boundary-review warning comes first: inspect adjacent bytes and segment limits before candidate tuning.
   A target ending without a return is not an assembly verdict; report or correct its extent first.

Write plausible original code, pure C (no inline asm). Never fetch or copy code from outside this project
(another repository, a finished decomp of this game, the internet): a match must come from your own C. Be brief in your messages: work, don't narrate.
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
4. Prefer polish_function(function, renames='{{"arg0":"value"}}', comment="What it does") for local names and comments.
   renames/types are JSON objects. Existing header type aliases can be substituted with types. The app applies the
   plan without shifting physical lines, judges it, and learns verified patterns for identical helpers. reuse=true
   tests a suggested pattern. Shared names still use rename_symbol. For complex scopes or restructuring use
   make_readable(function, source): the same function with meaningful parameter and local names, the header's
   types and names, and one comment saying what it does. Keep the statements' shape (order, casts, temporaries):
   it's judged again and must still match byte for byte, or it's refused and the matching C stays. Refused? Undo
   your last change and try a smaller one.

Once every function in a source file is readable, claim_readable may give you the file to tidy instead (TIDY A
FILE): duplicate declarations/includes have already had a guarded cleanup. Follow its list with edit_file and
rename_symbol, use tidy_file(path) for another mechanical cleanup, then tidy_done. A file counts as one of your {count}.

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
machine code, judged by objdiff. Use only the decomp tools. Work on up to {count} compiled claims (a batch is one). Submit reusable matching discoveries with share_knowledge as soon as you have evidence; a second model is required for automatic acceptance, not for proposing. Continue with the next eligible function while session capacity remains, even after a difficult or unsuccessful claim. Difficulty, low expected yield, or a low effort setting alone is not a reason to end an unattended session. The claim count is a maximum, not a quota. Stop earlier when the session turn/context allowance is nearly used; leave precise notes for unfinished work. Do not apologise for missing the maximum. Nobody answers during a session: never
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
   is worth the extra compiles. A direct callee blocks work?
   release_function(needs=that one) allows one pivot per unchanged input, respecting pauses.

Rules: C/C++ only (no inline asm, asm functions or asm includes); don't touch targets, configs or build
scripts (the tools refuse). Never fetch or copy code from outside this project (another repository, a
finished decomp of this game, the internet): a match must come from your own C. Write code a human would
have written: match the file's style and names.
Guides: {guides}. Be brief in your messages: work, don't narrate.
"""


REPAIR_GUIDANCE = """\
When a candidate makes a specific repair, optionally include a one-sentence repair_note, repair_category and
repair_automation in compile_and_diff (single function only). These are saved only on a verified new match.
Explain what changed and how a deterministic repair could do it; omit uncertain explanations. No extra compile
is needed. report_repair can annotate your captured match afterwards. These are review ideas, never live rules.
"""
WORK_PROMPT += "\n" + REPAIR_GUIDANCE + 'Prefer compile_patch for small edits and compile_and_diff for substantial rewrites. Keep the returned candidate revision. Do not fetch get_candidate after a successful patch unless the source is lost or stale. Same-function edits must be sequential. When handing off a precise untested edit, release_function with next_experiment fields revision, old, new, title saves a validated exact-anchor proposal. Retrieve it with get_function(section="experiment"); it remains untested until the normal judge runs. compile_variants runs up to six independent supplied alternatives against one revision and returns the chosen continuation; keep the baseline on ties. Automatic repairs are explicit via try_local_repairs, never a substitute for submitted variants. A saved historical score can differ from the current verified baseline; compare current trials to the rechecked baseline. Release with precise function notes when the move-on budget is exhausted, then take eligible work. Global share_knowledge is only for reusable game/compiler matching tips; function-specific findings belong in release notes. Optional tools may be disabled: use the available schema. guide(topic="agent-experiments") explains repair families, selectors, recovery, compiler trials and paging. Detailed tool help is guide(topic="tool:NAME").'
FORK_PROMPT += "\n" + REPAIR_GUIDANCE + 'Prefer compile_patch for small edits and compile_and_diff for substantial rewrites. Keep the returned candidate revision. When handing off a precise untested edit, release_function with next_experiment fields revision, old, new, title saves an exact-anchor proposal. Retrieve it with get_function(section="experiment") and test through the normal judge. Do not fetch get_candidate after a successful patch unless the source is lost or stale. Same-function edits must be sequential. When handing off a precise untested edit, release_function with next_experiment fields revision, old, new, title saves a validated exact-anchor proposal. Retrieve it with get_function(section="experiment"); it remains untested until the normal judge runs. compile_variants runs up to six independent supplied alternatives against one revision and returns the chosen continuation; keep the baseline on ties. Automatic repairs are explicit via try_local_repairs, never a substitute for submitted variants. A saved historical score can differ from the current verified baseline; compare current trials to the rechecked baseline. Release with precise function notes when the move-on budget is exhausted, then take eligible work. Global share_knowledge is only for reusable game/compiler matching tips; function-specific findings belong in release notes. Optional tools may be disabled: use the available schema. guide(topic="agent-experiments") explains repair families, selectors, recovery, compiler trials and paging. Detailed tool help is guide(topic="tool:NAME").'


def _console_guides() -> dict:
    """topic -> file of the guides consoles keep in their packages (consoles/<id>/guides/<topic>.md)."""
    return {path.stem: path for folder in consoles.guide_dirs() for path in sorted(folder.glob("*.md"))}


def guide_text(topic: str = "", platform: str | None = None) -> str:
    """A guide's text. With `platform` (a project's), the parts its file marks for other platforms are left out
    (for_platform)."""
    topic = topic.strip().lower()
    own = _console_guides()
    if topic.startswith('tool:'):
        name = topic[5:]
        return TOOL_HELP.get(name, "No extended tool help loaded; use the tool's schema and guide() for the work loop.")
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
    return "Detailed guides available on demand: " + ", ".join(f'guide(topic="{t}")' for t in topics)


def _fork(net: RomNetwork, slug: str, what: str) -> str:
    if not net.fork_slug(slug):
        raise NetworkError(f"{what} is for forks of public decomps; in a ROM project the shared header is get_context")
    return slug


def project_info(net: RomNetwork, slug: str, detail: bool = False) -> str:
    wish = net.instructions(slug)
    if not detail:
        order = net.claim_order(slug)
        steering = f"\nThe project's owner chose the order helpers take functions in: {CLAIM_ORDERS[order]}." if order else ''
        return _compact_project_info(net,slug) + steering + (f"\nThe user's instructions for this project (they come first):\n{wish}" if wish else "")
    text = _project_info(net, slug, detail)
    open_ = net.overview(slug).get("suggestions") or []
    if open_ and not detail:
        text += f'\nOpen suggestions: {len(open_)} compiler/setup suggestions; project_info(detail=true) lists them.'
    if open_ and detail:  # so agents agree with one (suggest the same) rather than repeat it in other words
        text += "\nOpen suggestions to the user (suggest the same compiler and flags to agree):\n" + "\n".join(
            f"- {s['toolchain'] or '(setup)'} {' '.join(s['flags'])}"
            + "".join(f" 0x{a:X}-0x{b:X}" for a, b in s.get("ranges", [])) + f": {s['text'][:200]} [{len(s['agents'])} agree"
            + (f"; {s['check']}" if s["check"] else "") + "]" for s in open_)
    if not net.fork_slug(slug) and (known := net.knowledge.for_prompt(slug)):
        text += "\n" + known + "\n"
    if not detail and len(text) > 1800:
        text = text[:1800] + "\n… project_info(detail=true) shows the full project facts and suggestions.\n"
    if (order := net.claim_order(slug)):
        text += (f"\nThe project's owner chose the order helpers take functions in: {CLAIM_ORDERS[order]}. "
                 "claim_function without a function follows it; name a function to get that one.\n")
    return text + (f"\nThe user's instructions for this project (they come first):\n{wish}" if wish else "")


def _compact_project_info(net, slug):
    """Indexed totals and saved metadata only: no overview, SDK, room or build scans."""
    proj=net._project(slug)
    cfg=net.cfg(proj)
    totals=net.project_store(slug).one('SELECT total,matched,attempts FROM project_totals WHERE project_id=?',proj['id'])
    profile=cfg.get('compiler') or {}
    platform=consoles.get(cfg.get('platform'))
    compiler=profile.get('toolchain') or 'set by the project build'
    known=', '.join(c.id for c in platform.compilers)
    return (f"{proj['name']} ({slug})\ncompiler: {compiler} {' '.join(profile.get('flags',[]))}\n"
            + (f"this platform's compilers (for try_compiler): {known}\n" if known else '')
            + (f"progress: {totals['matched']}/{totals['total']} functions matched; {totals['attempts']} compiles\n" if totals else 'Progress totals unavailable; no project scan performed.\n')
            + 'Compact saved metadata; each claim uses its own effective compiler profile. '
              'project_info(detail=true) loads full facts, knowledge and suggestions. Next: get_claims() to recover held work, or claim_function().')


def _project_info(net: RomNetwork, slug: str, detail: bool = True) -> str:
    if not detail:
        return _compact_project_info(net,slug)
    if net.fork_slug(slug):
        return net.forks.info(slug)
    ov = net.overview(slug)
    pct = 100 * ov["matched_bytes"] / (ov["bytes"] or 1)
    ranges = "".join(f"\n  0x{r['start']:X}-0x{r['end']:X}: {r['name']} {' '.join(r['flags'])}" for r in ov["compiler_ranges"]) if detail else ""
    return (f"{ov['title']} ({ov['rom']['game_code']}, {ov['rom']['region']})\n"
            f"compiler: {ov['compiler']['name']} {' '.join(ov['compiler']['flags'])}"
            + (f"; these address ranges are built with another:{ranges}" if ranges else
               f"; {len(ov['compiler_ranges'])} address-range overrides (each claim shows its applicable compiler)" if ov["compiler_ranges"] else "") + "\n"
            + (f"this platform's compilers (for try_compiler): {', '.join(ov['compilers'])}\n" if len(ov["compilers"]) > 1 else "")
            + f"progress: {ov['matched']}/{ov['functions']} functions matched ({pct:.1f}% of code), "
            f"{ov['attempts']} compiles so far, context.h v{ov['context_version']}\n"
            f"Next: claim_function(). Guides: guide().")


# name -> handler(net, agent, slug, **args); every handler returns text for the model
@release_operation("tool", protect_new=True)
def _release_with_experiment(net, agent, slug, function, notes, needs, not_c, move_on, next_experiment, claim_token=""):
    from . import agent_review
    saved = agent_review.save_experiment(net,agent,slug,function,next_experiment) if next_experiment is not None else ''
    return agent_tokens.brief(net,net.release_function(agent,function,notes,slug,needs or '',bool(not_c),bool(move_on),claim_token=claim_token),slug,agent) + ('\n'+saved if saved else '')


@claim_state.quick
def _claim(net: RomNetwork, agent: Agent, slug: str, function: str, strategy: str, count: int = 1) -> str:
    """A claim; one asked for that's matched, set aside (no C makes it) or someone else's is the next free function
    instead (an agent asking for those has nothing better to do than the next one, and is told why). count > 1: a
    batch of small ones."""
    from . import work_availability
    focused=agent.id in getattr(net,'_agent_focus',{})
    if not focused and not function and (waiting := work_availability.cached(net,agent,slug)):
        return waiting
    def packet(text):
        return work_availability.observe(net,agent,slug,text)
    if focused:
        function=net.focus_target(agent,slug,function)
        count=1
    strategy = strategy or ("finish" if net.fork_slug(slug) else "easiest")
    if not function and int(count or 1) > 1:
        return packet(agent_tokens.claim_packet(net, agent, slug, net.claim_many(agent, slug, int(count), strategy)))
    try:
        return packet(agent_tokens.claim_packet(net, agent, slug, net.claim(agent, slug, function or None, strategy)))
    except NetworkError as e:
        if focused or not function or not re.search(r"already matched|is set aside|is claimed by|outside this agent|Max tries reached", str(e)):
            raise
        return packet(str(e) + '\n\n' + agent_tokens.brief(net, net.claim(agent, slug, None, strategy), slug, agent))


def _compile(net: RomNetwork, agent: Agent, slug: str, function: str, source: str,
             repair_note: str = '', repair_category: str = 'other', repair_automation: str = '') -> str:
    """Optional repair notes ride along with a candidate, saving a separate explanation round trip."""
    from ..repair_feedback import CATEGORIES
    names = [n.strip() for n in function.split(",") if n.strip()]
    if repair_note and repair_category not in CATEGORIES:
        repair_note=f'[Original category: {repair_category}] '+repair_note
        repair_category='other'
    if repair_note and len(names) != 1:
        raise NetworkError('Repair notes require one function and a valid category. Use report_repair after a batch.')
    previous = net.project_store(slug).one('SELECT MAX(id) AS id FROM repair_examples')['id'] or 0
    previous_diff = None
    previous_attempt = None
    if len(names) == 1:
        fn = net._function(names[0], slug)
        previous_attempt = net.project_store(slug).one('SELECT id,source FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1', fn['id'], agent.id)
        kept = net._last_diffs.get((agent.id, fn['id']))
        previous_diff = list(kept[0]) if kept else None
    if len(names) <= 1:
        result = net.compile_and_diff(agent, names[0] if names else function, source, slug)
    else:
        result = net.attempt_many(agent, [net.resolve_function(agent, slug, n) for n in names], source, slug)
    if len(names) == 1:
        agent_tokens.outcome(net, agent, fn, source, result)
        from . import agent_evidence
        unchanged_note=agent_evidence.track(net,agent,slug,fn)
        agent_tokens.remember(net, agent, fn, previous_attempt)
        result = agent_tokens.feedback(net, agent, slug, names[0], result, previous_diff)
        if unchanged_note:
            result += '\n'+unchanged_note
        _, revision = agent_tokens.candidate(net, agent, slug, names[0])
        open_function = net._function(names[0],slug)['status']=='open'
        held = net.project_store(slug).one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
        if open_function and held:
            state = net.stall_status(fn['id'],agent.id)
            remaining = max(0,state['after']-state['no_better'])
            result += f"\nBudget: {remaining}/{state['after']} without improvement remaining; Candidate revision {revision}."
            limit = net.attempt_limit_note(agent,names[0],slug)
            if limit:
                result += limit
            elif not remaining:
                from ..release_receipts import instruction
                result += '\nNext: '+instruction(net,fn['id'],agent.id)+'. Then claim another function.'
        elif open_function:
            result += f"\nCandidate revision {revision}. Claim ended; claim another eligible function. Do not release this function again."
        else:
            result += f"\nCandidate revision {revision}."
        # Server conversion cannot recover tokens already sent. Coach once per claim when the exact
        # submission was a small edit; keep accepting full rewrites without a rejection round trip.
        if previous_attempt and previous_attempt['source'] and source!=previous_attempt['source']:
            before = previous_attempt['source']
            prefix = 0
            while prefix<min(len(before),len(source)) and before[prefix]==source[prefix]:
                prefix += 1
            suffix = 0
            while suffix<min(len(before),len(source))-prefix and before[-suffix-1]==source[-suffix-1]:
                suffix += 1
            changed=max(len(before),len(source))-prefix-suffix
            claim=net.project_store(slug).one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
            stamp=(agent.id,fn['id'],claim['claimed_at'] if claim else None)
            coached=getattr(net,'_patch_coached',set())
            if changed<=160 and len(source)>400 and stamp not in coached:
                if len(coached)>=128:
                    coached.clear()
                coached.add(stamp);net._patch_coached=coached
                result += '\nSmall edit: use compile_patch with revision, old and new next time; avoid resending the full C.'
    if repair_note:
        fn = net._function(names[0], slug)
        case = net.project_store(slug).one('SELECT e.id FROM repair_examples e JOIN attempts a ON a.id=e.attempt_id '
                             'WHERE e.function_id=? AND e.agent_id=? AND e.id>? AND a.source=? '
                             'ORDER BY e.id DESC LIMIT 1', fn['id'], agent.id, previous, source)
        if case:
            net.repair_feedback.report(agent, slug, names[0], repair_note, repair_category, repair_automation, case_id=case['id'])
            result += f"\nRepair example #{case['id']} and your explanation saved for review."
    return result


def _add_note(net: RomNetwork, agent: Agent, slug: str, text: str, function: str = "") -> str:
    """A note for the next agent on one function. #25: a note without one used to become a project-wide knowledge
    proposal, which read like agreed knowledge although one model had only guessed it; that's share_knowledge's
    job when an agent means it."""
    if not function.strip():
        raise NetworkError("add_note needs the function the note is for; something every agent on this game should "
                           "know goes through share_knowledge")
    return net.add_note(agent, slug, text, function)


def _redraft(net, slug, function, context, agent=None):
    if isinstance(context,str) and context.strip() and not re.search(r'[;{}#]',context):
        raise NetworkError('redraft context must be C declarations, not prose. Save observations with add_note; use get_function(section=candidate) to read existing C.')
    if not isinstance(context,str) or not context.strip():
        raise NetworkError('redraft needs nonempty C declarations in context (a real struct, callee prototype or extern). '
                           'To inspect existing C, use get_function(function, section="candidate"). No redraft was run.')
    return agent_tokens.brief(net,net.redraft(slug,function,context),slug,agent)


def _get_context(net, agent, slug, function=""):
    if not function:
        held = net.project_store(slug).all('SELECT f.name FROM functions f JOIN claims c ON c.function_id=f.id '
                             'JOIN projects p ON p.id=f.project_id WHERE c.agent_id=? AND p.slug=?', agent.id, slug)
        if len(held) == 1:
            function = held[0]['name']
    return net.get_context(slug, function or None)


def _report_data_region(net, agent, slug, function, start, end, reason):
    from .data_regions import report
    return report(net, agent, slug, function, start, end, reason)


def _resolve_addresses(net, agent, slug, queries, context_function=""):
    import json

    from .address_lookup import resolve
    return json.dumps(resolve(net,agent,slug,queries,context_function),ensure_ascii=False)


HANDLERS: dict[str, Callable[..., str]] = {
    "claim_asset_region": asset_agents.claim,
    "inspect_asset_region": asset_agents.inspect,
    "preview_asset_region": asset_agents.preview,
    "propose_asset_region": asset_agents.propose,
    "defer_asset_region": asset_agents.defer,
    "get_boundary_evidence": boundary_investigations.evidence,
    "suggest_boundary": boundary_investigations.report,
    "get_claims": claim_state.get_claims,
    "resolve_addresses": _resolve_addresses,
    "report_data_region": _report_data_region,
    "project_info": lambda net, agent, slug, detail=False: project_info(net, slug, detail),
    "get_candidate": agent_tokens.get_candidate,
    "compile_patch": agent_tokens.patch,
    "compile_variants": agent_tokens.variants,
    "compiler_experiments": compiler_experiments.run,
    "get_compiler_trial": compiler_experiments.retrieve,
    "review_search_seed": reviewed_search_seeds.review,
    "get_search_brief": search_brief.read,
    "try_local_repairs": lambda net,agent,slug,function,revision,families=None,max_trials=6:
        local_experiments.run(net,agent,slug,function,revision,families,max_trials),
    "compile_family": agent_tokens.local_family,
    "family_targets": agent_tokens.family_targets,
    "get_assembly": lambda net, agent, slug, function, start=0, limit=60, focus="": agent_tokens.assembly(net, slug, function, start, limit, focus, agent),
    "claim_function": lambda net, agent, slug, function="", strategy="", count=1:
        _claim(net, agent, slug, function, strategy, count),
    "get_function": agent_tokens.function_view,
    "compile_and_diff": lambda net, agent, slug, function, source, repair_note='', repair_category='other', repair_automation='':
        _compile(net, agent, slug, function, source, repair_note, repair_category, repair_automation),
    "report_repair": lambda net, agent, slug, function, change, category='other', automation='':
        net.repair_feedback.report(agent, slug, function, change, category, automation),
    "last_diff": agent_tokens.diff_page,
    "report_candidate_issue": lambda net, agent, slug, function, revision, issue, reason, alternative_revision="":
        candidate_quality.report(net,agent,slug,function,revision,issue,reason,alternative_revision),
    "release_function": lambda net, agent, slug, function, notes, needs="", not_c=False, move_on=False, next_experiment=None, claim_token="":
        _release_with_experiment(net,agent,slug,function,notes,needs,not_c,move_on,next_experiment,claim_token),
    "get_context": _get_context,
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
    "redraft": lambda net, agent, slug, function, context: _redraft(net, slug, function, context, agent),
    "compiler_map": lambda net, agent, slug, survey=False, check_ranges=False:
        net.compiler_map_text(slug, bool(survey), bool(check_ranges)),
    "list_functions": lambda net, agent, slug, status="open", limit=40: net.list_functions(slug, status, limit),
    "add_note": lambda net, agent, slug, text, function="": _add_note(net, agent, slug, text, function),
    "share_knowledge": lambda net, agent, slug, text="", supports=0, evidence="": net.knowledge.share(agent, slug, text, supports, evidence),
    "search_flags": lambda net, agent, slug, function, compiler="", source="":
        net.search_flags(agent, slug, function, compiler, source),
    "claim_readable": lambda net, agent, slug: net.readability.claim(agent, slug),
    "polish_function": lambda net, agent, slug, function, renames="{}", comment="", types="{}", reuse=False: polish_automation.structured(net.readability, agent, slug, function, renames, comment, types, reuse),
    "tidy_file": lambda net, agent, slug, path: polish_automation.tidy(net.readability, agent, slug, path),
    "make_readable": lambda net, agent, slug, function, source: net.readability.submit(agent, slug, function, source),
    "tidy_done": lambda net, agent, slug, unit, summary: net.readability.tidy_done(agent, slug, unit, summary),
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
    "standins_for": lambda net, agent, slug, what: net.forks.standins_for(agent, _fork(net, slug, "standins_for"), what),
    "split_file": lambda net, agent, slug, path, at, new_name="", data="":
        net.forks.split_file(agent, _fork(net, slug, "split_file"), path, at, new_name, data),
    "join_functions": lambda net, agent, slug, function, with_function, reason:
        net.forks.propose_join(agent, _fork(net, slug, "join_functions"), function, with_function, reason),
}

READ_ONLY = frozenset({"get_search_brief","get_boundary_evidence", "get_claims", "resolve_addresses", "project_info", "get_function", "last_diff", "get_context", "read_data", "get_class",
                       "guess_struct", "compiler_map", "list_functions", "search_flags", "search_notes",
                       "find_similar", "guide", "read_file", "search_code", "file_status", "standins_for"})

ROM_ONLY = {"claim_asset_region","inspect_asset_region","preview_asset_region","propose_asset_region","defer_asset_region","review_search_seed","get_boundary_evidence", "suggest_boundary", "get_context", "edit_context", "split_function", "redraft", "search_flags"}
FORK_ONLY = {"read_file", "search_code", "edit_file", "file_status", "split_file", "join_functions", "tidy_done", "tidy_file",
             "standins_for"}
LTCG_ONLY = {"standins_for"}  # only projects of games built with link-time code generation have harness/ files


def tool_names(fork: bool, console=None, ltcg: bool = False) -> list[str]:
    """The tools that make sense for a project: forks and ROM projects share the core loop. A game started from
    scratch in project mode has those of ROM projects its console makes work there too (Console.own_tools: PC's
    redraft and search_flags)."""
    skip = ROM_ONLY - (console.own_tools if console is not None else frozenset()) if fork else FORK_ONLY
    return [n for n in HANDLERS if n not in skip and (ltcg or n not in LTCG_ONLY)]


def project_tools(net: RomNetwork, slug: str | None, agent=None, include_disabled=False) -> list[str]:
    """tool_names for a project (None: no project yet)."""
    # Discovery needs the project kind, not a saved-code recheck. fork_slug() uses RomNetwork._project(), which
    # synchronizes progress even for ROM projects; two slow lists can occupy both MCP metadata workers and block
    # initialization too. Actual work tools still synchronize through the ordinary project/invocation paths.
    try:
        proj = Network._project(net, slug) if slug else None
        cfg = net.cfg(proj) if proj is not None else {}
    except NetworkError:
        return []
    fork = cfg.get('kind') == 'fork'
    cfg = cfg if fork else {}
    from ..forks.project import ForkProject
    from .tool_policy import visible
    manifest = (ForkProject(proj['root'], create_state=False).manifest or {}) if fork and cfg.get("origin") == "scratch" else {}
    names = tool_names(fork, consoles.of(cfg) if cfg.get("origin") == "scratch" else None,
                       bool(manifest.get("ltcg")))
    return names if include_disabled else visible(net, slug, names, agent)


def _named(net: RomNetwork, agent: Agent, slug: str, args: dict, tool="") -> dict:
    """Function names as agents write them (a plain symbol that repeats across files, symbol@unit, Class::method)
    turned into the stored name, preferring the function the agent holds."""
    function = args.get("function")
    if isinstance(function, str) and not function.strip():
        args = {**args, "function": ""}  # a blank name is no name: add_note says so, the other tools get none
    elif function and "," not in function:  # (several are resolved one by one, see _compile)
        try:
            resolved = net.resolve_function(agent, slug, function)
        except NetworkError:
            address_like = re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|(?:D_|func_)[0-9a-fA-F]{8,16})(?:\+(?:0x[0-9a-fA-F]+|[0-9]+))?',str(function))
            symbol = net.project_store(slug).one('SELECT 1 FROM symbols s JOIN projects p ON p.id=s.project_id WHERE p.slug=? AND s.name=?',slug,function)
            if tool not in {"get_function", "get_assembly"} or not (address_like or symbol):
                raise
            from .address_lookup import read_function
            resolved = read_function(net,agent,slug,function)
        args = {**args, "function": resolved}
    return args


@contextmanager
def _tool_foreground(net, agent, slug, name, args):
    if asset_agents.scope(net,agent):
        with net.progress.view():yield
        return
    # Ordinary judgements, current candidate/target reads and notes can use context
    # already installed by a saved recheck. Settings/layout/prerequisite changes
    # still serialize behind the complete import.
    # get_function's current DB views use the same committed context as
    # get_candidate/get_assembly. They must not queue for every saved verdict.
    # Experimental recovery suggestions retain the complete-import boundary.
    function_read = name == 'get_function' and args.get('section', 'summary') in {
        'summary', 'brief', 'candidate', 'source', 'draft', 'matched', 'all', 'full',
        'warnings', 'quality', 'history', 'notes', 'declarations', 'compiler', 'layouts'}
    allowed = function_read or name in {'compile_and_diff','compile_patch','compile_variants','add_note','get_assembly'} or (
        name == 'get_candidate' and args.get('base','current') == 'current') or (
        name == 'release_function' and not args.get('needs','').strip() and not args.get('not_c',False))
    notes = allowed and name in {'add_note','release_function'}
    progress = getattr(net, 'progress', None)
    with progress.foreground() if allowed and progress is not None else nullcontext():
        attempt = nullcontext()
        if notes and name == 'release_function':
            # Compiles own the candidate lock before publishing under the project
            # lock. A release must use that same order, including its receipt guard.
            fn = net._function(args['function'],slug)
            store = net.project_store(fn['project_id'])
            with store.lock:
                lock = net._attempt_locks.setdefault((agent.id,fn['id']),threading.RLock())
            from ..request_timing import timed_lock
            attempt = timed_lock(lock,'candidate_lock_wait')
        # Keep note writes and their publication marker together so a finishing
        # saved worker cannot acknowledge them before they are published.
        with attempt, progress._project_lock(slug) if notes and progress is not None else nullcontext():
            if notes and name == 'release_function' and net._function(args['function'],slug)['id'] != fn['id']:
                raise NetworkError('The function changed while this release waited. Read its current claim before releasing.')
            yield
            if notes and progress is not None:progress.foreground_changed(slug)


def _invoke(net: RomNetwork, agent: Agent, slug: str, name: str, args: dict) -> str:
    """Work tools see pulled changes; the pending compact connection handshake reads saved metadata."""
    if asset_agents.scope(net,agent):
        with net.progress.view():return _dispatch(net,agent,slug,name,args)
    if boundary_investigations.scope(net,agent):
        return _dispatch(net,agent,slug,name,args)
    if (name == 'project_info' and not args.get('detail', False)
            and (agent.id, slug) in getattr(net, '_pending_tool_handshakes', set())):
        return _dispatch(net, agent, slug, name, args)
    # Enter before this wrapper's sync, not only inside the engine's attempt.
    with _tool_foreground(net, agent, slug, name, args):
        progress = getattr(net, 'progress', None)
        if progress is not None:progress.sync(slug, force=True)
        result = _dispatch(net,agent,slug,name,args)
        if progress is not None and name not in READ_ONLY:
            progress.sync(slug, force=True)
        return result


def _dispatch(net,agent,slug,name,args, *, count_usage=True):
    asset_agents.guard(net,agent,slug,name,args)
    boundary_investigations.guard(net,agent,slug,name,args)
    pending = getattr(net, '_pending_tool_handshakes', set())
    handshake = (agent.id, slug) in pending
    if handshake and name != 'project_info':
        raise NetworkError('First call project_info(detail=false) to verify tools in this CLI session. No work was changed.')
    from .tool_policy import record
    record(net, agent, slug, name, count=count_usage)
    if not count_usage:
        return HANDLERS[name](net, agent, slug, **_named(net, agent, slug, args, name))
    from . import session_budget
    from .agent_local_pass import after_tool
    budget=session_budget.current(net,agent,slug)
    if budget:
        budget.begin()
    result=''
    try:
        if asset_agents.scope(net,agent) and name=='project_info':
            if handshake:pending.discard((agent.id,slug))
            return 'Asset discovery connection ready. Call claim_asset_region; do not claim or compile functions.'
        named=_named(net,agent,slug,args,name)
        # A real CLI handshake confirms authenticated access, not a saved-code
        # import. Keep its compact facts on the current database snapshot so a
        # long recheck cannot prevent the CLI from establishing its connection.
        # The next work tool still synchronizes and validates its own inputs.
        compact_handshake = handshake and name == 'project_info' and not args.get('detail', False)
        with net.progress.view() if compact_handshake else nullcontext():
            result=HANDLERS[name](net,agent,slug,**named)
        if name in {'get_function','get_assembly'} and args.get('function') != named.get('function'):
            result = f"Resolved read {str(args.get('function'))[:200]!r} to {named['function']}. This does not rename or create a function.\n"+result
        if handshake and name == 'project_info':
            pending.discard((agent.id, slug))
        if name == 'try_compiler':
            from .session_followups import diagnostic_completed
            diagnostic_completed(net,agent,named.get('function',''),slug)
        if name in {'report_boundary', 'report_data_region', 'report_candidate_issue', 'suggest_boundary'}:
            from .session_followups import investigation_completed
            investigation_completed(net, agent, slug, name)
        result=after_tool(net,agent,slug,name,named,result)
        if name in {'compile_patch','compile_and_diff','try_local_repairs','compile_family','compile_variants','compiler_experiments'}:
            function=named.get('function','')
            if function and ',' not in function:
                from .efficiency_feedback import frontload
                result=frontload(net,agent,slug,function,result)
        return result
    finally:
        if budget:
            budget.end(result or '')


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


def build_mcp(net: RomNetwork, agent_of: Callable[[Context], tuple[Agent, str]], observe=None) -> MCPServer:
    """MCP server for CLI agents. `agent_of` maps a request to (agent, project slug)."""
    from .tool_policy import ensure
    ensure(net)
    mcp = MCPServer(name="decomp", title="decompdecomp", instructions=INSTRUCTIONS, version=__version__)

    health_limiter = anyio.CapacityLimiter(2)
    async def run(ctx: Context, name: str, **args) -> str:
        queued=time.monotonic()
        try:
            checking = name == 'project_info' and (ctx.headers or {}).get('x-decomp-connection-check') == '1'
        except ValueError:
            checking = False
        prefix = 'connection_check_' if checking else ''
        call_id = uuid.uuid4().hex
        def execute():
            from ..request_timing import capture
            started=time.monotonic()
            try:
                agent,slug=agent_of(ctx)
            except NetworkError as error:
                raise ToolError(str(error)) from None
            if observe:
                observe(agent,slug,prefix+'tool_request',{'name':name,'arguments':args,'call_id':call_id})
            result=None
            error=None
            with capture() as metrics:
                dispatch=time.monotonic()
                try:
                    if checking:
                        # The engine's project lookup also synchronizes saved C.
                        # This authenticated wiring probe needs existence only;
                        # ordinary project_info/work tools retain engine sync.
                        Network._project(net,slug)
                        result=f'Authenticated tools ready for {slug}. App-only connection check; project facts are supplied separately.'
                    else:
                        # Name resolution also reads the project before the engine
                        # judge enters its own foreground scope.
                        with _tool_foreground(net, agent, slug, name, args):
                            result=_dispatch(net,agent,slug,name,args,count_usage=True)
                except Exception as exc:
                    error=exc
                elapsed=time.monotonic()-dispatch
            timings={'queue_ms':round((started-queued)*1000,2),'dispatch_ms':round(elapsed*1000,2),
                     'sql_read_ms':round(metrics['sql']*1000,2),'sql_reads':metrics['queries'],
                     'build_ms':round(metrics.get('build',0)*1000,2),
                     'write_lock_wait_ms':round(metrics.get('write_lock_wait',0)*1000,2),
                     'candidate_lock_wait_ms':round(metrics.get('candidate_lock_wait',0)*1000,2),
                     'before_dispatch_ms':round((dispatch-started)*1000,2)}
            timings.update({phase+'_ms':round(metrics.get(phase,0.)*1000,2)
                            for phase in ('sql_connection','sql_lock_wait','sql_execute','sql_fetch',
                                          'progress_project_lock_wait','progress_verification_wait','progress_recheck_wait','progress_sync',
                                          'progress_stat','progress_files','progress_manifest','progress_render','progress_import',
                                          'progress_saved_judge','progress_publish')})
            if observe:
                payload={'name':name,'call_id':call_id,'timings':timings}
                payload['error' if error else 'content']=str(error) if error else result
                observe(agent,slug,prefix+'tool_response',payload)
            from ..read_cache import ReadPending
            if isinstance(error,(NetworkError,ReadPending)):
                raise ToolError(str(error)) from None
            if error:
                raise error
            return result
        return await anyio.to_thread.run_sync(execute,limiter=health_limiter if checking else None)

    def tool(fn):
        full = " ".join((fn.__doc__ or "").split())
        TOOL_HELP[fn.__name__] = full
        description = full if len(full) <= 240 else full[:195].rsplit(' ', 1)[0] + f". More: guide(topic='tool:{fn.__name__}')."
        return mcp.tool(meta=ALWAYS_LOAD, description=description)(fn)

    @tool
    async def claim_asset_region(ctx: Context) -> str:
        """Claim or recover one data region in your asset discovery run; includes prior attempts and evidence."""
        return await run(ctx,"claim_asset_region")

    @tool
    async def inspect_asset_region(ctx: Context, region: str, claim_token: str, offset: int | None = None, length: int = 256) -> str:
        """Inspect a held region, neighbours and static usage; up to 4096 original bytes per window."""
        return await run(ctx,"inspect_asset_region",region=region,claim_token=claim_token,offset=offset,length=length)

    @tool
    async def preview_asset_region(ctx: Context, region: str, claim_token: str, action: str = "textures", options: dict | None = None, model_settings: dict | None = None) -> str:
        """Compare texture layouts, validate an image/audio preview, or test geometry; no base64 payloads."""
        return await run(ctx,"preview_asset_region",region=region,claim_token=claim_token,action=action,options=options,model_settings=model_settings)

    @tool
    async def propose_asset_region(ctx: Context, region: str, claim_token: str, proposal: dict) -> str:
        """Submit an advisory name/type, evidence, counter, uncertainty, approach, citations and optional layouts/boundaries. For review_target claims instead submit verdict (agree/disagree/uncertain), evidence and inspected ROM citations. Human acceptance remains separate."""
        return await run(ctx,"propose_asset_region",region=region,claim_token=claim_token,proposal=proposal)

    @tool
    async def defer_asset_region(ctx: Context, region: str, claim_token: str, approach: str, findings: str) -> str:
        """Record an unsuccessful approach and release this region; then claim another."""
        return await run(ctx,"defer_asset_region",region=region,claim_token=claim_token,approach=approach,findings=findings)

    @tool
    async def project_info(ctx: Context, detail: bool = False) -> str:
        """Which game you're working on, its compiler, and progress so far."""
        return await run(ctx, "project_info", detail=detail)

    @tool
    async def claim_function(ctx: Context, function: str = "", strategy: str = "", count: int = 1) -> str:
        """Claim the next function to decompile (or a specific one by name or address).

        Returns a lightweight receipt with saved candidate C and claim token. No baseline compile, new draft or
        imported build is run while claiming. Read get_assembly or get_function(section=brief) for supporting
        evidence; get_candidate rechecks a baseline. get_claims recovers held work. Each compile renews the claim.

        Args:
            function: optional name or address (0x80001234) of a specific function
            strategy: no_compiling_c (only functions without a recorded successful C compilation), untried (no compile attempts first), finish (forks' default: the files closest to being linked first), easiest (fewest
                instructions; ROM projects' default), closest (nearly matched already), hardest, random
            count: up to 6: small functions come as a batch (neighbours in one file) to write together and
                judge in one compile_and_diff; a big one comes alone
        """
        return await run(ctx, "claim_function", function=function, strategy=strategy, count=count)

    @tool
    async def get_function(ctx: Context, function: str, section: str = 'summary', start: int = 0, limit: int = 6000) -> str:
        """Show any function (by name or address): assembly, status, best attempt, notes. Read matched
        functions to learn how this codebase is written. section: summary (default), brief, all, history, candidate, experiment, quality, notes, declarations, compiler, layouts. start/limit page supporting sections by character offset; candidate/brief always return complete C. Prefer the specific section you need."""
        return await run(ctx, "get_function", function=function, section=section, start=start, limit=limit)

    @tool
    async def compile_and_diff(ctx: Context, function: str, source: str, repair_note: str = '',
                               repair_category: str = 'other', repair_automation: str = '') -> str:
        """Submit a whole-function rewrite and judge it against the original. For small edits use compile_patch to avoid resending C; score 0 = byte-identical.

        ROM projects: compiled with the game's compiler (context.h included), linked at the function's
        address and compared with the ROM. Forks: send the complete definition (plus any #include "...",
        prototypes, externs or #pragma lines it needs); it replaces the function in its source file, which is
        built with the project's build system and diffed with objdiff. Several functions ("a, b, c"): send all
        their definitions in one source; each is judged on its own."""
        return await run(ctx, "compile_and_diff", function=function, source=source, repair_note=repair_note,
                         repair_category=repair_category, repair_automation=repair_automation)

    @tool
    async def get_claims(ctx: Context) -> str:
        """Recover your live claims, canonical function names and claim tokens in this project. Read-only; no builds, drafting, new claims or source selection. Use get_candidate for C/revision."""
        return await run(ctx, "get_claims")

    @tool
    async def get_candidate(ctx: Context, function: str, base: Literal["current", "saved_best", "last_compiling", "reported_corrected"] = "current") -> str:
        """Read your current candidate C/revision and bounded advisory C-line mismatch locations, when compiler debug information is available. Use base="saved_best" to explicitly restore saved best C, base="reported_corrected" to select a compiled alternative explicitly linked to a quality report (an agent hypothesis, not verified correctness), or base="last_compiling" to recover your last scored C after compiler errors. Selection changes only your continuation; historical scores require rejudging. No full assembly listing or historical notes."""
        return await run(ctx, 'get_candidate', function=function, base=base)

    @tool
    async def compile_patch(ctx: Context, function: str, revision: str, old: str, new: str, repair_note: str = '',
                            repair_category: str = 'other', repair_automation: str = '', occurrence: int = 0) -> str:
        """Compile one exact-text replacement in your versioned candidate. Use occurrence=1-based index for repeated old text; default 0 requires a unique anchor. Stale revisions are refused.
        Uses the ordinary compiler, claim limits and exact judge; no files bypass the build checks."""
        return await run(ctx, 'compile_patch', function=function, revision=revision, old=old, new=new, occurrence=occurrence,
                         repair_note=repair_note, repair_category=repair_category, repair_automation=repair_automation)

    @tool
    async def compile_variants(ctx: Context, function: str, revision: str, patches: list[dict]) -> str:
        """Try 1–6 independent old/new replacements against one candidate revision. Each patch can specify occurrence=1-based index for repeated old text. Normal judge and limits.
        Stops on match or claim error; returns a compact table and current revision. Cached or rejected
        trials may retain earlier C. Each patch starts from the original snapshot, not the previous patch."""
        return await run(ctx, 'compile_variants', function=function, revision=revision, patches=patches)

    @tool
    async def compiler_experiments(ctx: Context, function: str, revision: str, trials: list[CompilerTrial]) -> str:
        """Test 1–6 explicit compiler/flags profiles, optionally with independent old/new/occurrence patches against one saved revision. 60-second budget; diagnostic only. No source resends, match credit or compiler application."""
        return await run(ctx,'compiler_experiments',function=function,revision=revision,trials=[t.model_dump(exclude_none=True) for t in trials])

    @tool
    async def get_compiler_trial(ctx: Context, function: str, trial_id: str, include_header: bool = False) -> str:
        """Retrieve an exact experimental C/compiler/flags pair and its diagnostic result for this worker. In-memory, bounded history; no compile or application."""
        return await run(ctx,'get_compiler_trial',function=function,trial_id=trial_id,include_header=include_header)

    @tool
    async def review_search_seed(ctx: Context, function: str, trial_id: str, review_note: str = '', enabled: bool = True) -> str:
        """Explicitly review a current-profile compiler diagnostic for the next bounded deep search. Record why it is promising and caller checks. Saved C is unchanged; changed/unknown signatures stay diagnostic-only even if exact. enabled=false disables the seed."""
        return await run(ctx,'review_search_seed',function=function,trial_id=trial_id,review_note=review_note,enabled=enabled)

    @tool
    async def get_search_brief(ctx: Context, function: str) -> str:
        """Read a compact current-input packet: allocation source lines/priorities, negative experiments, reviewed roots and source references. No compiler runs or source adoption."""
        return await run(ctx,'get_search_brief',function=function)

    @tool
    async def try_local_repairs(ctx: Context, function: str, revision: str, families: list[str] | None = None, max_trials: int = 6) -> str:
        """Try applicable local repairs against one revision; at most six normal compiles and 60 seconds. Omit families to discover repairs. Includes local_widening, callee_parameter_width, callee_return_width, split_integer_lifetime, merge_integer_lifetimes, branch_call, goto_leaf and switch_layout. Retain best continuation; use instead of parallel family probes."""
        return await run(ctx,'try_local_repairs',function=function,revision=revision,families=families,max_trials=max_trials)

    @tool
    async def compile_family(ctx: Context, function: str, revision: str, family: str,
                             selector: NextFamilyTarget | AnchorFamilyTarget | DeclarationFamilyTarget | None = None) -> str:
        """Bounded local family; selector has exactly one mode: next, anchor (old/occurrence), or declaration (name).
        Omit selector for pattern families or automatic signedness/declaration_order targets. Normal judge and budgets apply."""
        return await run(ctx,'compile_family',function=function,revision=revision,family=family,
                         selector=selector.model_dump() if selector else None)

    @tool
    async def family_targets(ctx: Context, function: str, family: str = 'declaration_order', expression: str = '') -> str:
        """Read current revision and valid named targets/pairs with tried status. No compile or full source.
        Optional expression gives literal occurrence counts and line numbers; family=casts needs expression."""
        return await run(ctx, 'family_targets', function=function, family=family, expression=expression)

    @tool
    async def get_assembly(ctx: Context, function: str, start: int = 0, limit: int = 60, focus: str = "") -> str:
        """Read target assembly: focus="mismatch" for the first differing target instruction, or a callee symbol/short anchor. Default focuses the first mismatch, or a bounded body region. Focus returns at most 40 lines. Use focus="page" for explicit start/limit paging (up to 200 lines)."""
        return await run(ctx, 'get_assembly', function=function, start=start, limit=limit, focus=focus)

    @tool
    async def report_repair(ctx: Context, function: str, change: str, category: str = 'other', automation: str = '') -> str:
        """Annotate your captured verified match: the decisive change and how a deterministic rule could make it.
        Optional after-the-fact alternative to compile_and_diff's repair_note. One sentence is enough; do not
        guess. This collects review evidence and never enables a rule. Categories: types, casts, constants,
        addresses, signatures, layout, temporaries, expression_order, control_flow, compiler_settings, other.
        """
        return await run(ctx, 'report_repair', function=function, change=change, category=category, automation=automation)

    @tool
    async def last_diff(ctx: Context, function: str, context: int = 1, start: int = 0, limit: int = 40) -> str:
        """A page of your last compile diff. Zero-based start and limit (up to 200 rows). Costs no compile.
        context controls equal rows around differences; advance start for the next page."""
        return await run(ctx, "last_diff", function=function, context=context, start=start, limit=limit)

    @tool
    async def report_candidate_issue(ctx: Context, function: str, revision: str, issue: str, reason: str, alternative_revision: str = "") -> str:
        """Report a suspected invalid saved candidate by revision. Issues: uninitialized, loop_mutation,
        wrong_store, wrong_value, other. Give a short concrete reason; optional alternative_revision must
        be a different compiled/scored source of this function; it is retained separately and selectable with
        get_candidate(base="reported_corrected"). Hypotheses only: no score or match is changed.
        """
        return await run(ctx, 'report_candidate_issue', function=function, revision=revision, issue=issue,
                         reason=reason, alternative_revision=alternative_revision)

    @tool
    async def release_function(ctx: Context, function: str, notes: str, needs: str = "", not_c: bool = False, move_on: bool = False, next_experiment: dict | None = None, claim_token: str = "") -> str:
        """Give up a function you're stuck on, once compile_and_diff says to move on. Notes are required: what you
        tried, what is left. Your best attempt is kept for the next agent. Released before that (fewer compiles than
        the move-on rule), it answers once with what to try instead; call it again to release anyway.

        Args:
            function: the function you hold
            notes: what you tried, what is left
            claim_token: always pass the token from the current claim, continuation state or get_candidate, including the first release and retries. A stale token cannot release a new claim.
            next_experiment: optional untested exact patch with revision, old, new and title. Validated against your current candidate; rejected if stale or ambiguous. The next agent can retrieve it with get_function(section="experiment") and test through compile_patch.
            needs: optional: a direct callee recorded in the original assembly that blocks this function. Paused
                candidates cannot be selected this way. Explain indirect/inline/header concerns in notes instead.
            move_on: explicitly release now with precise notes explaining why further exploration is unproductive. Skips the one-time release pushback; does not mark a match or no-C verdict.
            not_c: true only with evidence no C makes its code at all (hand-written assembly), saying why in notes. Unsupported compiler extensions, asm-register/tool restrictions, return-address builtins or unsuitable build flags are not proof; describe those as blockers and release normally:
                once agents of two models agree, it's set aside and no one is handed it again.
        """
        return await run(ctx, "release_function", function=function, notes=notes, needs=needs, not_c=not_c, move_on=move_on, next_experiment=next_experiment, claim_token=claim_token)

    @tool
    async def get_context(ctx: Context, function: str = "") -> str:
        """The shared header (types, structs, prototypes) used by function. Omit function only when you hold exactly one claim, or the project has a single header. Pass function explicitly for per-file headers or multiple claims."""
        return await run(ctx, "get_context", function=function)

    @tool
    async def edit_context(ctx: Context, old: str, new: str, message: str, function: str = "") -> str:
        """Edit the shared header: replace `old` (must occur exactly once) with `new`, or append `new` when
        old is "". Accepted only if every matched function still matches. Add prototypes and structs here."""
        return await run(ctx, "edit_context", old=old, new=new, message=message, function=function)

    @tool
    async def rename_symbol(ctx: Context, old: str, new: str, message: str = "") -> str:
        """Give a function or data address a real name (e.g. func_80001234 -> player_update). Everything that
        uses the name changes with it (ROM: the assembly, matched sources, the header; a project: the game's
        symbols in its config and every source and header file); never breaks a match."""
        return await run(ctx, "rename_symbol", old=old, new=new, message=message)

    @tool
    async def split_function(ctx: Context, function: str, at: str, new_name: str = "") -> str:
        """Split a function the disassembler merged with the next one (e.g. after a function that never
        returns). `at` is the address where the second function starts."""
        return await run(ctx, "split_function", function=function, at=at, new_name=new_name)

    @tool
    async def get_boundary_evidence(ctx: Context, function: str) -> str:
        """Original mapped instructions, neighbours, direct edges and symbols for boundary review; no claim or compile."""
        return await run(ctx, "get_boundary_evidence", function=function)

    @tool
    async def suggest_boundary(ctx: Context, function: str, action: str, supporting: str, counter: str,
                               uncertainty: str, citations: str, end: str = "", confidence: str = "tentative") -> str:
        """Save an advisory boundary recommendation with cited hex instructions; never apply a layout change.
        action: delay_slot, join_previous, adjust, padding, data, keep, unclear. end: exclusive hex address for adjust.
        confidence: strong, tentative, unclear. citations: comma-separated hex instruction addresses from evidence."""
        return await run(ctx, "suggest_boundary", function=function, action=action, supporting=supporting,
                         counter=counter, uncertainty=uncertainty, citations=citations, end=end, confidence=confidence)

    @tool
    async def report_boundary(ctx: Context, function: str, end: str, reason: str) -> str:
        """Report that a function really ends earlier: what follows `end` is alignment padding, linker leftovers
        or data, not code anyone wrote (or, in a PC project, another function the analysis merged with it), so no C
        can match the whole thing. ROM projects: the user approves it on the project page; then the function ends at
        `end` (an address) and the rest is set aside. PC projects: once agents of two models agree, the function is
        cut at `end` (an address, or an offset into it: +0x20) and the rest is a function of its own, set aside
        when it isn't code, kept only if the game still builds byte for byte. Say how you can tell."""
        return await run(ctx, "report_boundary", function=function, end=end, reason=reason)

    @tool
    async def report_data_region(ctx: Context, function: str, start: str, end: str, reason: str) -> str:
        """Report a whole suspected data table, including entries mistaken for functions. Claim a representative
        function first; its unit and original image identify the overlay. Use an exclusive end address and explain
        byte evidence. One review covers the region. No match/status changes; the user can pause its new claims
        in Settings. Do not misuse report_boundary to leave one word of a table pretending to be code."""
        return await run(ctx, "report_data_region", function=function, start=start, end=end, reason=reason)

    @tool
    async def resolve_addresses(ctx: Context, queries: list[str], context_function: str = "") -> str:
        """Resolve 1–20 symbols/hex addresses in one read-only call: function entry, interior, data, unknown or
        ambiguous. Reports canonical names, owning function offsets, units and original image positions.
        Supply context_function for overlays sharing RAM addresses; otherwise a unique held image supplies context.
        Registered data is never silently treated as code. No rename, boundary, claim or matching-status changes."""
        return await run(ctx, "resolve_addresses", queries=queries, context_function=context_function)

    @tool
    async def read_data(ctx: Context, what: str, length: int = 64) -> str:
        """Hex dump of the original's data: an address or symbol (ROM projects), a data symbol such as
        lbl_1_data_10 (forks); text, pointers and floats decoded."""
        return await run(ctx, "read_data", what=what, length=length)

    @tool
    async def redraft(ctx: Context, function: str, context: str) -> str:
        """Requires nonempty actual C declarations in context; to inspect existing C use get_function(section="candidate").
        m2c's machine draft of a function made again with your declarations (a struct, a prototype, an extern
        you worked out) added to the shared header's; yours replace the header's where they declare the same
        names. Returns the new draft, recorded nowhere: try it with compile_and_diff, and edit_context what helped.
        PC games: Ghidra's draft made compilable again with them (a class declaring the function, a callee's
        prototype)."""
        return await run(ctx, "redraft", function=function, context=context)

    @tool
    async def get_class(ctx: Context, what: str = "") -> str:
        """C++ classes found in the game (MIPS games' and PC programs' code; a PC program's run-time type
        information has the classes' own names): give a vtable address or a virtual function, constructor or destructor for its class's
        fields (offset, type, which functions use them) and virtual functions by slot; nothing lists them all. A
        layout to write the class from: the judge still decides."""
        return await run(ctx, "get_class", what=what)

    @tool
    async def guess_struct(ctx: Context, function: str, argument: str = "arg0") -> str:
        """The likely struct behind a function's pointer (MIPS games, PC programs), as C with offsets: from the loads
        and stores of every function the same pointer reaches, with how many agree on each field, where else the
        type flows, and the type matched code already uses for it. argument: arg0-arg3 (PC: on the stack), `this`
        (PC: ecx), return, or a global; add +0x14 to follow the pointer at 0x14. The game's own type when its debug
        information has one. Put what you confirm into context.h with edit_context (a project's header with
        edit_file)."""
        return await run(ctx, "guess_struct", function=function, argument=argument)

    @tool
    async def compiler_map(ctx: Context, survey: bool = False, check_ranges: bool = False) -> str:
        """Which compiler built each part of the game (ROM projects, MIPS; projects whose source files each have a
        compiler, PC and DS: over their files, with what the program itself says built it): where the code looks
        built by another compiler than the one it builds with today, from matches, habits that rule compilers out,
        and the judge. survey=true also starts the judge's survey in the background: agents' C for unmatched
        functions built with each compiler the project uses; what it confirms becomes a suggestion. A suggested range
        is tested on its neighbours before it applies (range checks, listed here); check_ranges=true looks at the
        applied ranges (projects: the open checks) again the same way."""
        return await run(ctx, "compiler_map", survey=survey, check_ranges=check_ranges)

    @tool
    async def list_functions(ctx: Context, status: str = "open", limit: int = 40) -> str:
        """List functions by status: open, matched or all."""
        return await run(ctx, "list_functions", status=status, limit=limit)

    @tool
    async def add_note(ctx: Context, text: str, function: str) -> str:
        """Leave a note on a function for the next agent on it. Say which function it's for: something every agent
        on this game should know goes through share_knowledge."""
        return await run(ctx, "add_note", text=text, function=function)

    @tool
    async def search_flags(ctx: Context, function: str, compiler: str = "", source: str = "") -> str:
        """The function's C (its best attempt, or source) built with each of its compiler's flag variants
        (optimisation levels, small data, code-shape options), judged and ranked. For C that looks right but won't
        match: the flags may be what's wrong. compiler: another one to try instead of the project's. PC games: the
        function's source file's flags (/Oy- keeps the frame pointer, /O1, /Ob1); one that matches stays the file's."""
        return await run(ctx, "search_flags", function=function, compiler=compiler, source=source)

    @tool
    async def claim_readable(ctx: Context) -> str:
        """Take the next matched function that isn't readable yet: rename, type and comment it so people understand
        it, while it still matches byte for byte."""
        return await run(ctx, "claim_readable")

    @tool
    async def polish_function(ctx: Context, function: str, renames: str = "{}", comment: str = "", types: str = "{}", reuse: bool = False) -> str:
        """Apply a small polish plan, judged byte for byte. renames and types are JSON objects mapping old to new
        identifiers. Renames cover simple parameters/locals only; types must already exist in the header. comment
        is one line. Physical source lines stay intact. reuse=true tests the unique verified identical-helper plan.
        Use rename_symbol for shared names, make_readable for complex scopes or larger edits."""
        return await run(ctx, "polish_function", function=function, renames=renames, comment=comment, types=types, reuse=reuse)

    @tool
    async def tidy_file(ctx: Context, path: str) -> str:
        """Remove exact duplicate includes/extern declarations without shifting source lines. Uses edit_file's build,
        full match protection and rollback. Does not mark the file tidied: review names/types, then tidy_done."""
        return await run(ctx, "tidy_file", path=path)

    @tool
    async def make_readable(ctx: Context, function: str, source: str) -> str:
        """A readable rewrite of a matched function (names, types, a comment). Kept only if it still matches byte
        for byte; otherwise the matching C stays."""
        return await run(ctx, "make_readable", function=function, source=source)

    @tool
    async def tidy_done(ctx: Context, unit: str, summary: str) -> str:
        """The source file claim_readable gave you to tidy is done (its types, names and declarations): say what you
        changed in a sentence or two."""
        return await run(ctx, "tidy_done", unit=unit, summary=summary)

    @tool
    async def share_knowledge(ctx: Context, text: str = "", supports: int = 0, evidence: str = "") -> str:
        """Keep text as the reusable rule without function-specific progress. Put concrete function names, scores and examples in evidence; evidence is retained for review, not included in every agent prompt.
        Reusable game/compiler matching tips across functions. Not function-specific notes: use
        release_function(notes=...) or add_note(function=...).
        Propose a general technique or back an independently verified proposal with supports=its number.
        Submit your own evidence-backed proposal now; do not wait for a second model before proposing.
        The user may accept one model's proposal; automatic acceptance requires two models.
        Compiler identity/profile changes belong in compiler_map, try_compiler/search_flags or suggest."""
        return await run(ctx, "share_knowledge", text=text, supports=supports, evidence=evidence)

    @tool
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

    @tool
    async def try_compiler(ctx: Context, function: str, compiler: str, flags: str = "", source: str = "") -> str:
        """Compile a function's C with another compiler and flags and see the diff, recording nothing: to find out
        which compiler built a part of the game before you suggest it. Cheap: try several.

        Args:
            function: the function to judge it against
            compiler: current retains this function's effective compiler and default flags, or a toolchain id (project_info lists this platform's); where each source file has its own
                compiler (DS), "all" tries every one, and the one that matches becomes the file's
            flags: its flags (default: that compiler's defaults for this game)
            source: the C (default: the function's best attempt)
        """
        return await run(ctx, "try_compiler", function=function, compiler=compiler, flags=flags, source=source)

    @tool
    async def search_notes(ctx: Context, query: str) -> str:
        """Search notes left by agents."""
        return await run(ctx, "search_notes", query=query)

    @tool
    async def find_similar(ctx: Context, function: str, limit: int = 5) -> str:
        """Functions whose instructions look like this one's (copy-pasted code, the same inlined helper,
        template instances), matched ones first, with the matching source of the best one. Start there."""
        return await run(ctx, "find_similar", function=function, limit=limit)

    @tool
    async def guide(ctx: Context, topic: str = "") -> str:
        """Decompilation know-how. Call without a topic for the list (workflow, mips, gcc-kmc, ido, m2c,
        data, structs, n64, naming)."""
        return await run(ctx, "guide", topic=topic)

    @tool
    async def read_file(ctx: Context, path: str = "", start: int = 1, count: int = 200, file_path: str = "",
                        offset: int = 0, limit: int = 0) -> str:
        """Forks only: read a file of the project (path relative to its root), with line numbers."""
        # file_path/offset/limit: the names of Claude Code's own Read tool, which agents passed here hundreds of
        # times a night, each a refused call
        if not (path := path or file_path):
            raise ValueError("read_file needs path: the file, relative to the project's root")
        return await run(ctx, "read_file", path=path, start=offset or start, count=limit or count)

    @tool
    async def search_code(ctx: Context, pattern: str, glob: str = "") -> str:
        """Forks only: search the project's C/C++ sources and headers with a regular expression. `glob`
        narrows the files (e.g. "include/**/*.h")."""
        return await run(ctx, "search_code", pattern=pattern, glob=glob)

    @tool
    async def edit_file(ctx: Context, path: str, old: str, new: str, message: str = "") -> str:
        """Forks only: change a C/C++ source or header file. Replace `old` (exactly once) with `new`, or
        append `new` when old is "". The project is rebuilt and measured; the edit is undone if any matched
        function stops matching. Use it for structs, prototypes, globals and helpers. `message`: what the
        change is, for its commit."""
        return await run(ctx, "edit_file", path=path, old=old, new=new, message=message)

    @tool
    async def file_status(ctx: Context, path: str) -> str:
        """Forks only: how far a source file (or a function's file) is from being linked into the game: which
        functions still differ, its data next to the original's (with what to fix), why the last link failed.
        A file is linked (the game built from its C) once every function and all its data match."""
        return await run(ctx, "file_status", path=path)

    @tool
    async def standins_for(ctx: Context, what: str) -> str:
        """Games built with link-time code generation (the brief's "Calling convention" section says so): a starting
        harness for a unit, from the original's references. `what`: a unit, its source file or one of its functions.
        Returns stand-ins for what the unit's functions call or read elsewhere (with their conventions), a
        HarnessEntry that calls each function the way its call sites do, and the declarations to put in the unit's
        source. Write them into harness/<unit>.standins.cpp and harness/<unit>.drv.cpp with edit_file (old=""
        creates a file), then shape them: the stand-ins must read the registers the original reads and leave the
        ones its callers keep alive."""
        return await run(ctx, "standins_for", what=what)

    @tool
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

    @tool
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
