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

from .. import __version__, agents, arch, candidate_quality, claim_stalls, consoles
from ..network import CLAIM_ORDERS, Agent, Network, NetworkError
from ..release_receipts import operation as release_operation
from ..repair_feedback import AGENT_CATEGORIES, submission, track_matches
from . import (
    agent_tokens,
    asset_agents,
    automatic_assignment,
    boundary_investigations,
    claim_state,
    compiler_experiments,
    compiler_passes,
    compiler_source,
    focused_agents,
    learnings_search,
    local_experiments,
    polish_automation,
    prove_chain,
    repair_search,
    reviewed_search_seeds,
    score_permutations,
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
    "near-match": "finishing a near-match: combining neutral edits, compiler passes, register priorities",
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
    "port": "fixing matched C so it also builds for a PC, without losing the console match",
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
   structs it's handed, the best previous attempt and notes (start from those!), a
   machine draft (m2c), and identical matched code if any. The machine draft's control flow and arithmetic are
   usually right, its types and names usually wrong: start from it and fix those. redraft(function, context)
   makes it again with a struct or prototype you worked out.
2. get_context() shows the shared header every compile includes (when it's big, the brief has the
   declarations the function uses).
3. Write the C functions (same names as the assembly) and judge them: compile_and_diff(function="a, b, c",
   source=all the definitions) for a batch, one name for one. Read the diff: '<' '>' missing/extra
   instructions, 'i' operands, 'o' order, 'r' registers, 's' stack. Fix structure first, registers last.
   Read guide("workflow") or guide("{compiler_guide}") when needed. find_similar(function) shows look-alike functions;
   a matched one is the best starting point. Try the supplied same-game twin before editing a draft.
   Next, use compiler_experiments for a specific assembly-backed flag or source/flag hypothesis.
   For a small register-only diff, try_local_repairs can probe operand order and temporary lifetimes
   before you spend more attempts on broad rewrites.
4. Score 0 = matched and saved. Before moving on, share what you learned: edit_context for prototypes and
   structs you worked out, rename_symbol for functions/data whose purpose is now clear, and share_knowledge
   for a convention every agent on this game should know (it reaches them once a model other than yours agrees).
   Right C that can't match because the project's compiler or flags look wrong? search_flags(function) tries
   its compiler's flags at once, try_compiler(...) other compilers; then suggest(...) what matches to the user.
5. compile_and_diff says when to move on ({budget}): then
   release_function and claim the next one. If you learned something, leave a short handoff: what still
   differs, what you tried and its judged result (including failed approaches), and one untested
   "Next step: ..." for the next agent. Omit notes if there is nothing useful to pass on. Before
   that, compile the next step instead of releasing. A direct callee blocks work?
   release_function(needs=that one) allows one pivot per unchanged input, respecting pauses. Sure that
   no C makes its code at all (hand-written assembly): not_c=true, saying why.
   A boundary-review warning comes first: use get_boundary_evidence to inspect the original bytes, overlap,
   and possible data references. Leave boundary decisions to review and move to an eligible function.
   A target ending without a return is not an assembly verdict; report or correct its extent first.

Write plausible original code, pure C (no inline asm). Never fetch or copy code from outside this project
(another repository, a finished decomp of this game, the internet): a match must come from your own C. Be brief in your messages: work, don't narrate.
"""

# What a ROM helper is told when its processor has the reader (never a tool the game's console doesn't offer)
WORK_STRUCTS = """\
The brief says when a function is handed a struct: guess_struct(function, "arg0") shows one as C."""
WORK_BOUNDARY = """\
The function runs on past its end into padding, linker leftovers or data? report_boundary(...) where it really
ends, then work on as it is."""

# A helper started on "make matched code readable" (options task=readable), in any project (app/readable.py);
# {names}: step 3, as the project's mode has it (READABLE_ROM, READABLE_FORK)
READABLE_PROMPT = """\
You are a decompilation agent in decompdecomp, working on {title}. The functions you get already match the game
byte for byte. Your job: make their C readable for people who will port or mod the game, without changing what
the compiler makes of it. Other agents may work on other functions at the same time. Repeat for up to {count}
functions, then stop:

1. claim_readable() gives you the next matched function that isn't readable yet, with its matching C.
   Follow the game's optional polishing checklist when the claim includes it.
2. Read the function, and get_function on the functions it calls, until you understand what it does.
3. {names}
4. Prefer polish_function(function, renames='{{"arg0":"value"}}', comment="What it does") for local names and comments.
   renames/types are JSON objects. Existing header type aliases can be substituted with types. The app applies the
   plan without shifting physical lines, judges it, and learns verified patterns for identical helpers. reuse=true
   tests a suggested pattern. Shared names still use rename_symbol. For complex scopes or restructuring use
   make_readable(function, source): the same function with meaningful parameter and local names, the header's
   types and names, and one comment saying what it does. Preserve the original behavior and the code shape that
   matching requires. Each rewrite is judged again and must still match byte for byte, or it's refused and the matching C stays. Refused? Undo
   your last change and try a smaller one.

Once every function in a source file is readable, claim_readable may give you the file to tidy instead (TIDY A
FILE): duplicate declarations/includes have already had a guarded cleanup. Follow its list with edit_file and
rename_symbol, use tidy_file(path) for another mechanical cleanup, then tidy_done. A file counts as one of your {count}.

Write C people would write. Be brief in your messages: work, don't narrate.
"""

READABLE_ROM = ("rename_symbol the globals and other functions it uses, once their purpose is clear.\n"
                "   Describe structs, enums and constants in the shared header (get_context shows it) with\n"
                "   edit_context, and use those names.")
READABLE_FORK = ("rename_symbol the globals and other functions it uses, once their purpose is clear\n"
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
   release_function and claim the next. If you learned something, leave a short handoff: what still
   differs, what you tried and its judged result (including failed approaches), and one untested
   "Next step: ..." for the next agent. Omit notes if there is nothing useful to pass on. Before that,
   compile the next step instead of releasing; sure that no C makes its code at all (hand-written
   assembly): not_c=true, saying why. Very large functions: get the structure right first; a near miss
   is worth the extra compiles. A direct callee blocks work?
   release_function(needs=that one) allows one pivot per unchanged input, respecting pauses.

Rules: C/C++ only (no inline asm, asm functions or asm includes); don't touch targets, configs or build
scripts (the tools refuse). Never fetch or copy code from outside this project (another repository, a
finished decomp of this game, the internet): a match must come from your own C. Write code a human would
have written: match the file's style and names.
Guides: {guides}. Be brief in your messages: work, don't narrate.
"""

# What a fork whose console can join functions is told (the base prompt names no tool its console lacks)
FORK_JOIN = """\
Part of another function the analysis cut (no prologue, registers the code before set, a switch's cases in the
next symbol)? join_functions(it, the other part, why), so it becomes one whole function."""

# What a ROM helper whose engine can join functions is told (the base prompt names no tool its engine lacks)
ROM_JOIN = """\
Part of another function the analysis cut (no prologue, registers the code before set, a switch's cases in the
next symbol)? join_functions(it, the other part, why), so it becomes one whole function."""


REPAIR_GUIDANCE = (
    "Classify each verified repair before moving on. Put repair_category on a likely winning single-function "
    "compile_and_diff or compile_patch; it is saved only on a match. If the result asks for report_repair, "
    "send just the category. Categories: " + ", ".join(AGENT_CATEGORIES) +
    ". For other, optionally give a short suggested_category. No explanation or extra compile required. "
    "If a mismatch persists, use search_repair_examples for past fixes.\n"
)
HANDOFF_GUIDANCE = """
When releasing a stalled function, give the next agent a structured handoff in next_experiment.
Use revision for the candidate they will inherit, tried for the approach you tested, changed for the
observed result (including unchanged output), and title for one untested next step. Include old and new
only for a precise patch with a unique anchor. If you explored a worse candidate, recover saved_best
before proposing its next step. If there is no usable candidate, leave those findings in notes.
A handoff is advisory. The next agent sees it on the claim while it applies.
"""
MATCHING_WORKFLOW = 'On hard functions, get_function(section="work") gives one measured objective, current C and recent trials. Use try_local_repairs(compare=true) to compare up to three measured options, then choose a continuation. Prefer compile_patch for small edits and compile_and_diff for substantial rewrites. Keep the returned candidate revision. Do not fetch get_candidate after a successful patch unless the source is lost or stale. Same-function edits must be sequential. compile_variants runs up to six independent supplied alternatives against one revision and returns the chosen continuation; keep the baseline on ties. Automatic repairs are explicit via try_local_repairs, never a substitute for submitted variants. A saved historical score can differ from the current verified baseline; compare current trials to the rechecked baseline. Global share_knowledge is only for reusable game/compiler matching tips; function-specific findings belong in release notes. Optional tools may be disabled: use the available schema. guide(topic="agent-experiments") explains repair families, selectors, recovery, compiler trials and paging. Detailed tool help is guide(topic="tool:NAME").'
WORK_PROMPT += "\n" + REPAIR_GUIDANCE + HANDOFF_GUIDANCE + MATCHING_WORKFLOW
FORK_PROMPT += "\n" + REPAIR_GUIDANCE + HANDOFF_GUIDANCE + MATCHING_WORKFLOW


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
    from .variants import member
    variant = member(net, slug) if getattr(net, "store", None) is not None else None
    scope = (f"Variant scope: {variant['variant']} (target {slug}). Match results and claims apply only to this target. "
             "Shared source revisions require independent verification of every bound variant.\n" if variant else '')
    return scope + _variant_project_info(net, slug, detail)


def _variant_project_info(net: RomNetwork, slug: str, detail: bool = False) -> str:
    if not detail:
        return _compact_project_info(net, slug) + compact_info_steering(net.instructions(slug), net.claim_order(slug))
    wish = net.instructions(slug)
    text = _project_info(net, slug, detail)
    if hasattr(net, 'project_store') and (milestones := net.project_store(slug).one(
            "SELECT COUNT(*) AS total,SUM(f.status='matched' AND f.matched_at>=m.recorded_at) AS later "
            "FROM matching_milestones m JOIN functions f ON f.id=m.function_id "
            "JOIN projects p ON p.id=f.project_id WHERE p.slug=?", slug)) and milestones['total']:
        text += (f"\nMeasured regional objectives: {milestones['total']} functions, "
                 f"{milestones['later'] or 0} later exact matches.")
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


COMPACT_NEXT = ('Compact saved metadata; each claim uses its own effective compiler profile. '
                'project_info(detail=true) loads full facts, knowledge and suggestions. Next: get_claims() to recover held work, or claim_function().')


def compact_info_head(net, proj):
    """project_info(detail=false) before its progress line: saved settings only. The MCP gateway answers that call
    itself from these parts and the database's totals (gateway/src/native.rs)."""
    cfg=net.cfg(proj)
    profile=cfg.get('compiler') or {}
    if isinstance(profile, str):
        profile = {'toolchain': profile}
    platform=consoles.get(cfg.get('platform'))
    compiler=profile.get('toolchain') or 'set by the project build'
    known=', '.join(c.id for c in platform.compilers)
    return (f"{proj['name']} ({proj['slug']})\ncompiler: {compiler} {' '.join(profile.get('flags',[]))}\n"
            + (f"this platform's compilers (for try_compiler): {known}\n" if known else ''))


def compact_info_steering(wish, order):
    """What project_info(detail=false) adds after the compact facts: the owner's claim order and instructions."""
    steering = f"\nThe project's owner chose the order helpers take functions in: {CLAIM_ORDERS[order]}." if order else ''
    return steering + (f"\nThe user's instructions for this project (they come first):\n{wish}" if wish else "")


def _compact_project_info(net, slug):
    """Indexed totals and saved metadata only: no overview, SDK, room or build scans."""
    proj=net._project(slug)
    totals=net.project_store(slug).one('SELECT total,matched,attempts FROM project_totals WHERE project_id=?',proj['id'])
    return (compact_info_head(net, proj)
            + (f"progress: {totals['matched']}/{totals['total']} functions matched; {totals['attempts']} compiles\n" if totals else 'Progress totals unavailable; no project scan performed.\n')
            + COMPACT_NEXT)


def _data_unit(net: RomNetwork, slug: str, name: str) -> str:
    from .data_units import unit, unit_text
    return unit_text(unit(net, slug, name))


def _compile_data(net: RomNetwork, slug: str, name: str, source: str, agent=None) -> str:
    from .data_units import compile_data, describe
    result = compile_data(net, slug, name, source)
    _data_agents().compiled(net, agent, slug, name, result)
    text = describe(result, name)
    return text + ("\nclaim_data() for the next unit." if result.exact and _data_agents().scope(net, agent) else "")


def _data_agents():
    from . import data_agents
    return data_agents


def _port_agents():
    from . import port_agents
    return port_agents


def _fix_for_pc(net: RomNetwork, agent, slug: str, function: str, source: str = "", edits=None) -> str:
    from ..port import port_fix
    return _port_agents().fixed(net, agent, slug, function,
                                port_fix.fix(net, agent, slug, function, source, edits=edits))


def _check_for_pc(net: RomNetwork, slug: str, function: str, source: str = "", edits=None) -> str:
    from ..port import port_fix
    return port_fix.check(net, slug, function, source, edits=edits)


def _candidate_behaviour(net, agent, slug, function, revision, calls=2, vis=3600, input=''):
    from .candidate_behaviour import answer
    return answer(net, agent, slug, function, revision, calls, vis, input)


def _compare_behaviour(net: RomNetwork, slug: str, *args) -> str:
    from .behaviour import answer
    return answer(net, slug, *args)


def _watch_in_emulator(net: RomNetwork, slug: str, *args) -> str:
    from .emulator_watch import watch
    return watch(net, slug, *args)


def _port_readiness(net: RomNetwork, slug: str) -> str:
    """The port readiness report as text (port/readiness.py)."""
    from ..port.readiness import describe, readiness
    return describe(readiness(net, slug))


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
def _release_with_experiment(net, agent, slug, function, notes="", needs="", not_c=False, move_on=False,
                             next_experiment=None, claim_token="", *, automatic=False):
    from . import session_budget

    pivot = {}
    if automatic:
        budget = session_budget.current(net, agent, slug)
        pivot['automatic_pivot'] = bool(budget and automatic_assignment.available(net, agent, slug, budget, completed=1))
    released = net.release_function(agent, function, notes, slug, needs or '', bool(not_c), bool(move_on),
                                    claim_token=claim_token, next_experiment=next_experiment, **pivot)
    return agent_tokens.brief(net, released, slug, agent)


@claim_state.quick
def _claim(net: RomNetwork, agent: Agent, slug: str, function: str, strategy: str, count: int = 1,
           fallback: bool = True, max_insns: int = 0) -> str:
    """Claim the named function or choose available work when fallback is enabled; max_insns caps the size of an
    automatically chosen one."""
    from ..network import claim_size_cap
    with claim_size_cap(max_insns):
        return _claim_capped(net, agent, slug, function, strategy, count, fallback)


def _claim_capped(net, agent, slug, function, strategy, count, fallback):
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
        if (focused or not fallback or not function
                or not re.search(r"already matched|is set aside|is claimed by|outside this agent|Max tries reached", str(e))):
            raise
        return packet(str(e) + '\n\n' + agent_tokens.brief(net, net.claim(agent, slug, None, strategy), slug, agent))


def _suggest(net, agent, slug, text='', compiler='', flags='', function='', ranges='', source='', trial_id='', review_only=False):
    if type(review_only) is not bool:
        raise NetworkError('Choose whether to keep the trial for review with a boolean.')
    if trial_id:
        if not function or any((compiler, flags, ranges, source)):
            raise NetworkError('Give the function and trial_id for a saved trial. Its compiler, flags and C come from the receipt.')
        return compiler_experiments.suggest_trial(net, agent, slug, function, trial_id, text, review_only=review_only)
    if review_only:
        raise NetworkError('Review-only compiler suggestions need an exact trial_id and its function.')
    return net.suggest(agent, slug, text, compiler, flags, function or None, ranges, source)


def _compile(net: RomNetwork, agent: Agent, slug: str, function: str, source: str,
             repair_note: str = '', repair_category: str = '', repair_automation: str = '',
             suggested_category: str = '') -> str:
    """A category can ride along with a candidate, saving a separate model turn."""
    names = [n.strip() for n in function.split(",") if n.strip()]
    if focused_agents.scope(net, agent) and len(names) != 1:
        raise NetworkError('Submit one definition for your assigned function.')
    if repair_note and len(names) != 1:
        # A small model put the function's callees and data in `function` ("func_A, func_B, D_8010...") with a
        # note, and the old "Repair notes require one function" read as being about the note: it gave up on the
        # function without compiling once. Say what was wrong with `function` and how to send it.
        listed = ', '.join(names[:4]) + (', …' if len(names) > 4 else '')
        raise NetworkError(f'`function` names {len(names)} things ({listed}): give only the one function you are '
                           'compiling, and declare its callees and data inside `source`. Nothing was compiled; '
                           'send it again. (A batch of several claimed functions takes no repair_note; use '
                           'report_repair after it.)')
    previous_diff = None
    previous_attempt = None
    if len(names) == 1:
        fn = net._function(names[0], slug)
        previous_attempt = net.project_store(slug).one('SELECT id,source FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1', fn['id'], agent.id)
        kept = net._last_diffs.get((agent.id, fn['id']))
        previous_diff = list(kept[0]) if kept else None
    saved = []
    if len(names) == 1 and (repair_category or repair_note or suggested_category):
        with submission(fn, agent.id, repair_category, repair_note, repair_automation, suggested_category) as saved:
            result = net.compile_and_diff(agent, names[0], source, slug)
    elif len(names) <= 1:
        result = net.compile_and_diff(agent, names[0] if names else function, source, slug)
    else:
        result = net.attempt_many(agent, [net.resolve_function(agent, slug, n) for n in names], source, slug)
    if len(names) == 1:
        measured = agent_tokens.outcome(net, agent, fn, source, result)
        from . import agent_evidence, guided_matching
        saved_function = net._function(names[0], slug)
        project = net.project_row(slug, sync=False)
        objective = guided_matching.observe(net, project, saved_function, agent, source, measured) if measured.get('rows') else ''
        agent_tokens.remember(net, agent, fn, previous_attempt)
        unchanged_note = None if result.startswith("🔁 REPEAT:") else agent_evidence.track(net,agent,slug,fn)
        first = not net.project_store(slug).one(
            'SELECT 1 FROM attempts a JOIN claims c ON c.function_id=a.function_id AND c.agent_id=a.agent_id '
            'WHERE c.function_id=? AND c.agent_id=? AND a.created_at>=c.claimed_at AND a.compiled=1 '
            'LIMIT 1 OFFSET 1', fn['id'], agent.id) if (fn['n_insns'] or 0) >= 128 and measured.get('rows') else False
        result = agent_tokens.feedback(net, agent, slug, names[0], result, previous_diff)
        if first and not unchanged_note and not result.startswith("🔁 REPEAT:"):
            from .efficiency_feedback import draft_triage
            if hint := draft_triage(measured, fn, net.project_row(slug)['arch']):
                result = result + '\n' + hint
        if unchanged_note:
            result += '\n'+unchanged_note
        if objective:
            result += '\n'+objective
        if measured.get('build_failed') or (not unchanged_note and measured.get('score') is not None
                and saved_function['best_score'] is not None and measured['score'] > saved_function['best_score']):
            result += '\n'+guided_matching.recovery(measured.get('rows'), [], measured, saved_function['best_score'], project['arch'])
        _, revision = agent_tokens.candidate(net, agent, slug, names[0])
        open_function = net._function(names[0],slug)['status']=='open'
        held = net.project_store(slug).one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
        if focused_agents.scope(net, agent):
            result += f"\nCandidate revision {revision}."
        elif open_function and held:
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
    for case_id in saved:
        detail = ' and your explanation' if repair_note.strip() else ' classification'
        result += f"\nRepair example #{case_id}{detail} saved for review."
    return result


def _add_note(net: RomNetwork, agent: Agent, slug: str, text: str, function: str = "") -> str:
    """A note for the next agent on one function. #25: a note without one used to become a project-wide knowledge
    proposal, which read like agreed knowledge although one model had only guessed it; that's share_knowledge's
    job when an agent means it."""
    if not function.strip():
        raise NetworkError("add_note needs the function the note is for; something every agent on this game should "
                           "know goes through share_knowledge")
    return net.add_note(agent, slug, text, function)


def _redraft(net, slug, function, context, agent=None, reference_source='', reference_url='', reference_sha256=''):
    if isinstance(context, str) and context.strip() and not re.search(r'[;{}#]', context):
        raise NetworkError('redraft context must be C declarations, not prose. Save observations with add_note; use get_function(section=candidate) to read existing C.')
    evidence = ''
    if reference_source or reference_url or reference_sha256:
        prototype, evidence = net.reference_wrapper(slug, function, reference_source, reference_url, reference_sha256)
        # Explicit declarations can refine opaque wrapper arguments and override the generated prototype.
        from .. import seeds
        generated_names = seeds.declarations(prototype)[0].names
        if not any(generated_names & declaration.names for declaration in seeds.declarations(seeds.c_only(context))):
            context = prototype + '\n' + context
    if not isinstance(context,str) or not context.strip():
        raise NetworkError('redraft needs nonempty C declarations in context (a real struct, callee prototype or extern). '
                           'To inspect existing C, use get_function(function, section="candidate"). No redraft was run.')
    draft = net.redraft(slug, function, context)
    if evidence:
        net.add_note(agent, slug, evidence, function)
        draft = evidence + '\n\n' + draft
    return agent_tokens.brief(net, draft, slug, agent)


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
    "compiler_passes": lambda net, agent, slug, function, revision, passes=None:
        compiler_passes.run(net, agent, slug, function, revision, passes),
    "score_permutations": lambda net, agent, slug, function, revision, statements=None, patches=None, max=64, marked='':
        score_permutations.run(net, agent, slug, function, revision, statements, patches, max, marked),
    "prove_chain": lambda net, agent, slug, function, revision, families=None, steps=4, children=40:
        prove_chain.run(net, agent, slug, function, revision, families, steps, children),
    "compiler_source": lambda net, agent, slug, compiler, function_name: compiler_source.run(net, agent, slug, compiler,
                                                                                             function_name),
    "get_compiler_trial": compiler_experiments.retrieve,
    "review_search_seed": reviewed_search_seeds.review,
    "get_search_brief": search_brief.read,
    "try_local_repairs": lambda net,agent,slug,function,revision,families=None,max_trials=6,anchor='',compare=False:
        local_experiments.run(net,agent,slug,function,revision,families,max_trials,anchor=anchor,compare=compare),
    "compile_family": agent_tokens.local_family,
    "family_targets": agent_tokens.family_targets,
    "get_assembly": lambda net, agent, slug, function, start=0, limit=60, focus="": agent_tokens.assembly(net, slug, function, start, limit, focus, agent),
    "claim_function": lambda net, agent, slug, function="", strategy="", count=1, fallback=True, max_insns=0:
        _claim(net, agent, slug, function, strategy, count, fallback, max_insns),
    "get_function": agent_tokens.function_view,
    "compile_and_diff": lambda net, agent, slug, **args: _compile(net, agent, slug, **args),
    "search_repair_examples": repair_search.search,
    "get_repair_examples": repair_search.details,
    "upvote_repair_example": repair_search.upvote,
    "report_repair": lambda net, agent, slug, function, change='', category='other', automation='', case_id=0, suggested_category='':
        net.repair_feedback.report(agent, slug, function, change, category, automation, case_id, suggested_category),
    "last_diff": agent_tokens.diff_page,
    "report_candidate_issue": lambda net, agent, slug, function, revision, issue, reason, alternative_revision="":
        candidate_quality.report(net,agent,slug,function,revision,issue,reason,alternative_revision),
    "release_function": _release_with_experiment,
    automatic_assignment.RELEASE: lambda net, agent, slug, **args:
        _release_with_experiment(net, agent, slug, **args, automatic=True),
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
    "get_data_unit": lambda net, agent, slug, name: _data_unit(net, slug, name),
    "compile_data": lambda net, agent, slug, name, source: _compile_data(net, slug, name, source, agent),
    "claim_port_fix": lambda net, agent, slug, function="": _port_agents().claim(net, agent, slug, function),
    "release_port_fix": lambda net, agent, slug, function, note="":
        _port_agents().release(net, agent, slug, function, note),
    "check_for_pc": lambda net, agent, slug, function, source="", edits=None: _check_for_pc(
        net, slug, function, source, edits),
    "fix_for_pc": lambda net, agent, slug, function, source="", edits=None: _fix_for_pc(
        net, agent, slug, function, source, edits),
    "claim_data": lambda net, agent, slug, name="": _data_agents().claim(net, agent, slug, name),
    "release_data": lambda net, agent, slug, name, note="": _data_agents().release(net, agent, slug, name, note),
    "guess_struct": lambda net, agent, slug, function, argument="arg0": net.guess_struct(slug, function, argument),
    "redraft": lambda net, agent, slug, function, context="", **reference: _redraft(net, slug, function, context, agent, **reference),
    "compiler_map": lambda net, agent, slug, survey=False, check_ranges=False:
        net.compiler_map_text(slug, bool(survey), bool(check_ranges)),
    "list_functions": lambda net, agent, slug, status="open", limit=40: net.list_functions(slug, status, limit),
    "port_readiness": lambda net, agent, slug: _port_readiness(net, slug),
    "compare_candidate_behaviour": _candidate_behaviour,
    "compare_behaviour": lambda net, agent, slug, function, calls=3, vis=3600, from_vi=0, input="",
        expansion_pak=False: _compare_behaviour(net, slug, function, calls, vis, from_vi, input, expansion_pak),
    "watch_in_emulator": lambda net, agent, slug, target, kind="write", vis=600, from_vi=0, input="", length=4,
        max_hits=40, expansion_pak=False: _watch_in_emulator(net, slug, target, kind, vis, from_vi, input, length,
                                                             max_hits, expansion_pak),
    "add_note": lambda net, agent, slug, text, function="": _add_note(net, agent, slug, text, function),
    "share_knowledge": lambda net, agent, slug, text="", supports=0, evidence="": net.knowledge.share(agent, slug, text, supports, evidence),
    "search_flags": lambda net, agent, slug, function, compiler="", source="":
        net.search_flags(agent, slug, function, compiler, source),
    "claim_readable": lambda net, agent, slug: net.readability.claim(agent, slug),
    "polish_function": lambda net, agent, slug, function, renames="{}", comment="", types="{}", reuse=False: polish_automation.structured(net.readability, agent, slug, function, renames, comment, types, reuse),
    "tidy_file": lambda net, agent, slug, path: polish_automation.tidy(net.readability, agent, slug, path),
    "make_readable": lambda net, agent, slug, function, source: net.readability.submit(agent, slug, function, source),
    "tidy_done": lambda net, agent, slug, unit, summary: net.readability.tidy_done(agent, slug, unit, summary),
    "suggest": _suggest,
    "try_compiler": lambda net, agent, slug, function, compiler, flags="", source="":
        net.try_compiler(agent, slug, function, compiler, flags, source),
    "search_notes": lambda net, agent, slug, query: net.search_notes(slug, query),
    "find_similar": lambda net, agent, slug, function, limit=5: net.find_similar(slug, function, limit),
    "search_learnings": lambda net, agent, slug, symptom, top=5: learnings_search.run(net, agent, slug, symptom, top),
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

READ_ONLY = frozenset({"search_repair_examples", "get_repair_examples", "get_search_brief","get_boundary_evidence", "get_claims", "resolve_addresses", "project_info", "get_function", "last_diff", "get_context", "read_data", "get_class",
                       "guess_struct", "compiler_map", "list_functions", "search_flags", "search_notes",
                       "find_similar", "search_learnings", "compiler_source", "guide", "read_file", "search_code", "file_status", "standins_for",
                       "port_readiness", "get_data_unit", "check_for_pc", "watch_in_emulator"})

# Read-only tools that read a function's saved C or the headers: they read the saved folder first (an edit made there
# is imported and judged before it's shown) and wait during a recheck for its evidence. Other reads don't.
READS_SAVED_FILES = frozenset({"get_function", "get_context"})

# Tools whose change lands in files the user may open or edit next (a header, a name everywhere, a layout): published
# before the call answers. Every other tool's change is published by the project's autosave worker.
PUBLISH_AFTER = frozenset({"edit_context", "rename_symbol", "split_function", "join_functions", "edit_file",
                           "split_file", "tidy_file", "redraft"})

# Tools whose work is judging a candidate: the check before them reads only what a judge reads.
JUDGE_TOOLS = frozenset({"compile_and_diff", "compile_patch", "compile_variants", "compile_family", "try_local_repairs",
                         "try_compiler", "compiler_experiments", "compiler_passes", "score_permutations", "prove_chain",
                         # (they read and write the database, not the saved files: release_function waited 3.2 s
                         # a call for the project's lock, behind other helpers' full checks)
                         "claim_function", "release_function", automatic_assignment.RELEASE, "add_note"})
REPAIR_CAPTURE_TOOLS = frozenset({'compile_and_diff', 'compile_patch', 'compile_variants', 'compile_family',
                                 'try_local_repairs', 'try_compiler', 'compiler_experiments', 'search_flags',
                                 'edit_file', 'edit_context', 'redraft'})


ROM_ONLY = {"watch_in_emulator","compare_behaviour","compare_candidate_behaviour","get_data_unit","compile_data","claim_data","release_data","claim_port_fix","release_port_fix","check_for_pc","fix_for_pc","claim_asset_region","inspect_asset_region","preview_asset_region","propose_asset_region","defer_asset_region","review_search_seed","get_boundary_evidence", "suggest_boundary", "get_context", "edit_context", "split_function", "redraft", "search_flags",
            # GCC pass dumps and the standalone judge worker are ROM-mode (compiler_passes.py, score_permutations.py)
            "compiler_passes", "score_permutations", "prove_chain"}
FORK_ONLY = {"read_file", "search_code", "edit_file", "file_status", "split_file", "join_functions", "tidy_done", "tidy_file",
             "standins_for"}
LTCG_ONLY = {"standins_for"}  # only projects of games built with link-time code generation have harness/ files


def _rom_joins() -> bool:
    """Whether the ROM engine can join functions. #192 adds EditMixin.join_functions, which is enough on its own;
    an engine whose handler has another name sets RomNetwork.joins_functions."""
    return bool(getattr(RomNetwork, "joins_functions", False)) or callable(getattr(RomNetwork, "join_functions", None))


def _supports(console, name: str, fork: bool) -> bool:
    """Whether `console` can do `name` in this kind of project. A tool no console lacks keeps its place."""
    if name == "join_functions":
        # Project mode's joins are the console's (Console.join_functions); a ROM's are the engine's (#192).
        return console.joins_functions if fork else _rom_joins()
    if name == "report_boundary":
        return console.reports_boundaries if fork else arch.BOUNDARIES in console.features
    if name == "compare_candidate_behaviour":
        return not fork and console.emulator is not None and console.recompiler is not None
    if name == "get_class":
        return console.reads_classes
    if name == "guess_struct":
        return console.reads_structs
    if name == "compiler_map":
        return console.file_compilers if fork else arch.COMPILER_MAP in console.features
    return True


def tool_names(fork: bool, console=None, ltcg: bool = False, scratch: bool = True) -> list[str]:
    """The tools that make sense for a project: forks and ROM projects share the core loop. A game started from
    scratch in project mode has those of ROM projects its console makes work there too (Console.own_tools: PC's
    redraft and search_flags). A tool its console can't do (a project mode's join or boundary, a processor without
    the reader for classes or structs, a compiler map), or a ROM join its engine can't make, isn't offered, so its
    prompt and schema don't either."""
    own = console.own_tools if console is not None and scratch else frozenset()
    skip = ROM_ONLY - own if fork else FORK_ONLY
    names = [n for n in HANDLERS if n not in skip and (ltcg or n not in LTCG_ONLY)]
    return [n for n in names if console is None or _supports(console, n, fork)]


def project_tools(net: RomNetwork, slug: str | None, agent=None, include_disabled=False) -> list[str]:
    """tool_names for a project (None: no project yet), from the console its settings name, less the optional tools
    Agents & models turned off for every helper or for `agent` (include_disabled: all of them, for that page)."""
    # Discovery needs the project kind, not a saved-code recheck. fork_slug() uses RomNetwork._project(), which
    # synchronizes progress even for ROM projects; two slow lists can occupy both MCP metadata workers and block
    # initialization too. Actual work tools still synchronize through the ordinary project/invocation paths.
    try:
        proj = Network._project(net, slug) if slug else None
        cfg = net.cfg(proj) if proj is not None else {}
    except NetworkError:
        return []
    if proj is None:
        names = tool_names(False)
    else:
        fork = cfg.get('kind') == 'fork'
        console = consoles.of(cfg)
        from ..forks.project import ForkProject
        manifest = (ForkProject(proj['root'], create_state=False).manifest or {}) if fork and cfg.get("origin") == "scratch" else {}
        # #256: only the tools the game's console can do (a join, a boundary report, classes, structs, a map)
        names = tool_names(fork, console if console.id or console.arch else None, bool(manifest.get("ltcg")),
                           scratch=cfg.get("origin") == "scratch")
    if include_disabled:
        return names
    from .tool_policy import visible
    return visible(net, slug, names, agent)


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
        'warnings', 'quality', 'history', 'notes', 'declarations', 'compiler', 'layouts', 'work'}
    # Polishing also judges current DB inputs and rechecks source/header/target
    # receipts before publication; claims may reuse such a verified plan.
    releasing = name in {'release_function', automatic_assignment.RELEASE}
    allowed = function_read or name in {'compile_and_diff','compile_patch','compile_variants','compile_family','try_local_repairs',
                                       'search_repair_examples','get_repair_examples','upvote_repair_example','get_search_brief',
                                       'add_note','report_repair','get_assembly','get_context','claim_readable','polish_function','make_readable'} or (
        name == 'get_candidate' and args.get('base','current') == 'current') or (
        releasing and not args.get('needs','').strip() and not args.get('not_c',False))
    notes = allowed and (releasing or name == 'add_note')
    progress = getattr(net, 'progress', None)
    from ..request_timing import timed_lock
    with progress.foreground() if allowed and progress is not None else nullcontext():
        attempt = nullcontext()
        if notes and releasing:
            # Compiles own the candidate lock before publishing under the project
            # lock. A release must use that same order, including its receipt guard.
            fn = net._function(args['function'],slug)
            store = net.project_store(fn['project_id'])
            with store.lock:
                lock = net._attempt_locks.setdefault((agent.id,fn['id']),threading.RLock())
            attempt = timed_lock(lock,'candidate_lock_wait')
        # Keep note writes and their publication marker together so a finishing
        # saved worker cannot acknowledge them before they are published.
        with attempt, timed_lock(progress._project_lock(slug), 'progress_project_lock_wait') if notes and progress is not None else nullcontext():
            if notes and releasing and net._function(args['function'],slug)['id'] != fn['id']:
                raise NetworkError('The function changed while this release waited. Read its current claim before releasing.')
            yield
            if notes and progress is not None:progress.foreground_changed(slug)


# How long a tool call waits for a save or recheck to settle before the agent is told (agents were handed "Saved files
# changed during the recheck" hundreds of times a night by the app's own saves, and gave up on their function).
PROGRESS_RETRY_SECONDS = 90


def _undisturbed(net: RomNetwork, agent: Agent, slug: str, run: Callable[[], str]) -> str:
    """One tool call. Saves, pulls and rechecks of the project are the app's business: a call they disturb waits for
    them (holding no lock) and runs again, so the agent never sees them. Every way in (MCP, the built-in agent) uses it."""
    from .progress import ProgressMoved
    deadline = time.monotonic() + PROGRESS_RETRY_SECONDS
    progress = getattr(net, 'progress', None)
    while True:
        try:
            # tolerating: a saved folder the sync refuses is the app's to report, not every tool's to fail on
            with (progress.deferring() if progress is not None else nullcontext(),
                  progress.tolerating() if progress is not None else nullcontext()):
                return run()
        except ProgressMoved:
            if time.monotonic() >= deadline:
                disturbed = net.__dict__.setdefault('_progress_disturbed', {})
                disturbed[agent.id] = disturbed.get(agent.id, 0) + 1  # (the helper's runner: not this session's fault)
                raise
            while progress is not None and slug in progress.rechecking and time.monotonic() < deadline:
                time.sleep(0.1)
            time.sleep(0.05)


def _invoke(net: RomNetwork, agent: Agent, slug: str, name: str, args: dict) -> str:
    with automatic_assignment.serialize(net, agent, slug, name):
        return _undisturbed(net, agent, slug, lambda: _invoke_once(net, agent, slug, name, args))


def _judge_preflight(name, args):
    return name in JUDGE_TOOLS or name == 'get_candidate' and args.get('base', 'current') == 'current'


def _invoke_once(net: RomNetwork, agent: Agent, slug: str, name: str, args: dict) -> str:
    """Work tools see pulled changes; the pending compact connection handshake reads saved metadata."""
    if (asset_agents.scope(net,agent) or _data_agents().scope(net,agent)
            or (_port_agents().scope(net,agent) and name != "fix_for_pc")):
        # their work isn't in the saved progress files: no sync before each call (app/data_agents.py). A kept port
        # fix is a function's C, which is: fix_for_pc syncs before and after, as any work tool
        with net.progress.view():return _dispatch(net,agent,slug,name,args)
    if boundary_investigations.scope(net,agent):
        return _dispatch(net,agent,slug,name,args)
    if (name == 'project_info' and not args.get('detail', False)
            and (agent.id, slug) in getattr(net, '_pending_tool_handshakes', set())):
        return _dispatch(net, agent, slug, name, args)
    # Enter before this wrapper's sync, not only inside the engine's attempt. Revisions are read once per call until
    # a write (Store.versions_held).
    from ..store import Store
    progress = getattr(net, 'progress', None)
    if progress is not None and (name in {'report_repair', 'upvote_repair_example'} or name in READ_ONLY and name not in READS_SAVED_FILES):
        # A read answers from the database as it is: it waited for the folder's checks and rechecks like a write
        # (read_data 601 s, resolve_addresses 188 s behind a full recheck), for nothing it reads.
        with _tool_foreground(net, agent, slug, name, args), Store.versions_held(), progress.view():
            return _dispatch(net,agent,slug,name,args)
    with _tool_foreground(net, agent, slug, name, args), Store.versions_held():
        if progress is not None:
            # (a compile reads only the manifest and the headers: Progress.check_for_judging)
            if _judge_preflight(name, args):
                # A claim never waits behind the autosave's check of the folder (Progress.check_for_claim).
                check = progress.check_for_claim if name == 'claim_function' else progress.check_for_judging
                function = args.get('function', '')
                if function and ',' not in function:
                    # Resolve on the DB snapshot, then check the held function's
                    # saved files along with the shared judge inputs.
                    with progress.view():
                        function = _named(net, agent, slug, args, name)['function']
                    check(slug, function=function)
                else:
                    check(slug)
            else:
                progress.sync(slug, force=True)
        publish = name in PUBLISH_AFTER
        before = progress.publish_token(slug) if progress is not None and publish else None
        # `after`: this call's publishing is taken care of (here, or by the project's autosave worker), so a judge
        # inside doesn't make its own check after.
        with progress.checked_around(slug, after=True) if progress is not None else nullcontext():
            result = _dispatch(net,agent,slug,name,args)
        # What a call wrote is in the database when it answers; the project's autosave worker writes it to the saved
        # folder a few seconds later, many helpers' changes at once (Progress.deferring). Each call publishing its own
        # held the project's lock: with eight helpers on a game, a third of a compile's time was spent waiting for
        # it or writing files. The tools whose change the user edits next in the folder still publish at once.
        if progress is not None and publish and not progress.unchanged(slug, before):
            progress.sync(slug, force=True)
        return result


def _repair_request(cases, *, automatic=False):
    examples = ', '.join(f'{function} (case_id={case_id})' for case_id, function in cases.items())
    when = 'Before working on the next assignment' if automatic else 'Before claiming more work'
    return f"{when}, call report_repair with function, case_id and category for {examples}. Use other if unsure."


@claim_stalls.snapshot_rules
def _dispatch(net,agent,slug,name,args, *, count_usage=True):
    asset_agents.guard(net,agent,slug,name,args)
    _data_agents().guard(net,agent,slug,name,args)
    _port_agents().guard(net,agent,slug,name,args)
    boundary_investigations.guard(net,agent,slug,name,args)
    if name not in project_tools(net, slug, include_disabled=True):
        raise NetworkError(_unoffered(net, slug, name))  # #256: an old prompt or a cached tool list
    pending = getattr(net, '_pending_tool_handshakes', set())
    handshake = (agent.id, slug) in pending
    if handshake and name != 'project_info':
        raise NetworkError('First call project_info(detail=false) to verify tools in this CLI session. No work was changed.')
    from .tool_policy import record, visible
    record(net, agent, slug, name, count=count_usage)
    if not count_usage:
        return HANDLERS[name](net, agent, slug, **_named(net, agent, slug, args, name))
    repair_key = (slug, agent.id)
    pending_repairs = getattr(net, '_pending_repair_classifications', {})
    if name == 'claim_function' and (cases := pending_repairs.get(repair_key)):
        store = net.project_store(slug)
        snapshot = cases.copy()
        remaining = {case_id: function for case_id, function in snapshot.items()
                     if store.one("SELECT 1 FROM main.repair_examples WHERE id=? AND agent_id=? "
                                  "AND status='new' AND category='unclassified'", case_id, agent.id)}
        if remaining and visible(net, slug, ['report_repair'], agent):
            raise NetworkError(_repair_request(remaining))
        for case_id in snapshot:
            cases.pop(case_id, None)
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
        if name in {'compile_and_diff', 'compile_patch', 'compile_family', 'compile_variants', 'try_local_repairs'}:
            function = named.get('function', '')
            if function and ',' not in function and net._function(function, slug)['status'] == 'matched':
                raise NetworkError(f"{function} is already matched. Claim another function, or use the polishing tools.")
        focused = focused_agents.scope(net, agent)
        if focused and name in focused_agents.TARGET_TOOLS and named.get('function'):
            net.focus_target(agent, slug, named['function'])
        automatic = budget is not None and automatic_assignment.enabled(net, agent, slug)
        before = automatic_assignment.held(net, agent, slug) if automatic and name in automatic_assignment.HANDOFF_TOOLS else None
        # A real CLI handshake confirms authenticated access, not a saved-code
        # import. Keep its compact facts on the current database snapshot so a
        # long recheck cannot prevent the CLI from establishing its connection.
        # The next work tool still synchronizes and validates its own inputs.
        compact_handshake = handshake and name == 'project_info' and not args.get('detail', False)
        tracking = track_matches(slug, agent.id) if name in REPAIR_CAPTURE_TOOLS else nullcontext([])
        with (net.progress.view() if compact_handshake else nullcontext(), tracking as matches):
            result=HANDLERS[name](net,agent,slug,**named)
        if name == 'claim_function' and isinstance(result, str):
            from .claim_state import claimed_name
            result += focused_agents.convert(net, agent, slug, claimed_name(net, agent, slug, named))
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
        if matches and visible(net, slug, ['report_repair'], agent):
            cases = dict(matches)
            net.__dict__.setdefault('_pending_repair_classifications', {}).setdefault(repair_key, {}).update(cases)
            result += '\n' + _repair_request(cases, automatic=automatic)
        if focused and name in focused_agents.CANDIDATE_TOOLS and named.get('function'):
            fn = net._function(named['function'], slug)
            limit, used = net.attempt_limit(agent, fn)
            result += f'\nFocused tries used: {used}/{limit}.'
        if automatic:
            result = automatic_assignment.after_tool(net, agent, slug, budget, before, name, handshake, result)
        return result
    finally:
        if budget:
            budget.end(result or '')


def _unoffered(net: RomNetwork, slug: str, name: str) -> str:
    """Why an old prompt or a cached tool list calling `name` is answered instead of run."""
    console = consoles.of(net.cfg(net._project(slug)))
    where = f"{console.name} projects" if console.name else "this project"
    subject = "its console"
    why = {"join_functions": "can't join functions yet", "report_boundary": "can't report function ends yet",
           "get_class": "can't read C++ classes yet", "guess_struct": "can't read struct layouts yet",
           "compiler_map": "can't read a compiler map yet"}.get(name)
    if name == "join_functions" and not net.fork_slug(slug):
        subject, why = "the ROM engine", "can't join functions yet"  # ROM joins are the engine's (#192), not a console's
    return (f"{name} isn't offered in {where}: " + (f"{subject} {why}" if why else "use the tools you were given")
            + " (ROADMAP.md says what's planned).")



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


# Tools that read, claim or note without compiling: they share a lane no compile can fill.
QUICK_TOOLS = frozenset({
    'guide', 'project_info', 'get_claims', 'list_functions', 'get_function', 'get_assembly', 'get_context',
    'last_diff', 'search_notes', 'search_learnings', 'read_file', 'search_code', 'file_status', 'get_class', 'compiler_map',
    'get_candidate', 'get_compiler_trial', 'get_search_brief', 'family_targets', 'get_boundary_evidence',
    'resolve_addresses', 'read_data', 'standins_for', 'claim_function', 'claim_readable', 'release_function', automatic_assignment.RELEASE,
    'search_repair_examples', 'get_repair_examples', 'upvote_repair_example',
    'add_note', 'report_repair', 'share_knowledge', 'suggest', 'tidy_done', 'claim_asset_region', 'defer_asset_region',
    'inspect_asset_region', 'report_data_region', 'report_boundary', 'suggest_boundary', 'port_readiness', 'get_data_unit', 'claim_data', 'release_data',
    'claim_port_fix', 'release_port_fix',
})
QUICK_LANE = 16
WORK_LANE = 32


def build_mcp(net: RomNetwork, agent_of: Callable[[Context], tuple[Agent, str]], observe=None, reader=None) -> MCPServer:
    """MCP server for CLI agents. `agent_of` maps a request to (agent, project slug). `reader` (ctx, name, args) ->
    text answers every call instead, in the read-only tool process (app/tool_reader.py)."""
    if reader is None:
        from .tool_policy import ensure
        ensure(net)
    mcp = MCPServer(name="decomp", title="decompdecomp", instructions=INSTRUCTIONS, version=__version__)

    health_limiter = anyio.CapacityLimiter(2)
    # Tool calls have threads of their own, apart from the window's (anyio's shared default of 40), and quick
    # tools have their own lane: a burst of compiles can't make guide or get_claims wait (MCP error -32001).
    quick_limiter = anyio.CapacityLimiter(QUICK_LANE)
    work_limiter = anyio.CapacityLimiter(WORK_LANE)
    async def run(ctx: Context, name: str, /, **args) -> str:
        if reader is not None:
            return await anyio.to_thread.run_sync(lambda: reader(ctx, name, args), limiter=quick_limiter)
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
                    elif _judge_preflight(name, args) or name == 'get_search_brief':
                        # Candidate tools use the same single scoped check in both transports.
                        result = _invoke(net, agent, slug, name, args)
                    else:
                        def once():
                            with _tool_foreground(net, agent, slug, name, args):
                                return _dispatch(net, agent, slug, name, args)
                        with automatic_assignment.serialize(net, agent, slug, name):
                            result = _undisturbed(net, agent, slug, once)
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
            from . import tool_latency
            tool_latency.record(net, agent.name, slug, name, timings, error is not None)
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
        lane = health_limiter if checking else quick_limiter if name in QUICK_TOOLS else work_limiter
        return await anyio.to_thread.run_sync(execute,limiter=lane)

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
    async def inspect_asset_region(ctx: Context, region: str, claim_token: str, offset: int | None = None, length: int = 256, evidence_path: str | None = None, evidence_offset: int = 0) -> str:
        """Read up to 4096 original bytes or follow an evidence path for a held region."""
        return await run(ctx,"inspect_asset_region",region=region,claim_token=claim_token,offset=offset,length=length,evidence_path=evidence_path,evidence_offset=evidence_offset)

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
    async def claim_function(ctx: Context, function: str = "", strategy: str = "", count: int = 1,
                             fallback: bool = True, max_insns: int = 0) -> str:
        """Claim the next function to decompile (or a specific one by name or address).

        Returns a lightweight receipt with saved candidate C and claim token. No baseline compile, new draft or
        imported build is run while claiming. Read get_assembly or get_function(section=brief) for supporting
        evidence; get_candidate rechecks a baseline. get_claims recovers held work. Each compile renews the claim.

        Args:
            function: optional name or address (0x80001234) of a specific function
            strategy: no_compiling_c (only functions without a recorded successful C compilation), untried (no compile attempts first), finish (forks' default: the files closest to being linked first), easiest (fewest
                instructions; ROM projects' default), closest (nearly matched already, relative to size), nearest
                (lowest saved score first), hardest, random
            count: up to 6: small functions come as a batch (neighbours in one file) to write together and
                judge in one compile_and_diff; a big one comes alone
            fallback: allow another assignment if the named function is unavailable (default true).
                Set false when the task requires only the named function.
            max_insns: when choosing automatically, only functions of at most this many instructions (0: any size)
        """
        options = {} if fallback else {"fallback": False}
        if max_insns:
            options["max_insns"] = max_insns
        return await run(ctx, "claim_function", function=function, strategy=strategy, count=count, **options)

    @tool
    async def get_function(ctx: Context, function: str, section: str = 'summary', start: int = 0, limit: int = 6000) -> str:
        """Show any function (by name or address): assembly, status, best attempt, notes. Read matched
        functions to learn how this codebase is written. section: summary (default), work (one objective, complete current C, mismatch and recent measurements), brief, all, history, candidate, experiment, quality, notes, declarations, compiler, layouts. start/limit page supporting sections by character offset; candidate/brief always return complete C. Prefer the specific section you need."""
        return await run(ctx, "get_function", function=function, section=section, start=start, limit=limit)

    @tool
    async def compile_and_diff(ctx: Context, function: str, source: str, repair_note: str = '',
                               repair_category: str = '', repair_automation: str = '', suggested_category: str = '') -> str:
        """Submit a whole-function rewrite and judge it against the original. For small edits use compile_patch to avoid resending C; score 0 = byte-identical.

        ROM projects: compiled with the game's compiler (context.h included), linked at the function's
        address and compared with the ROM. Forks: send the complete definition (plus any #include "...",
        prototypes, externs or #pragma lines it needs); it replaces the function in its source file, which is
        built with the project's build system and diffed with objdiff. Several functions ("a, b, c"): send all
        their definitions in one source; each is judged on its own."""
        return await run(ctx, "compile_and_diff", function=function, source=source, repair_note=repair_note,
                         repair_category=repair_category, repair_automation=repair_automation, suggested_category=suggested_category)

    @tool
    async def get_claims(ctx: Context) -> str:
        """Recover your live claims, canonical function names and claim tokens in this project. Read-only; no builds, drafting, new claims or source selection. Use get_candidate for C/revision."""
        return await run(ctx, "get_claims")

    @tool
    async def get_candidate(ctx: Context, function: str, base: Literal["current", "saved_best", "last_compiling", "reported_corrected", "regional_alternative", "measured_option"] = "current", option: int = 0) -> str:
        """Read your current candidate C/revision and bounded advisory C-line mismatch locations, when compiler debug information is available. Use base="saved_best" to explicitly restore saved best C, base="reported_corrected" to select a compiled alternative explicitly linked to a quality report (an agent hypothesis, not verified correctness), or base="last_compiling" to recover your last scored C after compiler errors. Selection changes only your continuation; historical scores require rejudging. No full assembly listing or historical notes."""
        return await run(ctx, 'get_candidate', function=function, base=base, option=option)

    @tool
    async def compile_patch(ctx: Context, function: str, revision: str, old: str, new: str, repair_note: str = '',
                            repair_category: str = '', repair_automation: str = '', occurrence: int = 0,
                            suggested_category: str = '') -> str:
        """Compile one exact-text replacement in your versioned candidate. Use occurrence=1-based index for repeated old text; default 0 requires a unique anchor. Stale revisions are refused.
        Uses the ordinary compiler, claim limits and exact judge; no files bypass the build checks."""
        return await run(ctx, 'compile_patch', function=function, revision=revision, old=old, new=new, occurrence=occurrence,
                         repair_note=repair_note, repair_category=repair_category, repair_automation=repair_automation,
                         suggested_category=suggested_category)

    @tool
    async def compile_variants(ctx: Context, function: str, revision: str, patches: list[dict], combine: bool = False) -> str:
        """Try 1–6 independent old/new replacements against one candidate revision. Each patch can specify occurrence=1-based index for repeated old text. Normal judge and limits.
        Stops on match or claim error; returns a compact table and current revision. Cached or rejected
        trials may retain earlier C. Each patch starts from the original snapshot. With combine=true, send
        2–5 patches and reserve one trial for nonoverlapping edits that improve different target instructions."""
        return await run(ctx, 'compile_variants', function=function, revision=revision, patches=patches, combine=combine)

    @tool
    async def compiler_passes(ctx: Context, function: str, revision: str, passes: list[str] | None = None) -> str:
        """Near-matches: dump 1-4 of the compiler's own passes for your candidate revision (rtl, jump, cse, loop,
        cse2, flow, combine, sched, lreg, greg, sched2, jump2, dbr; default jump, greg, dbr) and its register
        allocation priorities. GCC-family compilers in ROM projects; diagnostic only, records nothing."""
        return await run(ctx, 'compiler_passes', function=function, revision=revision, passes=passes)

    @tool
    async def score_permutations(ctx: Context, function: str, revision: str, statements: list[str] | None = None,
                                 patches: list[dict] | None = None, max: int = 64, marked: str = '') -> str:
        """Judge many candidates at once with the exact judge: every order of 2-6 statements (exact whole lines of
        your candidate), every combination of 2-6 old/new patches (up to 64), or every combination marked in a full
        source (`marked`: /*{*/a/*|*/b/*}*/ alternatives, /*permute{*/ one statement per line /*}permute*/; up to
        2000, 5 minutes). Only a candidate beating the saved best is recorded; else nothing, no move-on count. ROM projects."""
        return await run(ctx, 'score_permutations', function=function, revision=revision, statements=statements,
                         patches=patches, max=max, marked=marked)

    @tool
    async def prove_chain(ctx: Context, function: str, revision: str, families: list[str] | None = None,
                          steps: int = 4, children: int = 40) -> str:
        """Would the repair library reach the match from here? Applies the best child of the named families (default
        all) step by step, judged with the exact judge (at most 8 steps, 80 children a step, 2 minutes). Only a final
        source beating the saved best is recorded; otherwise nothing, and no move-on count. ROM projects."""
        return await run(ctx, 'prove_chain', function=function, revision=revision, families=families, steps=steps,
                         children=children)

    @tool
    async def compiler_source(ctx: Context, compiler: str, function_name: str) -> str:
        """Read one function or macro of a compiler pass from the compiler's installed source (e.g. "gcc-2.7.2-kmc",
        "birthing_insn_p"; sched.c, local-alloc.c, global.c, loop.c...): the rule behind your last rows. Bounded."""
        return await run(ctx, 'compiler_source', compiler=compiler, function_name=function_name)

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
    async def get_search_brief(ctx: Context, function: str, base: Literal["current", "saved_best"] = "current") -> str:
        """Read a compact current-input packet: allocation source lines/priorities, negative experiments, reviewed roots and source references. No compiler runs or source adoption."""
        return await run(ctx,'get_search_brief',function=function,base=base)

    @tool
    async def try_local_repairs(ctx: Context, function: str, revision: str, families: list[str] | None = None, max_trials: int = 6, anchor: str = '', compare: bool = False) -> str:
        """Try applicable local repairs against one revision; at most six normal compiles and 60 seconds. Use compare=true to measure at most three options and choose a continuation. Omit families to discover repairs. Give an exact unique C anchor to restrict edits to that span. Includes local_widening, callee_parameter_width, callee_return_width, split_integer_lifetime, merge_integer_lifetimes, branch_call, goto_leaf and switch_layout. Retain best continuation; use instead of parallel family probes."""
        return await run(ctx,'try_local_repairs',function=function,revision=revision,families=families,max_trials=max_trials,anchor=anchor,compare=compare)

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
    async def search_repair_examples(ctx: Context, category: str, function: str = '') -> str:
        """Find up to 12 short summaries of verified repairs by category in this project. A single live claim gives
        compiler and mismatch context automatically. Optionally choose a function. Open useful IDs with get_repair_examples."""
        return await run(ctx, 'search_repair_examples', category=category, function=function)

    @tool
    async def get_repair_examples(ctx: Context, ids: list[int]) -> str:
        """Read compiler settings and short before/after diff excerpts for 1–6 selected repair example IDs."""
        return await run(ctx, 'get_repair_examples', ids=ids)

    @tool
    async def upvote_repair_example(ctx: Context, id: int) -> str:
        """Mark a repair example that helped your work. Repeated votes by the same agent count once."""
        return await run(ctx, 'upvote_repair_example', id=id)

    @tool
    async def report_repair(ctx: Context, function: str, change: str = '', category: str = 'other',
                            automation: str = '', case_id: int = 0, suggested_category: str = '') -> str:
        """Classify your verified repair. Pass case_id from the match result and a category. For other, optionally
        suggest a short new category. Change and automation notes are optional."""
        return await run(ctx, 'report_repair', function=function, change=change, category=category,
                         automation=automation, case_id=case_id, suggested_category=suggested_category)

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
    async def release_function(ctx: Context, function: str, notes: str = "", needs: str = "", not_c: bool = False, move_on: bool = False, next_experiment: dict | None = None, claim_token: str = "") -> str:
        """Give up a function you're stuck on, once compile_and_diff says to move on. Your best attempt is kept.
        Leave a brief handoff only if you learned something. Released before that (fewer compiles than
        the move-on rule), it answers once with what to try instead; call it again to release anyway. A release with
        no compile since you claimed it is not taken, however often you ask: compile first, or end your session.
        After the 45-minute hand-off clock (45 minutes on the claim and since your last improvement) and an add_note
        hand-off, the release goes through whatever the compile count says.

        Args:
            function: the function you hold
            notes: optional. Describe the remaining assembly difference, what you tried and the judged result
                (especially failed approaches worth avoiding), and one untested "Next step: ..." if you have one.
                Required for move_on and not_c, which need evidence.
            claim_token: always pass the token from the current claim, continuation state or get_candidate, including the first release and retries. A stale token cannot release a new claim.
            next_experiment: stalled-claim handoff with revision, tried, changed and title (one untested next step).
                Optionally include old and new together for a precise untested patch. Use the candidate the next
                agent will inherit. Current handoffs appear in their claim and get_function(section="experiment").
            needs: optional: a direct callee recorded in the original assembly that blocks this function. Paused
                candidates cannot be selected this way. Explain indirect/inline/header concerns in notes instead.
            move_on: explicitly release now with precise notes explaining why further exploration is unproductive. Skips the one-time release pushback; does not mark a match or no-C verdict.
            not_c: true only with evidence no C makes its code at all (hand-written assembly), saying why in notes. Unsupported compiler extensions, asm-register/tool restrictions, return-address builtins or unsuitable build flags are not proof; describe those as blockers and release normally:
                once agents of two models agree, it's set aside and no one is handed it again.
        """
        return await run(ctx, "release_function", function=function, notes=notes, needs=needs, not_c=not_c, move_on=move_on, next_experiment=next_experiment, claim_token=claim_token)

    @tool
    async def release_and_continue(ctx: Context, function: str, notes: str = "", needs: str = "", not_c: bool = False,
                                   move_on: bool = False, next_experiment: dict | None = None, claim_token: str = "") -> str:
        """Release your current function with optional findings and receive your next assignment when eligible. Remember to
        pass the current claim_token. All other arguments and release rules are the same as release_function."""
        # The gateway can acknowledge release_function before it finishes. This
        # tool needs the real response, which also contains the next brief.
        return await run(ctx, automatic_assignment.RELEASE, function=function, notes=notes, needs=needs,
                         not_c=not_c, move_on=move_on, next_experiment=next_experiment, claim_token=claim_token)

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
    async def get_data_unit(ctx: Context, name: str) -> str:
        """One of the game's data symbols, for writing it as C (docs/PORTING.md): its address, how far it runs (to the
        next place the game names or refers to), its bytes, who uses it, and any C it has."""
        return await run(ctx, "get_data_unit", name=name)

    @tool
    async def claim_data(ctx: Context, name: str = "") -> str:
        """Data agents: take the next data unit to write as C (or the one named): its address, extent, bytes, users."""
        return await run(ctx, "claim_data", name=name)

    @tool
    async def release_data(ctx: Context, name: str, note: str = "") -> str:
        """Data agents: give a unit back with what you learned, for the next try."""
        return await run(ctx, "release_data", name=name, note=note)

    @tool
    async def claim_port_fix(ctx: Context, function: str = "") -> str:
        """Port-fix agents: take the next matched function to fix for the PC (or the one named): its C and what a
        64-bit PC compiler says of it."""
        return await run(ctx, "claim_port_fix", function=function)

    @tool
    async def release_port_fix(ctx: Context, function: str, note: str = "") -> str:
        """Port-fix agents: give a function back with what you learned, for the next try."""
        return await run(ctx, "release_port_fix", function=function, note=note)

    @tool
    async def check_for_pc(ctx: Context, function: str, source: str = "", edits: list[dict] | None = None) -> str:
        """What a 64-bit PC compiler (PORT defined) says of C for a matched function, with the header it compiles
        with. Nothing is judged or saved. Give the whole C as `source`, or `edits` ([{"old", "new"}], each `old`
        once in its C) for a long function."""
        return await run(ctx, "check_for_pc", function=function, source=source, edits=edits)

    @tool
    async def fix_for_pc(ctx: Context, function: str, source: str = "", edits: list[dict] | None = None) -> str:
        """Judge a matched function's C fixed for the PC: kept only when the console build still matches byte for
        byte and the PC build is no worse on errors and pointer-size warnings and better on one. Give the whole C as
        `source`, or `edits` ([{"old", "new"}], each `old` once in its C) for a long function."""
        return await run(ctx, "fix_for_pc", function=function, source=source, edits=edits)

    @tool
    async def compile_data(ctx: Context, name: str, source: str) -> str:
        """Judge C that defines one of the game's data symbols: kept only when it holds the game's bytes at its
        address, all of the datum, pointers by name (`void *D_x[2] = {func, &D_y};`)."""
        return await run(ctx, "compile_data", name=name, source=source)

    @tool
    async def redraft(ctx: Context, function: str, context: str = '', reference_source: str = '',
                      reference_url: str = '', reference_sha256: str = '') -> str:
        """Pass C declarations or an N64Recomp wrapper’s GitHub commit permalink and required ROM SHA-256.
        Supply reference_source to use pasted source instead of fetching the file.
        Reference findings are saved as a function note after drafting. Review inferred types before judging.
        m2c's machine draft of a function made again with your declarations (a struct, a prototype, an extern
        you worked out) added to the shared header's; yours replace the header's where they declare the same
        names. Returns the new draft, recorded nowhere: try it with compile_and_diff, and edit_context what helped.
        PC games: Ghidra's draft made compilable again with them (a class declaring the function, a callee's
        prototype)."""
        return await run(ctx, "redraft", function=function, context=context, reference_source=reference_source,
                         reference_url=reference_url, reference_sha256=reference_sha256)

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
    async def port_readiness(ctx: Context) -> str:
        """What still stands between this game and a native PC port (docs/PORTING.md): code without C, library
        code, graphics microcode, data reached by address, addresses written as numbers, raw blobs. Read-only."""
        return await run(ctx, "port_readiness")

    @tool
    async def compare_candidate_behaviour(ctx: Context, function: str, revision: str, calls: int = 2,
                                          vis: int = 3600, input: str = '') -> str:
        """Compare the current, already compiled open candidate on 1–3 emulator-recorded N64 calls.
        Returns the first observable return or memory difference per call. Diagnostic only. Repeat
        the same request if the isolated build is still running."""
        return await run(ctx, 'compare_candidate_behaviour', function=function, revision=revision,
                         calls=calls, vis=vis, input=input)

    @tool
    async def compare_behaviour(ctx: Context, function: str, calls: int = 3, vis: int = 3600, from_vi: int = 0,
                                input: str = "", expansion_pak: bool = False) -> str:
        """Does a function behave like the original once built by another compiler? Records its first `calls` real
        calls (1-8) in the console's reversing emulator from your ROM (N64 for now), runs each through its
        recompiled C (the original's instructions) and through its decompiled C as the port builds it, and compares
        $v0/$v1/$f0 and the memory each leaves, named by symbol. For matched C that a port runs differently (a
        result from a void function, a wrong prototype or type). vis/from_vi/input as in watch_in_emulator: the game
        must reach the function (without input it sits at its title). The first run builds a lot: it answers
        "still running"; call again with the same arguments. Changes nothing in the project."""
        return await run(ctx, "compare_behaviour", function=function, calls=calls, vis=vis, from_vi=from_vi,
                         input=input, expansion_pak=expansion_pak)

    @tool
    async def watch_in_emulator(ctx: Context, target: str, kind: str = "write", vis: int = 600, from_vi: int = 0,
                                input: str = "", length: int = 4, max_hits: int = 40,
                                expansion_pak: bool = False) -> str:
        """Run the user's own ROM in its console's reversing emulator (N64 for now) and see what the real game does:
        kind="write": which code writes target (a data symbol or hex address; length bytes from it), with its
        caller; kind="call": each call of target (a function), from where, its a0-a3 and the words a0 points at.
        For C that matches but behaves differently in a port, or values a function really gets. vis: how far to run,
        in vertical interrupts (60 a second); from_vi: report hits from then on. input: controller script,
        "vi:BUTTON+BUTTON,vi:..." (A B Z START L R UP DOWN LEFT RIGHT CU CD CL CR, SX=n SY=n for the stick; a
        game drawing 30 frames a second takes 2 VIs a frame); without it a game sits at its title. A long run
        answers "still running": call again with the same arguments. Changes nothing."""
        return await run(ctx, "watch_in_emulator", target=target, kind=kind, vis=vis, from_vi=from_vi, input=input,
                         length=length, max_hits=max_hits, expansion_pak=expansion_pak)

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
    async def suggest(ctx: Context, text: str = "", compiler: str = "", flags: str = "", function: str = "",
                      ranges: str = "", source: str = "", trial_id: str = "", review_only: bool = False) -> str:
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
            trial_id: exact local ROM compiler_experiments receipt. Give function and omit compiler, flags, ranges and source.
            review_only: keep an exact trial as a pending suggestion even when automatic compiler changes are enabled.
        """
        return await run(ctx, "suggest", text=text, compiler=compiler, flags=flags, function=function, ranges=ranges,
                         source=source, trial_id=trial_id, review_only=review_only)

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
    async def search_learnings(ctx: Context, symptom: str, top: int = 5) -> str:
        """The recorded compiler-matching evidence, ranked by a symptom ("register swap", "store too early", "frame
        size", "hoisted constant"): each entry's fix and the repair families that make it (try them with prove_chain)."""
        return await run(ctx, "search_learnings", symptom=symptom, top=top)

    @tool
    async def guide(ctx: Context, topic: str = "") -> str:
        """Decompilation know-how. Call without a topic for the list (workflow, near-match, mips, gcc-kmc, ido,
        m2c, data, structs, n64, naming)."""
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
