"""Operator-selected evidence for stalled matches; never changes the judge or edits agent notes."""

import hashlib
import re
import time
from pathlib import Path

from .. import scoring
from ..elfobj import FunctionObj

BLOCKER = re.compile(r"\b(depend\w*|prerequisite|relocat\w*|linker|blocked|blocker)\b", re.I)


def judge_stamp() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = ("rom/match.py", "rom/link.py", "arch/arm.py", "arch/ppc64.py",
             "app/engine/core.py", "app/engine/judging.py", "app/match_diagnostics.py")
    return hashlib.sha256(b"".join((root / p).read_bytes() for p in paths)).hexdigest()


def configure(settings, net) -> None:
    """A toggle or a changed judge establishes a new revalidation boundary, persisted across restarts."""
    enabled = settings.data.get("match_diagnostics") is True
    if enabled:
        stamp = judge_stamp()
        if settings.data.get("match_diagnostics_stamp") != stamp or not settings.data.get("match_diagnostics_since"):
            settings.data["match_diagnostics_stamp"] = stamp
            settings.data["match_diagnostics_since"] = time.time()
            settings.save()
    net.match_diagnostics_since = settings.data.get("match_diagnostics_since", 0) if enabled else 0


def annotate(rows, since: float):
    """Old blocker claims need fresh evidence; their underlying facts are not automatically false."""
    marked = []
    for row in rows:
        text = row["text"] or ""
        if (row["created_at"] or 0) < since and BLOCKER.search(text):
            text = ("[Recheck: this blocker note predates the operator's diagnostic baseline or a judge "
                    "update. It is not a verified current dependency.] " + text)
        marked.append(dict(row, text=text))
    return marked


def details(result, cpu="") -> list[str]:
    if not result.mismatches or result.candidate is None:
        return []
    lines = ["Fresh linked-byte evidence: inspect the included helper/data before changing the caller. "
             "Keep the full best source, including helpers; a stub cannot test the remaining mismatch. "
             "A helper mismatch alone does not establish a dependency on another unmatched function."]
    candidate = result.candidate
    for item in result.mismatches:
        text = (f"{item.label}: first unequal byte at offset +0x{item.offset:X}; "
                f"window begins at 0x{item.address:X}.")
        text += f"\nExpected bytes: {item.expected[:64].hex(' ')}\nActual bytes:   {item.actual[:64].hex(' ')}"
        if item.code:
            target = FunctionObj(item.label, candidate.arch, candidate.big_endian, item.expected, [])
            actual = FunctionObj(item.label, candidate.arch, candidate.big_endian, item.actual, [])
            target.cpu = actual.cpu = cpu
            text += "\nLinked helper instruction window (addresses within this window are relative):\n"
            diff = scoring.diff(target, actual)
            # A changed helper length shifts early branches. Show the instruction/layout change first,
            # when the bounded window contains one, rather than spending the whole excerpt on offsets.
            focus = next((i for i, row in enumerate(diff.rows) if row[0] in ("|", ">", "<", "r", "s")), None)
            if focus is not None:
                rows = diff.rows[max(0, focus - 3):focus + 17]
                diff = scoring.DiffResult(diff.score, diff.max_score, rows, diff.bytes_match)
                text += "Prioritizing an instruction/layout difference; shifted branch offsets may be secondary.\n"
            text += diff.render(max_rows=20)
        lines.append(text)
    return lines
