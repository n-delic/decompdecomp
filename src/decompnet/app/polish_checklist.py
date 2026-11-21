"""Per-game naming and portability priorities for the shared polishing queue."""

import json

from ..network import NetworkError
from ..polish_names import CHECKLIST_KEY, REQUIRE_NAME

PORTABILITY_CHECKS = ("compile_64bit", "pointer_casts")
CHECKS = (REQUIRE_NAME, *PORTABILITY_CHECKS)
NAME_FORMATS = {"camelCase": "updateActorTarget", "snake_case": "update_actor_target", "PascalCase": "UpdateActorTarget"}
DEFAULT_FORMAT = "camelCase"


def read(net, project_id):
    store = net.project_store(project_id)
    row = store.one("SELECT value FROM settings WHERE key=?", f"{CHECKLIST_KEY}:{project_id}")
    saved = json.loads(row["value"]) if row else []
    if isinstance(saved, list):
        # Older projects stored only the two portability choices.
        return {"selected": [REQUIRE_NAME, *saved], "name_format": DEFAULT_FORMAT}
    return saved


def requires_name(net, project_id):
    return REQUIRE_NAME in read(net, project_id)["selected"]


def save(net, project_id, selected, name_format):
    if not isinstance(selected, list) or any(check not in CHECKS for check in selected):
        raise NetworkError("Choose the polishing checks again and save.")
    if not isinstance(name_format, str) or name_format not in NAME_FORMATS:
        raise NetworkError("Choose a supported function name format and save.")
    options = {"selected": [check for check in CHECKS if check in selected], "name_format": name_format}
    with net.project_store(project_id).tx() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (f"{CHECKLIST_KEY}:{project_id}", json.dumps(options)))
    return options


def brief(options, *, pc_tool=False):
    selected = options["selected"]
    lines = []
    if REQUIRE_NAME in selected:
        style = options["name_format"]
        lines.append(f"- A human-readable function name is required. Use rename_symbol to replace a generated "
                     f"name with a descriptive {style} name such as {NAME_FORMATS[style]}. Use that format for new "
                     "function names and keep existing descriptive names.")
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
