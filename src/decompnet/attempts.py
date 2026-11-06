"""Measured build provenance and handoff feedback shared by ROM and project attempts.

Old attempts and room reports have unknown provenance. Never attach today's compiler to an old score: that
made agents retry an EE-GCC candidate under CodeWarrior and mistake the regression for a source problem.
"""

import hashlib
import json

HANDOFF_ATTEMPTS = 12
REGRESSION_MINIMUM = 4


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def provenance(compiler: dict, source: str, *, header: str | None = None,
               context_version: int | None = None, unit: str = "", build_source: str | None = None) -> dict:
    """The inputs observed before a build; missing headers or project-owned flags stay unknown."""
    return {"compiler": compiler, "source_sha256": digest(source), "context_version": context_version,
            "header_sha256": digest(header) if header is not None else None, "unit": unit,
            "build_source_sha256": digest(build_source) if build_source is not None else None}


def compiler_text(compiler: dict) -> str:
    flags = compiler.get("flags")
    return (compiler.get("toolchain") or "project build (compiler not exposed)") + (
        " " + " ".join(flags) if isinstance(flags, list) else " (flags owned by project build)")


def brief(net, fn, current: dict | None = None, header: str | None = None) -> list[str]:
    """Show the measured origin of the retained source and cross-agent stalls, even after a restart."""
    if fn["status"] == "matched":
        return []
    probes = probe_brief(net, fn, header)
    if not fn["best_source"]:
        return probes
    row = net.store.one("SELECT provenance FROM attempts WHERE function_id=? AND score=? AND source=? "
                        "ORDER BY id DESC LIMIT 1", fn["id"], fn["best_score"], fn["best_source"])
    saved = json.loads(row["provenance"]) if row and row["provenance"] else None
    out = ["", "### Reproduce the retained score first"]
    if saved:
        out.append(f"Measured with {compiler_text(saved['compiler'])}; context version "
                   f"{saved['context_version'] if saved['context_version'] is not None else 'project-owned'}, "
                   f"header SHA-256 {saved['header_sha256'] or 'not captured (project headers)'}.")
        if current is not None and saved["compiler"] != current:
            out.append("COMPILER CHANGED: the retained score used different settings. Use try_compiler with "
                       "the recorded compiler/flags before editing this source; do not silently switch the project.")
        if header is not None and saved["header_sha256"] != digest(header):
            out.append("The shared header changed since that score. Recheck declarations before comparing scores.")
    else:
        out.append("Compiler/header provenance was not recorded for this score. Today's settings are not evidence "
                   "of how it was produced; reproduce it before treating a worse score as a source regression.")
    recent = net.store.all("SELECT score, agent_id FROM attempts WHERE function_id=? ORDER BY id DESC LIMIT ?", fn["id"], HANDOFF_ATTEMPTS)
    if len(recent) >= REGRESSION_MINIMUM and all(x["score"] is None or x["score"] > fn["best_score"] for x in recent):
        out.append(f"Last {len(recent)} attempts by {len({x['agent_id'] for x in recent})} agents did not reproduce "
                   "the retained score. Stop repeating that route: verify compiler/flags and header, then test "
                   "one new hypothesis. Separate measurements from guesses in your handoff notes.")
    return out + probes


def record_probe(net, fn, agent_id: int, source: str, measured: dict, score: int | None, matched: bool) -> None:
    """Retain an alternate compiler measurement without accepting a match or spending an attempt budget."""
    import time

    target = hashlib.sha256(fn["target"] or b"").hexdigest()
    encoded = json.dumps(measured, sort_keys=True)
    key = digest(encoded + target + str(fn["relocs"]) + str(fn["vram"]))
    with net.store.tx() as db:
        db.execute("INSERT OR REPLACE INTO compiler_probes "
                   "(function_id,input_key,agent_id,score,matched,source,provenance,target_sha256,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (fn["id"], key, agent_id, score, matched, source, encoded, target, time.time()))


def probe_brief(net, fn, header: str | None) -> list[str]:
    """A better alternate route stays visible to the next agent; stale inputs are explicitly historical."""
    row = net.store.one("SELECT * FROM compiler_probes WHERE function_id=? AND score IS NOT NULL "
                        "ORDER BY matched DESC,score,created_at DESC LIMIT 1", fn["id"])
    if row is None or (not row["matched"] and fn["best_score"] is not None and row["score"] >= fn["best_score"]):
        return []
    saved = json.loads(row["provenance"])
    changed = (row["target_sha256"] != hashlib.sha256(fn["target"] or b"").hexdigest()
               or header is None or saved["header_sha256"] != digest(header))
    return ["", "### Retained alternate-compiler probe (not an accepted match)",
            f"{compiler_text(saved['compiler'])}: score {row['score']}; "
            + ("matched linked bytes when measured." if row["matched"] else "did not match."),
            "Historical inputs changed or cannot be checked here; reproduce this probe." if changed else
            "Reproduce this route with try_compiler; a compiler change still goes through suggest and its checks.",
            "```c", row["source"].strip(), "```"]
