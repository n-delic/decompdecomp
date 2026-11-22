"""The watch_in_emulator tool: the user's own ROM run in its console's reversing emulator (Console.emulator, N64:
consoles/n64/emulator), to see what the real game does where a port or a function's C behaves differently.

Two questions it answers, each named by the project's own symbols:
- write: which code writes an address (a data symbol, or a hex address), with the caller of that code;
- call: what a function is called with, from where, and the words its first argument points at.

A run takes from seconds to minutes (breakpoints run the slower interpreter), and a helper's call is answered within
the MCP gateway's deadline (docs/MCP_GATEWAY.md). So each run goes on in the background: the call waits up to WAIT
seconds, and a run still going says so; the same call again, later, gets its result (kept for KEEP seconds).
"""
from __future__ import annotations

import re
import threading
import time

from ..network import NetworkError

WAIT = 40.0  # seconds a call waits for its run, under the gateway's deadline
KEEP = 1800.0  # seconds a finished run's answer stays for the call that asks again
MAX_VIS = 60 * 60 * 5  # five minutes of the game
_runs: dict[tuple, dict] = {}
_lock = threading.Lock()


def _address(net, proj, target: str, kind: str) -> tuple[int, str]:
    """The address a target names: hex, a symbol, or a function's name; with how the answer names it."""
    text = target.strip()
    if re.fullmatch(r"(0x)?[0-9A-Fa-f]{6,8}", text):
        value = int(text, 16)
        return value, net.symbols(proj["id"]).label(value)
    row = net.project_store(proj["id"]).one(
        "SELECT vram FROM functions WHERE project_id=? AND name=?", proj["id"], text)
    if row is not None and row["vram"] is not None:
        return row["vram"], text
    value = net.symbols(proj["id"]).resolve(text)
    if value is None:
        raise NetworkError(f"{target!r} is neither a hex address nor a symbol of this game: give one of its functions "
                           f"(for a call) or data symbols (for a write), or an address like 802A45C8")
    if kind == "call" and row is None:
        raise NetworkError(f"{target!r} is data, not a function: a call watch stops where a function starts")
    return value, text


def describe(net, proj, run, kind: str, label: str, vis: int, report_from: int) -> str:
    """The run as text: each hit named by the game's symbols."""
    symbols = net.symbols(proj["id"])
    hits = [h for h in run.hits if h.kind == ("break" if kind == "call" else "watch")]
    span = f"VIs {report_from} to {run.vis}" if report_from else f"the first {run.vis} VIs"
    lines = [f"{label}: {len(hits)} {'calls' if kind == 'call' else 'writes'} in {span}"
             + (" (run cut short: it took too long)" if run.timed_out else "")
             + (f", the first {len(hits)} reported" if len(hits) >= 200 else "") + "."]
    if run.vis < vis and not run.timed_out:
        lines.append(f"The game stopped at VI {run.vis} of {vis}.")
    for h in hits[:60]:
        if kind == "call":
            words = " ".join(f"{w:08X}" for w in h.words)
            lines.append(f"- VI {h.vi}: called from {symbols.label(h.ra - 8)} (returns to {h.ra:08X}); a0-a3 "
                         + " ".join(f"{a:08X}" for a in h.args) + f"; words at a0: {words}")
        else:
            lines.append(f"- VI {h.vi}: written by {symbols.label(h.pc)} (pc {h.pc:08X}), $ra {symbols.label(h.ra)}; "
                         f"a0 {h.args[0]:08X} a1 {h.args[1]:08X} v0 {h.v0:08X}")
    if len(hits) > 60:
        lines.append(f"... {len(hits) - 60} more")
    if not hits:
        lines.append("Nothing hit. A game sits at its title screen without input: give input to reach where it "
                     "happens, more VIs, or check the address (a write watch takes the address the game writes, "
                     "a call watch a function's start).")
    for e in run.errors[:3]:
        if not e.startswith("core: Unable to open rom database"):
            lines.append(f"Emulator: {e}")
    return "\n".join(lines)


def watch(net, slug: str, target: str, kind: str = "write", vis: int = 600, from_vi: int = 0, input: str = "",
          length: int = 4, max_hits: int = 40, expansion_pak: bool = False) -> str:
    """Run the game's ROM in the reversing emulator and report the writes to `target` or the calls of it."""
    proj = net.project_row(slug)
    console = net.console(proj)
    if console.emulator is None:
        raise NetworkError(f"{console.name} has no reversing emulator in decompdecomp yet (ROADMAP.md, \"The "
                           "reversing emulator\"): this tool runs N64 games for now")
    if not net.is_rom(proj):
        raise NetworkError("watch_in_emulator runs projects imported from a ROM: this one has no ROM to run")
    if kind not in ("write", "call"):
        raise NetworkError("kind is write (which code writes the address) or call (what the function is called with)")
    vis, from_vi = int(vis), int(from_vi)
    if not 1 <= vis <= MAX_VIS or not 0 <= from_vi < vis:
        raise NetworkError(f"vis is 1 to {MAX_VIS} (60 a second), and from_vi below it")
    length = max(1, min(int(length), 0x10000))
    max_hits = max(1, min(int(max_hits), 200))
    from pathlib import Path
    cfg = net.cfg(proj)
    rom = Path(proj["root"]) / cfg["rom"]["file"]
    if not rom.is_file():
        raise NetworkError(f"the game's ROM isn't at {rom} any more: import it again to run it")
    address, label = _address(net, proj, target, kind)
    key = (slug, kind, address, length, vis, from_vi, input.strip(), max_hits, bool(expansion_pak))
    now = time.monotonic()
    with _lock:
        for k in [k for k, r in _runs.items() if r.get("done") and now - r["done"] > KEEP]:
            del _runs[k]
        entry = _runs.get(key)
        if entry is None or entry.get("error"):
            entry = _runs[key] = {"started": now, "event": threading.Event()}

            def go(entry=entry):
                try:
                    entry["run"] = console.emulator.run(
                        rom, vis, inputs=input.strip(), report_from=from_vi, max_hits=max_hits,
                        expansion_pak=bool(expansion_pak), timeout=1500,
                        **({"breaks": [address]} if kind == "call" else {"watches": [(address, length)]}))
                except Exception as error:  # (the tool says it; the next call starts again)
                    entry["error"] = str(error)
                entry["done"] = time.monotonic()
                entry["event"].set()
            threading.Thread(target=go, name=f"emulator-{slug}", daemon=True).start()
    entry["event"].wait(WAIT)
    if not entry["event"].is_set():
        took = time.monotonic() - entry["started"]
        return (f"Still running ({took:.0f} s so far: breakpoints run the slower interpreter). Call watch_in_emulator "
                "again with the same arguments for the result; meanwhile work on something else.")
    if entry.get("error"):
        raise NetworkError(entry["error"])
    text = describe(net, proj, entry["run"], kind, label, vis, from_vi)
    if kind == "write" and (typed := record_type(net, slug, proj, entry["run"], address)):
        text += "\n" + typed
    return text


# MIPS stores: opcode -> (bytes written, the C type the store says). A float store (swc1, sdc1) says the type; an
# integer store says its width, and the unsigned type of that width is the least it claims.
STORES = {0x28: (1, "u8"), 0x29: (2, "u16"), 0x2B: (4, "u32"), 0x3F: (8, "u64"), 0x39: (4, "f32"), 0x3D: (8, "f64")}


def infer_type(net, proj, run, address: int) -> str | None:
    """The C type the watched writes agree on: every hit's store instruction writes the symbol's whole size with one
    opcode. A symbol of another size, a write by two kinds of store, or no hit says nothing."""
    symbols = net.symbols(proj["id"])
    sym = symbols.by_addr.get(address)
    hits = [h for h in run.hits if h.kind == "watch"]
    read = net.reader(proj)
    if sym is None or not sym.size or not hits:
        return None
    kinds = set()
    for h in hits:
        raw = read(h.pc, 4)
        if raw is None or len(raw) < 4:
            return None
        word = int.from_bytes(raw, "big" if proj["big_endian"] else "little")
        kinds.add(STORES.get(word >> 26))
    if len(kinds) != 1 or None in kinds:
        return None
    width, c_type = kinds.pop()
    return c_type if width == sym.size else None


def record_type(net, slug: str, proj, run, address: int) -> str:
    """Give the watched data symbol the type its writes agree on, unless an agent or the user typed it already."""
    c_type = infer_type(net, proj, run, address)
    if c_type is None:
        return ""
    store = net.project_store(proj["id"])
    row = store.one("SELECT name, c_type, type_source FROM symbols WHERE project_id=? AND address=? AND kind<>'func' LIMIT 1",
                    proj["id"], address)
    if row is None or (row["c_type"] and row["type_source"] != "emulator"):
        return ""
    from .globals_view import set_type
    set_type(net, slug, row["name"], c_type, "emulator")
    return f"Every write is a {c_type} store: {row['name']} is recorded as `{c_type}` in the Globals tab."
