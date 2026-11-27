"""The judge a verifier runs (gateway/src/verifier.rs): compile a hosted submission like decompdecomp does.

    decomp judge-worker      # one JSON request per line on stdin, one reply per line on stdout

A hosted server's agents compile on their own machines; a match counts once a trusted machine has compiled the same
C into the same bytes. This judges with this computer's own copy of each game, its compilers and linker: the exact
judge the app uses (RomNetwork._judge_with), so a verified match here is a match in the app.

Requests:
- {"op": "describe"} -> {"projects": [slug, ...], "toolchains": [id, ...], "version": ...}: the ROM projects in
  this home and the compilers installed.
- {"op": "judge", "slug", "function": {"name", "vram", "target" (hex)}, "compiler": {"toolchain", "flags"},
  "source"} -> {"score": 0 for a match, the difference otherwise, null when it didn't compile; "detail"}, or
  {"error": ...} when it can't be judged here. That happens when the project isn't here, or its function's target
  bytes differ from the hosted ones (another build of the game). With "diff": true the reply also carries "diff", the
  app's own side-by-side rows for the candidate, the first "rows" of them (default 60; the last line counts the rest)
  (DiffResult.render: the same normalisation compile_and_diff shows; when only a jump table's data differs, its lines
  name the cases whose entry differs, and "jump_tables" carries them as rows). With "raw_rows": true it carries "rows", every
  (marker, target, candidate) row of that diff, unrendered (the skill's region view, regions.py). Every compiled reply
  carries "code", a digest of the candidate's instructions: two sources with one digest compiled the same (the skill's
  invariant-spelling hint, invariant.py).
- {"op": "function", "slug", "name", "vram" (optional, for overlays)} -> {"name", "vram", "target" (hex), "unit",
  "arch", "asm" (the target's assembly text), "asm_names" ({split name: current name} of its renamed symbols),
  "header" (the project's typedefs and externs the judge prepends),
  "compiler": {"toolchain", "flags"} (this function's own profile), "tables" ([[low, [targets...]], ...]: its jump
  tables, as switch_tables.read), "callees" (its callees' matched C, capture_followup_repairs.callee_evidence) and
  "entry_scope" (promoted_entry_repairs.scope), "words" ({symbol: word} of its float constants, float_literal_words.read): the context the app's draft path hands draft_repairs.variants}: everything a local search needs to judge without being told the function's bytes
  (the match-function skill's fast_judge.py).
- {"op": "passes", "slug", "name", "source", "flags" (optional: flags added to the function's profile for this compile,
  as the judge request's compiler flags; the skill's --flags), "passes" (optional), "save" (optional: also return every file of the compile, the preprocessed C, the final assembly and each pass dump, as "files"), "sched_verbose" (optional: the first scheduler's
  notes too)} -> {"text"}: the compiler's allocation priority
  table, register preferences and scheduling orders for that C under the function's profile (compiler_passes.report,
  the code behind the compiler_passes tool; the skill's passes.py).
- {"op": "allocation", "slug", "name", "source", "flags" (optional, as for passes), "rows" (optional: the judge's raw rows)} -> {"lreg", "greg"
  (the function's part of each dump), "allocation" (gcc_allocation's values: pseudo, hard register, C lines),
  "text" (with rows: register_explain.explain, the skill's explain_regs.py)}.
- {"op": "neighbours", "slug", "name", "limit" (optional), "rows" (optional: the judge's raw rows for a candidate)}
  -> {"text", "items"}: the closest matched functions (callers, callees, the functions beside it, shared globals,
  look-alikes and, with rows, those sharing the mismatched region's instruction shape) with their matching C
  (matched_neighbours; the skill's neighbours.py).
- {"op": "signatures", "slug", "name", "source" (optional: the saved best)} -> {"text", "findings"}: every call in
  that C checked against the callee's matched C, the shared header and the target's call sites, and the function's own
  parameters against its matched callers (call_signatures; the skill's signatures.py, printed after a claim).
- {"op": "candidates", "slug", "max_insns" (optional)} -> {"items"}: open, unclaimed functions worth a small
  model's try, each with its saved C, stored draft and candidate profiles (match_brief; the skill's haiku.py).
- {"op": "brief", "slug", "name"} -> that function's assembly, saved C, a fresh draft, candidate profiles, matched
  neighbours' signatures and header declarations (match_brief.brief).
- {"op": "preprocess", "slug", "name", "source"} -> {"text", "cc1", "log"}: the candidate preprocessed as the app's
  compile does it (the function's profile, the project's header; compiler.preprocess_candidate), and the cc1 command
  line to run dumps on the result (the skill's preprocess.py). The bundled bare `cpp` aborts, so never run it by hand.

Opened as the reader process does (store.ATTACH_ONLY, no saved-progress sync), so it can run beside the app.

    decomp judge-worker --packs DIR    # judge from verification packs instead: no game imported here

With --packs, the projects are DIR/<slug>.sqlite3, verification packs (hosted_pack.py) that the verifier downloads
from the hosted server. Each becomes a small scratch project the same judge runs on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def describe(net) -> dict:
    from . import __version__, toolchains
    from .network import Network
    projects = []
    for row in net.store.all("SELECT slug FROM projects ORDER BY slug"):
        try:
            proj = Network._project(net, row["slug"])
        except Exception:
            continue
        if net.cfg(proj).get("kind") != "fork":
            projects.append(row["slug"])
    installed = sorted(tid for tid in toolchains.registry() if _installed(toolchains, tid))
    return {"projects": projects, "toolchains": installed, "version": __version__}


def _installed(toolchains, tid) -> bool:
    try:
        return toolchains.installed(tid) is not None
    except Exception:
        return False


def judge(net, request: dict) -> dict:
    from .network import Network
    from .scoring import SCORE_VERSION
    slug = str(request.get("slug") or "")
    wanted = request.get("function") or {}
    compiler = request.get("compiler") or {}
    source = request.get("source")
    if not isinstance(source, str) or not source.strip():
        return {"score": None, "detail": "no source"}
    try:
        proj = Network._project(net, slug)
    except Exception:
        return {"error": f"this computer has no project {slug!r}"}
    if net.cfg(proj).get("kind") == "fork":
        return {"error": f"{slug} is a fork project; only ROM projects are judged here"}
    store = net.project_store(proj["id"])
    rows = store.all("SELECT * FROM functions WHERE project_id=? AND name=? AND vram IS ? AND gone_mark IS NULL",
                     proj["id"], wanted.get("name"), wanted.get("vram"))
    if len(rows) != 1:
        return {"error": f"{slug} here has {len(rows)} functions named {wanted.get('name')!r} at that address"}
    fn = rows[0]
    try:
        expected = bytes.fromhex(str(wanted.get("target") or ""))
    except ValueError:
        return {"error": "the target bytes aren't hex"}
    if bytes(fn["target"] or b"") != expected:
        return {"error": f"{slug}'s {fn['name']} here has other target bytes: another build of the game"}
    if expected_header := request.get("header_sha256"):
        import hashlib

        header = net._context(proj["id"], fn["unit"])["header"]
        if hashlib.sha256(header.encode()).hexdigest() != expected_header:
            return {"error": "the verification pack has a different context header"}
    toolchain = compiler.get("toolchain")
    flags = compiler.get("flags") or []
    if not toolchain:
        return {"error": "no compiler named"}
    net._last_judge_checks = {}
    evidence = request.get("score_evidence") == SCORE_VERSION and not (request.get("diff") or request.get("raw_rows"))
    options = {"evidence": True} if evidence else {}
    matched, diff, log = net._judge_with(proj, fn, toolchain, list(flags), source, **options)
    if evidence and diff is not None:
        from dataclasses import asdict

        detail = "byte-identical" if matched else (log or "")[-2000:]
        return {"score_evidence": {"version": SCORE_VERSION, "matched": matched, "comparison": asdict(diff)},
                "detail": detail}
    checks = getattr(net, "_last_judge_checks", None) or {}
    rows = request.get("rows")  # how many rows the diff shows (default 60); the rest is counted in its last line
    shown = {}
    if request.get("diff") and diff is not None:
        shown = {"diff": diff.render(max_rows=rows if isinstance(rows, int) and rows > 0 else 60, more_hint=False)}
    if request.get("raw_rows") and diff is not None:
        shown["rows"] = [list(row) for row in diff.rows]
    # Every instruction exact but the jump table's data: the diff has no rows and the score still says 1. The rows name the
    # cases whose entry differs (case | target destination | compiled destination), the evidence the case-label families read.
    jump_tables = checks.get("jump_tables") or []
    if jump_tables and not matched:
        from .app.switch_tables import difference_text
        shown["jump_tables"] = jump_tables
        if "diff" in shown:
            shown["diff"] += "\n" + difference_text(jump_tables)
    if getattr(diff, "rows", None) is not None:  # what the candidate compiled to, so the skill sees spellings that make identical code
        import hashlib
        shown["code"] = hashlib.sha1("\n".join(str(row[2]) for row in diff.rows).encode()).hexdigest()[:16]
    if matched:
        return {"score": 0, "detail": "byte-identical", **shown}
    if diff is None:
        return {"score": None, "detail": (log or "it didn't compile")[-2000:]}
    return {"score": max(1, int(diff.score)), "detail": (log or "")[-2000:], **shown}


def lookup(net, request: dict):
    """(project, function row) for a request's slug, name and optional address, or an {"error": ...} reply."""
    from .network import Network
    slug = str(request.get("slug") or "")
    try:
        proj = Network._project(net, slug)
    except Exception:
        return {"error": f"this computer has no project {slug!r}"}
    if net.cfg(proj).get("kind") == "fork":
        return {"error": f"{slug} is a fork project; only ROM projects are judged here"}
    rows = net.project_store(proj["id"]).all(
        "SELECT * FROM functions WHERE project_id=? AND name=? AND gone_mark IS NULL", proj["id"], request.get("name"))
    if request.get("vram") is not None:
        rows = [row for row in rows if row["vram"] == request["vram"]]
    if len(rows) != 1:
        where = ", ".join(hex(row["vram"] or 0) for row in rows)
        return {"error": f"{slug} has {len(rows)} functions named {request.get('name')!r}"
                         + (f" (at {where}): pass the address" if rows else "")}
    return proj, rows[0]


def function_info(net, request: dict) -> dict:
    """A function's target bytes, header and compiler profile, so a local search needs only its name."""
    from .project import compiler_for
    found = lookup(net, request)
    if isinstance(found, dict):
        return found
    proj, fn = found
    from .app import draft_repairs, float_literal_words, repair_asm, switch_tables
    profile = compiler_for(net.cfg(proj), fn["vram"], fn)
    # What draft_repairs.variants needs beyond the source: families like switch_gap_goto read the jump tables, so a
    # script that calls variants without them never sees those families fire.
    return {"name": fn["name"], "vram": fn["vram"], "target": bytes(fn["target"] or b"").hex(), "unit": fn["unit"],
            "arch": proj["arch"], "big_endian": bool(proj["big_endian"]), "asm": fn["asm"] or "",
            # the assembly's automatic names that have been renamed since: the families read the C's spelling (#4304)
            "asm_names": repair_asm.renames(net, proj, fn["asm"] or ""),
            "header": net._context(proj["id"], fn["unit"])["header"],
            "tables": [[low, list(targets)] for low, targets in switch_tables.read(net, proj, fn)],
            "callees": draft_repairs.capture_followup_repairs.callee_evidence(net, proj, fn, fn["best_source"] or ""),
            "entry_scope": draft_repairs.promoted_entry_repairs.scope(net, proj, fn),
            "words": float_literal_words.read(net, proj, fn),
            "compiler": {"toolchain": profile.get("toolchain"), "flags": list(profile.get("flags") or [])}}


def _profile_flags(request: dict):
    """The request's `flags` (added to the function's profile, as the judge request's compiler flags take them) as a
    tuple, or an error reply. Two functions of one game were probed under `-mfp32` with fast_judge.py while passes.py and
    explain_regs.py still dumped the `-mfp64` profile: every float register they named was another compile's."""
    flags = request.get("flags") or ()
    if isinstance(flags, str):
        flags = flags.split()
    if not isinstance(flags, (list, tuple)) or not all(isinstance(f, str) and f.startswith("-") for f in flags):
        return {"error": "flags must be a list of compiler options, each starting with '-'"}
    return tuple(flags)


def passes_report(net, request: dict) -> dict:
    """The compiler's own allocation, preference and scheduling output for a candidate (compiler_passes' code)."""
    from .app import compiler_passes
    from .network import NetworkError
    found = lookup(net, request)
    if isinstance(found, dict):
        return found
    proj, fn = found
    source = request.get("source")
    if not isinstance(source, str) or not source.strip():
        return {"error": "no source"}
    passes = tuple(request.get("passes") or compiler_passes.SKILL_PASSES)
    files = {} if request.get("save") else None
    flags = _profile_flags(request)
    if isinstance(flags, dict):
        return flags
    try:
        text = compiler_passes.report(net, proj, fn, source, passes, sched_verbose=bool(request.get("sched_verbose")), files=files,
                                      **({"profile_flags": flags} if flags else {}))
        return {"text": text, **({"files": files} if files is not None else {})}
    except NetworkError as error:
        return {"error": str(error)}


def _pseudo_lines(result, _profile) -> dict:
    return {v["pseudo"]: v.get("source_lines") or [] for v in (result.get("allocation") or {}).get("values") or []}


def allocation_report(net, request: dict) -> dict:
    """The allocation dumps of a candidate, and with the judge's rows the explanation of each register they swap."""
    from .app import compiler_passes, register_explain
    from .network import NetworkError
    found = lookup(net, request)
    if isinstance(found, dict):
        return found
    proj, fn = found
    source = request.get("source")
    if not isinstance(source, str) or not source.strip():
        return {"error": "no source"}
    flags = _profile_flags(request)
    if isinstance(flags, dict):
        return flags
    try:
        more = {"profile_flags": flags} if flags else {}
        result, _ = compiler_passes.diagnose(net, proj, fn, source, ("lreg", "greg"), compiler_passes.SKILL_EXCERPT_CHARS, **more)
        # The C lines come from a second compile with -g (its line notes). -g changes KMC GCC's code (a 20 became
        # 180), but not the pseudo numbers expansion hands out, so its lines are used only when it allocated the same
        # pseudos; otherwise the report has no lines.
        try:
            lines = _pseudo_lines(*compiler_passes.diagnose(net, proj, fn, source, (), 0, extra_flags=("-g",), **more))
        except NetworkError:
            lines = {}  # a compiler that refuses -g (SN's) still gives the tables, only without the C lines
    except NetworkError as error:
        return {"error": str(error)}
    phases = result.get("phases") or {}
    lreg, greg = (phases.get(p, {}).get("excerpt", "") for p in ("lreg", "greg"))
    values = (result.get("allocation") or {}).get("values") or []
    if lines and set(lines) == {v["pseudo"] for v in values}:
        values = [dict(v, source_lines=lines[v["pseudo"]]) for v in values]
    reply = {"lreg": lreg, "greg": greg, "allocation": values, "toolchain": result.get("toolchain")}
    if request.get("rows") is not None:
        rows = [tuple(row) for row in request["rows"]]
        reply["text"] = "\n".join(register_explain.explain(lreg, greg, rows, values, source))
    return reply


def neighbours_report(net, request: dict) -> dict:
    """The closest matched neighbours of a function, with snippets (matched_neighbours)."""
    from .app import matched_neighbours
    found = lookup(net, request)
    if isinstance(found, dict):
        return found
    proj, fn = found
    rows = [tuple(row) for row in request.get("rows") or ()] or None
    items = matched_neighbours.gather(net, proj, fn, max(1, min(int(request.get("limit") or 6), 12)), rows=rows)
    text = "\n".join(matched_neighbours.render(items)).strip() or "No matched neighbours yet."
    return {"text": text, "items": [{k: v for k, v in item.items() if k != "source"} for item in items]}


def signatures_report(net, request: dict) -> dict:
    """A source's calls checked against what the project knows of each callee (call_signatures)."""
    from .app import call_signatures
    found = lookup(net, request)
    if isinstance(found, dict):
        return found
    proj, fn = found
    source = request.get("source") or fn["best_source"] or ""
    if not source.strip():
        return {"error": f"{fn['name']} has no saved best yet: pass a source"}
    return signatures_for(net, proj, fn, source, call_signatures)


def signatures_for(net, proj, fn, source, call_signatures=None):
    """{"text", "findings"} for one function's source: its callees' and callers' matched C read from the project."""
    import re as _re
    if call_signatures is None:
        from .app import call_signatures
    store = net.project_store(proj["id"])
    own = call_signatures.body(source, fn["name"]) or source
    names = sorted({c["callee"] for c in call_signatures.calls(own)} - {fn["name"]})
    callees = {r["name"]: r["matched_source"] for r in store.all(
        "SELECT name, matched_source FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT "
        "NULL AND name IN (SELECT value FROM json_each(?))", proj["id"], json.dumps(names))}
    callers = {}
    if fn["vram"] is not None:
        try:
            near = sorted(net.xrefs(proj)["called_by"].get(fn["vram"], ()))
        except Exception:  # noqa: BLE001  (a project with no cross-references still gets the callee checks)
            near = []
        callers = {r["name"]: r["matched_source"] for r in store.all(
            "SELECT name, matched_source FROM functions WHERE project_id=? AND status='matched' AND matched_source IS "
            "NOT NULL AND vram IN (SELECT value FROM json_each(?))", proj["id"], json.dumps(near))}
    # A saved source can call a callee by the address name it had before the project renamed it (`func_80015F0C(0)` for
    # `allocTextNodeObject(void)`): it still links, but the name lookup above misses its matched C, and a stray argument
    # m2c gave it went unreported (200 points of an effect spawner's diff). Read those by address, under the source's name.
    stale = {int(m[1], 16): m[0] for m in (_re.fullmatch(r"func_([0-9A-Fa-f]{8})", n) for n in names) if m}
    stale = {vram: name for vram, name in stale.items() if name not in callees}
    if stale:
        for r in store.all(
                "SELECT name, vram, matched_source FROM functions WHERE project_id=? AND status='matched' AND "
                "matched_source IS NOT NULL AND vram IN (SELECT value FROM json_each(?))", proj["id"],
                json.dumps(sorted(stale))):
            if r["vram"] in stale and r["name"] != stale[r["vram"]]:
                callees[stale[r["vram"]]] = _re.sub(rf"\b{_re.escape(r['name'])}\b", stale[r["vram"]], r["matched_source"])
    asm = fn["asm"] or ""
    addresses = sorted({int(h, 16) for h in _re.findall(r"\bjal\s+func_([0-9A-Fa-f]{8})\b", asm)})
    aliases = {f"func_{r['vram']:08X}": r["name"] for r in store.all(
        "SELECT name, vram FROM functions WHERE project_id=? AND vram IN (SELECT value FROM json_each(?))",
        proj["id"], json.dumps(addresses))}
    header = net._context(proj["id"], fn["unit"])["header"]
    findings = call_signatures.check(fn["name"], source, header, callees, callers, asm, aliases,
                                     mips=proj["arch"] == "mips")
    return {"text": "\n".join(call_signatures.render(findings)), "findings": findings}


def match_candidates(net, request: dict) -> dict:
    """The open functions a small model's matching loop picks from (match_brief; the skill's haiku.py)."""
    from .app import match_brief
    return {"items": match_brief.candidates(net, str(request.get("slug") or ""),
                                            int(request.get("max_insns") or 0))}


def match_brief_report(net, request: dict) -> dict:
    """One function's screen for that loop: assembly, saved C, draft, candidate profiles, neighbours."""
    from .app import match_brief
    return match_brief.brief(net, str(request.get("slug") or ""), str(request.get("name") or ""))


def preprocess_report(net, request: dict) -> dict:
    """A candidate preprocessed exactly as the app's compile does it, and the cc1 command to dump its passes."""
    from . import compiler, toolchains
    from .project import compiler_config, compiler_for
    found = lookup(net, request)
    if isinstance(found, dict):
        return found
    proj, fn = found
    source = request.get("source")
    if not isinstance(source, str) or not source.strip():
        return {"error": "no source"}
    profile = compiler_for(net.cfg(proj), fn["vram"], fn)
    tid = profile.get("toolchain", "")
    if not tid or "cmd" in profile or not toolchains.installed(tid):
        return {"error": "this function's compiler isn't a pinned, installed one; nothing is downloaded"}
    cfg = compiler_config(Path(proj["root"]), {"compiler": {"toolchain": tid, "flags": profile.get("flags", [])}})
    header = net._context(proj["id"], fn["unit"])["header"]
    if toolchains.get(tid).pass_dumps:
        # A driver-run compiler (SN's, under wibo) has no `-E`: its own preprocessor step leaves the .i the compile
        # reads, which the diagnostic compile keeps with the pass dumps.
        from .app import compiler_diagnostics
        result = compiler_diagnostics.diagnose(profile, header, source, fn["name"], root=proj["root"], keep_files=True)
        if "files" not in result or "i" not in result["files"]:
            return {"error": result.get("skipped") or "the compile left no preprocessed file", "log": result.get("error", "")}
        return {"text": result["files"]["i"], "cc1": compiler_diagnostics.driver_cc1_words(cfg), "log": ""}
    return compiler.preprocess_candidate(cfg, header, source)


class Packs:
    """Projects from verification packs in a folder, each judged on a scratch project built from it."""

    def __init__(self, folder: Path, cache: Path):
        self.folder, self.cache = Path(folder), Path(cache)
        self.nets: dict[str, tuple[tuple, object, str]] = {}

    def slugs(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("*.sqlite3"))

    def net(self, slug: str):
        path = self.folder / f"{slug}.sqlite3"
        if not path.is_file():
            return None
        stat = path.stat()
        version = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if slug not in self.nets or self.nets[slug][0] != version:  # (a newer pack: a new scratch project)
            from .app.tool_reader import reader_progress
            from .hosted_pack import digest, scratch
            pack_sha256 = digest(path)
            net, found = scratch(path, self.cache)
            if found != slug:
                raise ValueError(f"{path.name} is the pack of {found!r}")
            reader_progress(net)
            stat = path.stat()
            if version != (stat.st_ino, stat.st_mtime_ns, stat.st_size):
                raise ValueError(f"the pack for {slug} changed while it was opening")
            self.nets[slug] = (version, net, pack_sha256)
        return self.nets[slug][1]

    def describe(self) -> dict:
        from . import __version__, toolchains
        installed = sorted(tid for tid in toolchains.registry() if _installed(toolchains, tid))
        return {"projects": self.slugs(), "toolchains": installed, "version": __version__}

    def judge(self, request: dict) -> dict:
        slug = str(request.get("slug") or "")
        net = self.net(slug)
        if net is None:
            return {"error": f"this computer has no verification pack for {request.get('slug')!r}"}
        pack_sha256 = request.get("pack_sha256")
        if pack_sha256 and pack_sha256 != self.nets[slug][2]:
            return {"error": "the verification pack is a different revision"}
        reply = judge(net, request)
        if pack_sha256 and "error" not in reply:
            reply["pack_sha256"] = pack_sha256
        return reply


def _judge_with_mips_linker(packs, request, lines, out):
    from .rom.match import elf_preparer, instruction_linker, rom_linker

    def exchange(operation, plan, lookup=None):
        out.write(json.dumps({operation: plan}) + "\n")
        out.flush()
        for line in lines:
            reply = json.loads(line)
            if isinstance(reply, dict) and "error" in reply:
                raise RuntimeError(reply["error"])
            if lookup is None or not isinstance(reply, dict) or "rom_lookup" not in reply:
                return reply
            try:
                value = {"rom_value": lookup(reply["rom_lookup"])}
            except Exception as error:
                value = {"error": str(error)}
            out.write(json.dumps(value) + "\n")
            out.flush()
        raise EOFError("verifier ended during a MIPS link request")

    def link(code, target, patches):
        plan = {"code": code.hex(), "target": target.code.hex(), "big_endian": target.big_endian,
                "gp": target.gp & 0xFFFFFFFF, "patches": patches}
        reply = exchange("link_mips", plan)
        return bytes.fromhex(reply["linked"]), reply["instructions_exact"]

    def prepare(obj, name):
        return exchange("parse_mips", {"object": obj.hex(), "name": name})

    def link_rom(obj, target, symbols, read):
        from .rom.verifier_link import link_rom as run

        return run(obj, target, symbols, read, exchange)

    rom_token = rom_linker.set(link_rom if request.get("rom_mips") == 1 else None)
    link_token = instruction_linker.set(link)
    prepare_token = elf_preparer.set(prepare if request.get("parse_mips") == 1 else None)
    try:
        return packs.judge(request)
    finally:
        elf_preparer.reset(prepare_token)
        instruction_linker.reset(link_token)
        rom_linker.reset(rom_token)


def serve(net, lines=sys.stdin, out=sys.stdout) -> None:
    """Answer requests from a home's network, or from Packs."""
    packs = net if isinstance(net, Packs) else None
    lines = iter(lines)
    for line in lines:
        try:
            request = json.loads(line)
            if request.get("op") == "describe":
                reply = packs.describe() if packs else describe(net)
            elif request.get("op") == "judge":
                if packs and request.get("pack_sha256") and request.get("link_mips") == 1:
                    reply = _judge_with_mips_linker(packs, request, lines, out)
                else:
                    reply = packs.judge(request) if packs else judge(net, request)
            elif request.get("op") == "function" and not packs:
                reply = function_info(net, request)
            elif request.get("op") == "passes" and not packs:
                reply = passes_report(net, request)
            elif request.get("op") == "allocation" and not packs:
                reply = allocation_report(net, request)
            elif request.get("op") == "neighbours" and not packs:
                reply = neighbours_report(net, request)
            elif request.get("op") == "signatures" and not packs:
                reply = signatures_report(net, request)
            elif request.get("op") == "candidates" and not packs:
                reply = match_candidates(net, request)
            elif request.get("op") == "brief" and not packs:
                reply = match_brief_report(net, request)
            elif request.get("op") == "preprocess" and not packs:
                reply = preprocess_report(net, request)
            else:
                reply = {"error": f"unknown op {request.get('op')!r}"}
        except Exception as error:  # this request only: the verifier releases it for another
            reply = {"error": f"{type(error).__name__}: {error}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


def main(home: Path, packs: Path | None = None, cache: Path | None = None) -> int:
    if packs is not None:
        out, sys.stdout = sys.stdout, sys.stderr
        serve(Packs(packs, cache or Path(packs) / ".judge-cache"), sys.stdin, out)
        return 0
    from . import store as storage
    storage.ATTACH_ONLY = True
    from .app.engine import RomNetwork
    from .app.tool_reader import reader_progress
    net = RomNetwork(Path(home) / ".decomp" / "decomp.db", Path(home), project_databases=True)
    reader_progress(net)  # never synchronizes saved progress folders: the app keeps them
    # Replies go on the real stdout; anything else printed (compiler chatter) goes to stderr.
    out, sys.stdout = sys.stdout, sys.stderr
    serve(net, sys.stdin, out)
    return 0
