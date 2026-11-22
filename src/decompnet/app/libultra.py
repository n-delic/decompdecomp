"""ROM header hints and evidence from explicitly imported local libultra archives.

Library fingerprints identify code, not a whole SDK release: unchanged objects occur in many releases.
No archive is downloaded or executed by this module.
"""

import re
from collections import Counter, defaultdict
from pathlib import Path

from .sdk_library import allowed
from .sdk_names import name_functions as name_functions

_VERSION = re.compile(r"\blib(?:g)?ultra\s+(2\.0[A-L])\b", re.I)
MIN_BYTES = 32


def report(net, proj):
    cfg = net.cfg(proj)
    if cfg.get("platform", "n64") != "n64":
        return None
    fork = net.fork_slug(proj["slug"])
    if fork:
        _, fp, _ = net.forks.get(proj["slug"])
        # Use the same selected original input as setup, including versioned splat and manifests.
        original = fp.needs(cfg.get("version")).get("path")
        path = None
        if isinstance(original, str) and original:
            try:
                path = (fp.root / original).resolve()
                path.relative_to(fp.root.resolve())
            except ValueError:
                path = None
    elif net.is_rom(proj):
        try:
            path = Path(proj["root"]) / cfg["rom"]["file"]
        except KeyError:
            return None
    else:
        return None
    try:
        if path is None:
            raise FileNotFoundError("No original ROM configured")
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError:
        header = b""
    valid = len(header) >= 16 and header[:4] == bytes.fromhex("80371240")
    if not valid and not fork:
        return None
    if not valid:
        header = bytes(16)  # imported target objects can still supply library signature evidence
    raw = header[12:16]
    value, letter = header[14], header[15]
    version = f"{value // 10}.{value % 10}{chr(letter)}" if value and 65 <= letter <= 90 else None
    prints = net.twins.prints(proj)
    keys = Counter(p.key for p in prints.values() if p.size >= MIN_BYTES and not p.asm)
    corpora = defaultdict(set)
    names = defaultdict(set)
    versions = defaultdict(set)
    # Use the fingerprint index: most installed SDK signatures do not occur in this game.
    # Materializing the whole library on every overview poll competed with agents for the GIL.
    rows = []
    fingerprints = list(keys)
    for at in range(0, len(fingerprints), 400):
        part = fingerprints[at:at + 400]
        rows.extend(net.store.all("SELECT corpus,name,fingerprint FROM sdk_signatures "
                                  "WHERE arch='mips' AND big_endian=1 AND fingerprint IN ("
                                  + ','.join('?' for _ in part) + ')', *part))
    for row in rows:
        if not allowed(net, proj, row["corpus"]):
            continue
        match = _VERSION.search(row["corpus"])
        if not match or row["fingerprint"] not in keys:
            continue
        versions[row["fingerprint"]].add(match.group(1).upper())
        corpora[row["corpus"]].add(row["fingerprint"])
        names[row["corpus"]].add(row["name"])
    candidates = [{"corpus": c, "version": _VERSION.search(c).group(1).upper(),
                   "fingerprints": len(ks), "version_specific_fingerprints": sum(len(versions[k]) == 1 for k in ks),
                   "functions": sum(keys[k] for k in ks), "names": len(names[c])}
                  for c, ks in corpora.items()]
    candidates.sort(key=lambda r: (-r["fingerprints"], r["corpus"]))
    indexed = {m.group(1).upper() for r in net.store.all("SELECT DISTINCT corpus FROM sdk_signatures WHERE arch='mips' AND big_endian=1")
               if (m := _VERSION.search(r["corpus"]))}
    return {"header_version": version, "header_raw": raw.hex() if valid else None, "header_offset": "0x0C",
            "signature_candidates": candidates, "summary": summary(version, candidates, indexed),
            "note": "Header declaration; signature counts are supporting evidence, not proof of one SDK revision."}


def summary(header, candidates, indexed):
    """One sentence saying what the ROM header declares, which indexed archives the code matches best and which
    releases no archive covers. A header can keep the version of the `romheader` a project started from while the
    game links a newer library, and code that is unchanged between releases matches every archive that has it, so
    the two can differ without either being wrong."""
    best = {c["version"] for c in candidates if c["fingerprints"] == candidates[0]["fingerprints"]} if candidates else set()
    parts = [f"ROM header declares {header}" if header else "ROM header declares no libultra version"]
    if best:
        parts.append("code matches the " + _span(best) + " archives in your folder")
    else:
        parts.append("no libultra archive in your folder matches its code")
    top = max([v for v in (header, *best) if v] or [None], default=None)
    absent = [f"2.0{chr(c)}" for c in range(ord("A"), ord(top[-1]) + 1) if f"2.0{chr(c)}" not in indexed] if top else []
    text = "; ".join(parts)
    if absent:
        text += f" ({_span(absent)} {'is' if len(absent) == 1 else 'are'} not indexed, so code shared with them can't be told apart)"
    return text + "."


def _span(versions):
    versions = sorted(versions)
    return versions[0] if len(versions) == 1 else f"{versions[0]}\u2013{versions[-1]}" if len(versions) > 2 else " and ".join(versions)
