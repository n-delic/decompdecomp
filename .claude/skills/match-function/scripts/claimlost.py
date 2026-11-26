"""The LOST marker of a match-function session: how every script learns that the session no longer holds its claim.

setup.py's renewal writes `LOST` (JSON) into the session folder (/tmp/dd-HOLDER/) when the app says someone else holds the
function or it is matched (`ours`: by this session, which is no loss), or when renewing has failed for as long as a claim lasts. Every setup.py command and the
long-running helpers (fast_judge, prove_chain, grid) print `warning()` first, so an agent working in the isolated
judge on a function another session already took finds out at its next command, not an hour later. Standard library only."""
import json
import os
import sys
import time
from pathlib import Path

MARKER = "LOST"


def session_root(root=None):
    """The session folder: the one given, $DD_SESSION_DIR, or the folder holding $UV_PROJECT_ENVIRONMENT (setup.py's
    `env` lines set it, so a helper run in the session finds its folder without being told the holder)."""
    if root:
        return Path(root)
    if os.environ.get("DD_SESSION_DIR"):
        return Path(os.environ["DD_SESSION_DIR"])
    venv = os.environ.get("UV_PROJECT_ENVIRONMENT")
    return Path(venv).parent if venv and (Path(venv).parent / "connection.json").exists() else None


def read(root=None):
    root = session_root(root)
    try:
        return json.loads((root / MARKER).read_text()) if root else {}
    except (OSError, ValueError):
        return {}


def write(root, function, reason, holder="", matched=False, ours=False):
    """Record the loss durably; the whole file is replaced in one step."""
    marker = {"function": function, "reason": reason, "holder": holder, "matched": matched, "ours": ours,
              "at": time.time()}
    return _save(root, marker)


def _save(root, marker):
    temporary = Path(root) / (MARKER + ".tmp")
    temporary.write_text(json.dumps(marker, indent=2))
    os.replace(temporary, Path(root) / MARKER)
    return marker


def clear(root):
    (Path(root) / MARKER).unlink(missing_ok=True)


def taken(marker):
    """Is the function someone else's (held or matched), so submitting to it is pointless?"""
    return bool(marker) and bool(marker.get("holder") or marker.get("matched"))


def warning(root=None):
    marker = read(root)
    if not marker or marker.get("ours"):  # this session matched it: nothing was lost
        return ""
    holder = Path(session_root(root)).name.removeprefix("dd-")
    what = marker.get("function") or "the function"
    if marker.get("matched"):
        now = f"{what} is MATCHED (if this session did not match it, another one did)"
    elif marker.get("holder"):
        now = f"{what} is now held by {marker['holder']}"
    else:
        now = f"{what} could not be renewed ({marker.get('reason')}) and has probably lapsed"
    return ("\n" + "!" * 78 + f"\n!!! CLAIM LOST: {now}.\n"
            f"!!! Work done on it from here is probably wasted. Run `setup.py check {holder}`: if the function is free,\n"
            f"!!! `setup.py reclaim {holder} {what}`; otherwise switch functions (`release`, then `reclaim {holder}`).\n"
            + "!" * 78 + "\n")


def warn(root=None):
    """Print the warning of a lost claim and say so (True). A function this session matched itself is no loss: it is
    said once, in one line."""
    marker = read(root)
    if marker.get("ours"):
        if not marker.get("said"):
            print(f"{marker.get('function')} is MATCHED by this session: nothing left to renew or claim for it.",
                  file=sys.stderr, flush=True)
            _save(session_root(root), {**marker, "said": True})
        return False
    text = warning(root)
    if text:
        print(text, file=sys.stderr, flush=True)
    return bool(text)
