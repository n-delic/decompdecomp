"""Per-game portability priorities for the shared polishing queue."""

import json

from ..network import NetworkError

CHECKS = ("compile_64bit", "pointer_casts")
KEY = "polishing-checklist"


def read(net, project_id):
    store = net.project_store(project_id)
    row = store.one("SELECT value FROM settings WHERE key=?", f"{KEY}:{project_id}")
    return json.loads(row["value"]) if row else []


def save(net, project_id, selected):
    if not isinstance(selected, list) or any(check not in CHECKS for check in selected):
        raise NetworkError("Choose the polishing checks again and save.")
    selected = [check for check in CHECKS if check in selected]
    with net.project_store(project_id).tx() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (f"{KEY}:{project_id}", json.dumps(selected)))
    return selected


def brief(selected, *, pc_tool=False):
    lines = []
    if "compile_64bit" in selected:
        method = ("Run check_for_pc with the candidate source and address its compile errors."
                  if pc_tool else "Use the project's 64-bit PC build or compiler check where available.")
        lines.append(f"- Compile for a 64-bit PC. {method} Use add_note to record any remaining blockers or missing verification.")
    if "pointer_casts" in selected:
        lines.append("- Fix casts between pointers and 32-bit integers. Keep values as pointers where possible, "
                     "or use uintptr_t/intptr_t when an integer must hold a pointer. Preserve genuine 32-bit game "
                     "addresses and data layouts. Use add_note to record any casts that cannot be changed while still matching.")
    return ("\nThis game's polishing checklist:\n" + "\n".join(lines)
            + "\nUse make_readable for source changes beyond a small rename plan. Every edit must still match.\n"
            if lines else "")
