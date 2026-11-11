"""Agents that fix the game's C for the PC (docs/PORTING.md, phase 4): a task like polishing, started from Start
agents once Compile for PC (port/pc_compile.py) has listed what a PC build refuses.

Each agent claims one function at a time (claim_port_fix), sees its C and what the PC compiler said of it, tries
fixes (check_for_pc shows the PC's verdict alone), and submits one (fix_for_pc), which is kept only when the
console build still matches byte for byte and the PC build is no worse on errors or pointer-size warnings and
better on one (port/port_fix.py): a big function is fixed a group of lines at a time, and the agent keeps it. A function it can't fix goes back with a note (release_port_fix). The queue is the functions
that don't compile first, then those that cast pointers to 32-bit integers, fewest tries first; a claim is a lease
on the function's row of the PC results, so a stopped agent's function comes back on its own. Port-fix agents see
only these tools, and fix only the function they hold.

Reads happen inside `net.progress.view()`: claims aren't part of the saved progress, and a sync before each one
takes seconds on a big project (app/data_agents.py says how that made starting an agent take 35 seconds).
"""

from __future__ import annotations

import time

from ..network import NetworkError
from ..port import pc_compile, port_fix

TASK = "port"
TOOLS = frozenset({"project_info", "claim_port_fix", "release_port_fix", "check_for_pc", "fix_for_pc", "get_function",
                   "get_context", "edit_context", "guess_struct", "get_class", "guide"})
REQUIRED = frozenset({"project_info", "claim_port_fix", "fix_for_pc", "release_port_fix"})
LEASE = 1800  # seconds a claim lasts without a judged fix
PROMPT = (
    "You are a decompdecomp helper fixing the C of {title}, a game the user owns, so it also builds for a PC: the "
    "first step of a port. Every function you get is matched: its C compiles to the game's exact bytes with the "
    "console's compiler. A fix is kept only when that is still true AND a current clang building for a 64-bit PC "
    "finds it no worse on errors and pointer-size warnings and better on one (fix_for_pc judges both), so a mistake "
    "costs nothing and a big function can be fixed a group of lines at a time: don't skip a function for its size. "
    "The game's code is private: never publish it.\n"
    "Loop: claim_port_fix() gives you a function, its C and what the PC compiler says of it. Fix the cause, not the "
    "warning: a pointer kept in an s32/u32 becomes a pointer type (in the variable, the parameter, the struct "
    "field); a call to an undeclared function gets its prototype in the header (edit_context; matched functions "
    "must still match; when the header alone fixes it, fix_for_pc the C as it is); an integer passed as a pointer "
    "gets the right type. When a pointer type changes the console's instructions (operand order, registers), don't "
    "fight the old compiler: write the pointer-typed lines inside `#ifdef PORT`, the original lines in `#else`, "
    "`#endif` (the console build doesn't define PORT); the same for MIPS asm and gcc-only builtins. Never cast a "
    "pointer to a 32-bit integer (s32/u32) to silence a warning; uintptr_t is the PC's pointer-sized integer, for "
    "words that hold addresses (a display list's). check_for_pc(function, source) shows the PC's verdict alone; "
    "fix_for_pc(function, source) judges both and keeps it. When nothing is left of it, claim the next. If after a few tries "
    "it won't pass, release_port_fix(function, note) with what you learned and claim another.\n"
    "Read guide(\"port\") once. Work up to {count} functions this session, then end; the app continues the queue. "
    "If claim_port_fix says nothing is left, end the session."
)


def _project(net, slug):
    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError("Fixing C for the PC reads projects decompdecomp split from a ROM (ROADMAP.md, Ports).")
    return proj


def configure(net, slug, options, count):
    """A port task needs the PC check's results to work from."""
    if options.get("task") != TASK:
        return options
    with net.progress.view():
        summary = pc_compile.summary(net, _project(net, slug))
    if summary is None:
        raise ValueError("Compile the game's C for a PC first (Port readiness → Compile for PC): port-fix agents "
                         "work through what that check lists.")
    if not summary["failed"] and not summary["warned"]:
        raise ValueError("The last PC check found nothing to fix: every function compiles for a PC without "
                         "pointer-size warnings.")
    return options


def bind(net, session, agent):
    if session.options.get("task") == TASK:
        net.__dict__.setdefault("_port_agents", {})[agent.id] = session


def scope(net, agent):
    return getattr(net, "_port_agents", {}).get(agent.id) if agent is not None else None


def _held(net, proj, agent) -> set[str]:
    return {r["name"] for r in pc_compile._store(net, proj).all(
        "SELECT name FROM port_compile WHERE project_id=? AND agent_id=? AND lease>?", proj["id"], agent.id,
        time.time())}


def guard(net, agent, slug, name, args):
    """Keep port-fix agents to their tools, and their fixes to the function they hold."""
    if not scope(net, agent):
        return
    if name not in TOOLS:
        raise NetworkError(f"Port-fix agents fix the game's C for the PC: {name} isn't one of their tools.")
    if name == "fix_for_pc":
        proj = _project(net, slug)
        function = net._function(str(args.get("function", "")), slug)["name"]
        if function not in _held(net, proj, agent):
            raise NetworkError("Claim this function first (claim_port_fix), then fix it.")


def queue(net, proj) -> list[str]:
    """The functions left, in the order agents take them (claimed ones included): those that don't compile, then
    those that cast pointers, fewest tries first."""
    matched = {r["name"] for r in net.project_store(proj["id"]).all(
        "SELECT name FROM functions WHERE project_id=? AND status='matched' AND gone_mark IS NULL", proj["id"])}
    rows = pc_compile._store(net, proj).all(
        "SELECT name, status, attempts FROM port_compile WHERE project_id=? AND status IN ('failed', 'warned')",
        proj["id"])
    return [r["name"] for r in sorted((r for r in rows if r["name"] in matched),
                                      key=lambda r: (r["attempts"] or 0, r["status"] != "failed", r["name"]))]


def left(net, slug) -> int:
    with net.progress.view():
        return len(queue(net, _project(net, slug)))


def claim(net, agent, slug, function: str = "") -> str:
    """Give the agent a function: the one it names, or the next one nobody holds. Holding one, it gets that back."""
    with net.progress.view():
        proj = _project(net, slug)
        order = queue(net, proj)
        if function:
            name = net._function(function, slug)["name"]
            if name not in order:
                raise NetworkError(f"`{name}` has nothing left to fix for the PC: claim_port_fix() gives the next.")
            wanted = [name]
        else:
            held = _held(net, proj, agent)
            wanted = [n for n in order if n in held] or order
        now = time.time()
        with pc_compile._store(net, proj).tx() as db:
            taken = {r["name"] for r in db.execute(
                "SELECT name FROM port_compile WHERE project_id=? AND agent_id IS NOT NULL AND agent_id!=? AND lease>?",
                (proj["id"], agent.id, now)).fetchall()}
            name = next((n for n in wanted if n not in taken), None)
            if name is None:
                return ("Another agent holds that function." if function else
                        "Nothing is left to claim: every function is fixed or held by another agent. End the session.")
            db.execute("UPDATE port_compile SET agent_id=NULL, lease=NULL WHERE project_id=? AND agent_id=? AND name!=?",
                       (proj["id"], agent.id, name))
            db.execute("UPDATE port_compile SET agent_id=?, lease=? WHERE project_id=? AND name=?",
                       (agent.id, now + LEASE, proj["id"], name))
        return f"Claimed {name} ({len(order)} functions left).\n" + brief(net, proj, name)


def brief(net, proj, name: str) -> str:
    """What an agent needs to fix a function: its C, what the PC said of it, and a previous try's note."""
    fn = net._function(name, proj["slug"])
    row = pc_compile._store(net, proj).one("SELECT note FROM port_compile WHERE project_id=? AND name=?",
                                           proj["id"], name)
    found = pc_compile.function(net, proj, name) or []
    text = (f"What a 64-bit PC compiler says of it now (line numbers are the C's own):\n{port_fix.render(found)}\n"
            f"Its C (matches the console byte for byte):\n```c\n{fn['matched_source'].strip()}\n```")
    if fn["unit"]:
        text += f"\nIt compiles with the header of unit {fn['unit']} (get_context)."
    if row is not None and row["note"]:
        text += f"\nA previous try left this note:\n{row['note']}"
    return text


def release(net, agent, slug, function: str, note: str = "") -> str:
    """Give a function back, with what was learned. It counts as a try, so the next claim is another function."""
    with net.progress.view():
        proj = _project(net, slug)
        name = net._function(function, slug)["name"]
        with pc_compile._store(net, proj).tx() as db:
            db.execute("UPDATE port_compile SET agent_id=NULL, lease=NULL, note=COALESCE(NULLIF(?, ''), note), "
                       "attempts=attempts+1 WHERE project_id=? AND name=? AND (agent_id=? OR agent_id IS NULL)",
                       (note[:2000], proj["id"], name, agent.id))
    return f"Released {name}. claim_port_fix() for the next function."


def fixed(net, agent, slug, function: str, result: dict) -> str:
    """After a judged fix: it counts as the session's work, a kept one frees the function, a refused one renews the
    lease."""
    text = result["text"]
    if not scope(net, agent):
        return text
    counts = net.__dict__.setdefault("_session_diagnostic_compiles", {})
    counts[agent.id] = counts.get(agent.id, 0) + 1
    if result["kept"] and port_fix._tally(result["found"]) == (0, 0):
        return text + "\nclaim_port_fix() for the next function."
    with net.progress.view():
        proj = _project(net, slug)
        with pc_compile._store(net, proj).tx() as db:
            db.execute("UPDATE port_compile SET agent_id=?, lease=? WHERE project_id=? AND name=? "
                       "AND (agent_id=? OR agent_id IS NULL)",
                       (agent.id, time.time() + LEASE, proj["id"], net._function(function, slug)["name"], agent.id))
    if result["kept"]:  # progress on a big function: it's still the agent's, with what's left
        return (text + "\nYou still hold it: fix what's left the same way, or release_port_fix(function, note) "
                "with where you got to.")
    return text


def prompt(net, session) -> str:
    from .agent_startup import project_facts
    title = project_facts(net, session.slug).get("title") or session.slug
    text = PROMPT.format(title=title, count=session.options.get("per_session", 8))
    if wish := net.instructions(session.slug):
        text += f"\nThe user's instructions for this project (they come first):\n{wish}\n"
    return text
