"""Agents that write the game's data as C (docs/PORTING.md, phase 1): a task like polishing, started from Start
agents. Each claims one data unit (app/data_units.py) at a time, writes its definition and has it judged by its
bytes (compile_data); a match frees the claim, and a unit it can't write goes back with a note for the next try.

The queue is every place in the game's initialised data its own code refers to, in address order, but a place inside
a unit already in C (a struct's field): fewest tries first, so a hard datum doesn't hold up the rest. Claims are a
lease on the unit's row, so a stopped agent's unit comes back on its own. Data agents see only these tools.

Everything here reads the project inside `net.progress.view()`: outside it, each read first syncs the project's
saved progress (seconds on a big project, a minute right after the app starts), and data units aren't part of
that saved progress. Starting a data agent took 35 seconds at "Validating worker selection" that way.
"""

from __future__ import annotations

import time

from ..network import NetworkError
from . import data_units

TASK = "data"
TOOLS = frozenset({"project_info", "claim_data", "release_data", "get_data_unit", "compile_data", "read_data",
                   "resolve_addresses", "guess_struct", "get_function", "get_context", "edit_context", "guide"})
REQUIRED = frozenset({"project_info", "claim_data", "compile_data", "release_data"})
LEASE = 1800  # seconds a claim lasts without a compile: a stopped agent's unit comes back
PROMPT = (
    "You are a decompdecomp helper writing the data of {title}, a game the user owns, as C for a port. Every unit is "
    "one of the game's data symbols; its C is kept only when it compiles to the game's exact bytes at its address "
    "(compile_data), so a mistake costs nothing. The game's code is private: never publish it.\n"
    "Loop: claim_data() gives you a unit (address, how far it runs, its bytes, the functions that use it, and the "
    "fields the game's code reads and writes in it). Keep those types; read the functions (get_function) for the "
    "rest: a struct, an array of s16, a table of pointers. Never store a pointer in an integer. Write "
    "the whole datum as one definition, pointers by name (`&D_80012340`, a function's name), and call "
    "compile_data(name, source). Types the code shares belong in the header (edit_context; matched functions must "
    "still match). When it matches, claim the next. If after a few tries it won't, release_data(name, note) with "
    "what you learned and claim another.\n"
    "Read guide(\"data\") once for the rules (initialisers, .bss, padding). Work up to {count} units this session, "
    "then end; the app continues the queue. If claim_data says nothing is left, end the session."
)


def configure(net, slug, options, count):
    """A data task needs a ROM project whose data the app can find."""
    if options.get("task") != TASK:
        return options
    with net.progress.view():
        proj = data_units._project(net, slug)
        found = data_units.initialised_data(net, proj)
    if not found:
        raise ValueError("The app can't find this game's initialised data yet (it reads where .bss starts from the "
                         "program: the N64's boot stub), so there is nothing for data agents to write.")
    return options


def bind(net, session, agent):
    if session.options.get("task") == TASK:
        net.__dict__.setdefault("_data_agents", {})[agent.id] = session


def scope(net, agent):
    return getattr(net, "_data_agents", {}).get(agent.id) if agent is not None else None


def guard(net, agent, slug, name, args):
    """Keep data agents to their tools, and their compiles to the unit they hold."""
    if not scope(net, agent):
        return
    if name not in TOOLS:
        raise NetworkError(f"Data agents write the game's data: {name} isn't one of their tools.")
    if name == "compile_data":
        proj = data_units._project(net, slug)
        address = net.symbols(proj["id"]).resolve(str(args.get("name", "")))
        if address is None or address not in _held(net, proj, agent):
            raise NetworkError("Claim this unit first (claim_data), then compile it.")


def _held(net, proj, agent) -> set[int]:
    return {r["address"] for r in data_units._store(net, proj).all(
        "SELECT address FROM data_units WHERE project_id=? AND agent_id=? AND lease>?", proj["id"], agent.id,
        time.time())}


def queue(net, proj) -> list[int]:
    """The units left, in the order agents take them (claimed ones included)."""
    regions = data_units.initialised_data(net, proj)
    if not regions:
        return []
    rows = {r["address"]: r for r in data_units._store(net, proj).all(
        "SELECT address, status, attempts, size, span FROM data_units WHERE project_id=?", proj["id"])}
    covered = sorted((a, a + (r["span"] or r["size"] or 0)) for a, r in rows.items() if r["status"] == "matched")
    places = sorted(a for a in data_units.references(net, proj)
                    if any(r["vram"] <= a < r["vram"] + r["size"] for r in regions))
    # a jump table is its function's own (the judge places it with the function's C): not a datum of its own
    symbols = net.symbols(proj["id"])
    tables = {s.address for s in symbols.by_name.values() if s.kind == "jtbl" or s.name.startswith("jtbl_")}
    left = [a for a in places if a not in tables and not any(lo <= a < hi for lo, hi in covered)]
    return sorted(left, key=lambda a: ((rows[a]["attempts"] if a in rows else 0), a))


def left(net, slug) -> int:
    """Units left, asked every turn of every data agent's session loop."""
    with net.progress.view():
        return len(queue(net, data_units._project(net, slug)))


def claim(net, agent, slug, name: str = "") -> str:
    """Give the agent a unit: the one it names, or the next one nobody holds. Holding one, it gets that one back."""
    proj = data_units._project(net, slug)
    store = data_units._store(net, proj)
    now = time.time()
    order = queue(net, proj)
    if name:
        address = net.symbols(proj["id"]).resolve(name)
        if address is None or address not in order:
            raise NetworkError(f"`{name}` isn't a unit left to write: claim_data() without a name gives the next.")
        wanted = [address]
    else:
        held = _held(net, proj, agent)
        wanted = [a for a in order if a in held] or order
    with store.tx() as db:
        taken = {r["address"] for r in db.execute(
            "SELECT address FROM data_units WHERE project_id=? AND agent_id IS NOT NULL AND agent_id!=? AND lease>?",
            (proj["id"], agent.id, now)).fetchall()}
        address = next((a for a in wanted if a not in taken), None)
        if address is None:
            return ("Another agent holds that unit." if name else
                    "Nothing is left to claim: every unit is in C or held by another agent. End the session.")
        db.execute("INSERT OR IGNORE INTO data_units(project_id, address) VALUES(?, ?)", (proj["id"], address))
        db.execute("UPDATE data_units SET agent_id=NULL, lease=NULL WHERE project_id=? AND agent_id=? AND address!=?",
                   (proj["id"], agent.id, address))
        db.execute("UPDATE data_units SET agent_id=?, lease=? WHERE project_id=? AND address=?",
                   (agent.id, now + LEASE, proj["id"], address))
    symbols = net.symbols(proj["id"])
    info = data_units.unit(net, slug, symbols.name_at(address)[0])
    note = store.one("SELECT note FROM data_units WHERE project_id=? AND address=?", proj["id"], address)
    text = data_units.unit_text(info)
    if note and note["note"]:
        text += f"\nA previous try left this note:\n{note['note']}"
    return f"Claimed {info['name']} ({len(order)} units left).\n{text}"


def release(net, agent, slug, name: str, note: str = "") -> str:
    """Give a unit back, with what was learned, for the next try. It counts as a try, so the queue (fewest tries
    first) hands out the units nobody has tried before this one again, never the same one straight back."""
    proj = data_units._project(net, slug)
    address = net.symbols(proj["id"]).resolve(name)
    if address is None:
        raise NetworkError(f"`{name}` is not a known symbol.")
    with data_units._store(net, proj).tx() as db:
        db.execute("UPDATE data_units SET agent_id=NULL, lease=NULL, note=COALESCE(NULLIF(?, ''), note), "
                   "attempts=attempts+1 WHERE project_id=? AND address=? AND (agent_id=? OR agent_id IS NULL)",
                   (note[:2000], proj["id"], address, agent.id))
    return f"Released {name}. claim_data() for the next unit."


def compiled(net, agent, slug, name: str, result) -> None:
    """After a data agent's compile: it counts as the session's work, renews the lease, and a match frees the unit."""
    if not scope(net, agent):
        return
    counts = net.__dict__.setdefault("_session_diagnostic_compiles", {})
    counts[agent.id] = counts.get(agent.id, 0) + 1
    proj = data_units._project(net, slug)
    address = net.symbols(proj["id"]).resolve(name)
    with data_units._store(net, proj).tx() as db:
        if result.exact:
            db.execute("UPDATE data_units SET agent_id=NULL, lease=NULL WHERE project_id=? AND address=?",
                       (proj["id"], address))
        else:
            db.execute("UPDATE data_units SET lease=? WHERE project_id=? AND address=? AND agent_id=?",
                       (time.time() + LEASE, proj["id"], address, agent.id))


def prompt(net, session) -> str:
    from .agent_startup import project_facts
    title = project_facts(net, session.slug).get("title") or session.slug
    text = PROMPT.format(title=title, count=session.options.get("per_session", 8))
    if wish := net.instructions(session.slug):
        text += f"\nThe user's instructions for this project (they come first):\n{wish}\n"
    return text
