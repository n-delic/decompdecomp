#!/usr/bin/env python3
"""The matching loop a small model can run: one command to get a function, one to try C, one to end (HAIKU.md).

    haiku.py next SLUG [FUNC] [--repo CHECKOUT] [--home DIR] [--holder NAME] [--max-size N]
                         start a session (setup.py: private worktree, venv, MCP connection, claim renewal), claim FUNC or
                         the most promising open function of at most N=150 instructions (recently tried ones last,
                         the most given up last of all; library names never), and print one screen: assembly, saved C, a fresh machine draft,
                         an earlier worker's near-miss under another profile, every candidate profile's score, the
                         matched twin's C, neighbours' signatures
    haiku.py try HOLDER CAND.c
                         judge CAND.c under every candidate profile; submit it when it beats the saved score; when it is
                         exact, make it the match (under another profile: `suggest`, for this function only).
                         Scaffolding the starting C already has may stay while improving; adding more is refused, and
                         an exact C with any is not saved as a match (it goes into the note)
    haiku.py done HOLDER [--note TEXT]     (also: giveup)
                         submit the best candidate seen, keep a better one under another profile as a compiler trial
                         (compiler_experiments: nothing is applied; the next `next` shows it), write the note, release
                         the claim, remove the session (with no try and no --note: only the claim and session go)

ROM projects only (project mode has no isolated judge). It never changes a compiler range or the project default, never
prints the connection token, and keeps every file in the session folder (/tmp/dd-HOLDER/).
"""
import argparse
import contextlib
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402
import fast_judge  # noqa: E402  (the app's judge-worker)
import handoff  # noqa: E402  (the claim token)
import judge  # noqa: E402  (the app's window API, for a function's status)
import setup  # noqa: E402  (sessions, the gateway, submissions)

MAX_SIZE = 150
TRIES = 15
ASM_LINES, C_LINES, ROW_LINES = 160, 70, 40
TWIN_LINES = 100
LINE_WIDTH = 240
PICK_ATTEMPTS = 5
RELEASE_WAIT_SECONDS = 600
VERIFY_SECONDS = 90


# --- the sources a matcher may not send -------------------------------------------------------------------------------

def _code(source):
    """The source without comments and string literals."""
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', " ", source, flags=re.S)


def _without_records(code):
    """Without struct and union bodies: a struct's `pad` members are layout, not stack padding."""
    while True:
        found = re.search(r"\b(?:struct|union)\b[^;{}()]*\{", code)
        if not found:
            return code
        depth, end = 1, found.end()
        while end < len(code) and depth:
            depth += {"{": 1, "}": -1}.get(code[end], 0)
            end += 1
        code = code[:found.start()] + " record " + code[end:]


def scaffolding(source):
    """{item: how often} of the scaffolding in `source`: constructs that score without being a program anybody wrote."""
    code = _code(source)
    found = {}
    fences = len(re.findall(r"\bwhile\s*\(\s*0[uUlL]*\s*\)", code))
    if fences:
        found["fence"] = fences
    found.update({f"repair:{name}": 1 for name in re.findall(r"\w*__repair_\w*", code)})
    volatiles = len(re.findall(r"\bvolatile\b", code))
    if volatiles:
        found["volatile"] = volatiles
    body, depth = [], 0
    for char in _without_records(code):
        depth += {"{": 1, "}": -1}.get(char, 0)
        body.append(char if depth > 0 else "\n")
    inside = "".join(body)
    for statement in re.split(r"[;{}]", inside):
        array = re.match(r"\s*(?!return\b|extern\b|typedef\b|goto\b|case\b)(?:(?:static|const|register|signed|unsigned"
                         r"|struct|union)\s+)*[A-Za-z_]\w*[\s*]+([A-Za-z_]\w*)\s*\[[^\]]*\]", statement)
        if not array:
            continue
        name = array[1]
        if re.match(r"_*(pad|dummy|unused|filler)", name, re.I) or len(re.findall(rf"\b{name}\b", inside)) == 1:
            found[f"padding:{name}"] = 1
    return found


def inherited(sources):
    """The scaffolding the C a session starts from already has: the most of each item in any one of `sources`."""
    found = {}
    for source in sources:
        for item, count in scaffolding(source or "").items():
            found[item] = max(count, found.get(item, 0))
    return found


def faults(source, allowed=None):
    """Why `source` may not be sent: its scaffolding beyond `allowed` ({item: how often}, from `inherited`)."""
    allowed = allowed or {}
    over = [item for item, count in scaffolding(source).items() if count > allowed.get(item, 0)]
    found = []
    if "fence" in over:
        found.append("a `do { } while (0)` fence (or `while (0)`): delete it and write the statements plainly")
    if any(item.startswith("repair:") for item in over):
        found.append("a `__repair_` local or helper from a search: rewrite it as ordinary C")
    if "volatile" in over:
        found.append("`volatile`: remove it")
    found.extend(f"the padding array `{item.split(':', 1)[1]}[]` (declared but not used for anything): delete it"
                 for item in over if item.startswith("padding:"))
    return found


# --- judging ----------------------------------------------------------------------------------------------------------

def label(profile):
    return f"{profile['toolchain']} {' '.join(profile['flags'])}"


class Worker:
    """One judge-worker of the session, for any function and any profile."""

    def __init__(self, info):
        venv = Path(info.get("venv") or "") / "bin" / "decomp"
        self.slug = info["slug"]
        self.judge = fast_judge.Judge(self.slug, "", vram=0, target_hex="", toolchain="-", flags=[],
                                      decomp=str(venv) if venv.exists() else None, home=info.get("home"))

    def ask(self, request):
        return self.judge.ask(dict(request, slug=self.slug))

    def score(self, function, profile, source, rows=False):
        """The judge's reply under one profile; {"score": None, "detail": why} when it can't be judged."""
        if not source.strip():
            return {"score": None, "detail": "no source"}
        request = {"op": "judge", "function": {k: function[k] for k in ("name", "vram", "target")},
                   "compiler": {"toolchain": profile["toolchain"], "flags": profile["flags"]}, "source": source}
        try:
            return self.ask(dict(request, **({"diff": True, "rows": ROW_LINES} if rows else {})))
        except SystemExit as error:  # this profile only (a compiler that isn't here)
            return {"score": None, "detail": str(error)}

    def close(self):
        self.judge.close()


def scores(worker, function, profiles, source):
    return [worker.score(function, profile, source) for profile in profiles]


def shown(score):
    return "no compile" if score is None else "EXACT" if score == 0 else str(score)


def rank(worker, items):
    """The shortlist, most promising first: functions nobody worked on lately by the lowest score any profile gives
    the saved C or the draft, per instruction, discounted for matched neighbours to learn from; then the others, those
    given up least first and, among equals, the longest untouched."""
    ranked = []
    for item in items:
        found = [s["score"] for source in dict.fromkeys((item["best"], item["draft"], item.get("near"))) if source
                 for s in scores(worker, item, item["profiles"], source)]
        found = [s for s in found + [item["best_score"]] if s is not None]
        if found:
            value = min(found) / max(1, item["n_insns"] or 1) / (1 + 0.25 * min(4, item["matched_near"]))
            value *= 1 + item.get("abandons", 0)
            worked = item.get("worked", 0)
            ranked.append((bool(worked), item.get("abandons", 0) if worked else 0, worked, value, item["n_insns"],
                           item["name"]))
    return [name for *_, name in sorted(ranked)]


# --- next -------------------------------------------------------------------------------------------------------------

def picker(function, max_size):
    def pick(gateway, info):
        worker = Worker(info)
        try:
            # asked first even for a named function: a project this loop can't judge (project mode) is refused
            # before anything is claimed
            items = worker.ask({"op": "candidates", "max_insns": 1 if function else max_size})["items"]
            names = [] if function else rank(worker, items)
        finally:
            worker.close()
        if function:
            return setup.claim(gateway, function)
        refusals = []
        for name in names[:PICK_ATTEMPTS]:
            try:
                return setup.claim(gateway, name)
            except setup.McpError as error:  # someone else took it, or it was tried: a refusal claims nothing
                refusals.append(f"{name}: {error}")
        raise setup.McpError("no function could be claimed: " + ("; ".join(refusals) or
                             f"no open function of at most {max_size} instructions with C to start from"))
    return pick


def clipped(text, lines, path):
    """At most `lines` lines of at most LINE_WIDTH characters: a search's saved C can be a few huge lines."""
    rows = (text or "").strip().splitlines()
    cut = [row if len(row) <= LINE_WIDTH else row[:LINE_WIDTH] + " ..." for row in rows[:lines]]
    if cut == rows:
        return "\n".join(rows) or "(none)"
    return "\n".join(cut + [f"... cut here: the whole text is in {path}"])


def differing(diff, path):
    """The judge's rows without its advice, which names tools this loop doesn't use."""
    start = diff.find("markers:")
    return clipped(diff[max(0, start):], ROW_LINES, path)


def read_state(root):
    try:
        return json.loads((root / "haiku.json").read_text())
    except (OSError, ValueError):
        sys.exit(f"no `haiku.py next` session in {root}: start one with haiku.py next SLUG")


def write_state(root, state):
    (root / "haiku.json").write_text(json.dumps(state, indent=1))


def same_profile(one, other):
    return (one["toolchain"], list(one["flags"])) == (other["toolchain"], list(other["flags"]))


def screen(root, info, brief, worker, write=print):
    """Print the one screen; returns (the scores per profile, the kept near-miss's score under its profile now, the
    scaffolding the starting C already has)."""
    folder = root / "start"
    folder.mkdir(exist_ok=True)
    twin = brief.get("twin")
    came_with = [brief["best"], brief["draft"], (twin or {}).get("source")]
    allowed = inherited(came_with)
    near = next((t for t in brief.get("trials") or [] if not faults(t["source"], allowed)), None)
    if near:
        allowed = inherited([*came_with, near["source"]])
    sources = {"best": brief["best"], "draft": brief["draft"], **({"near": near["source"]} if near else {})}
    files = {key: folder / f"{key}.c" for key in sources}
    files["asm"] = folder / "asm.s"
    for key, path in files.items():
        path.write_text((brief["asm"] if key == "asm" else sources[key]) or "")
    holder, profiles = info["holder"], brief["profiles"]
    write(f"=== {brief['name']} ({info['slug']}), {brief['n_insns']} instructions   holder {holder}\n"
          f"Write your C in {root}/work.c, then: haiku.py try {holder} {root}/work.c\n"
          f"End (matched or not): haiku.py done {holder}")
    write(f"\n--- target assembly\n{clipped(brief['asm'], ASM_LINES, files['asm'])}")
    write(f"\n--- saved best C (app score {brief['best_score']})\n{clipped(brief['best'], C_LINES, files['best'])}")
    write(f"\n--- fresh machine draft\n{clipped(brief['draft'], C_LINES, files['draft'])}")
    if near:
        write(f"\n--- near-miss kept from an earlier session: score {near['score']} under {label(near)}\n"
              f"{clipped(near['source'], C_LINES, files['near'])}")
    if twin:
        path = folder / "twin.c"
        path.write_text(twin["source"])
        write(f"\n--- MATCHED twin {twin['name']}: {round(twin['similarity'] * 100)}% the same instructions, "
              f"built with {label(twin)}. Copy it and change names and constants.\n"
              f"{clipped(twin['source'], TWIN_LINES, path)}")
    keys = list(sources)
    write("\n--- scores per profile (0 = exact; 'own' is what the app uses now)\n    "
          + "".join(f"{'saved' if key == 'best' else key:>11}" for key in keys) + "  profile")
    results = []
    for index, profile in enumerate(profiles):
        pair = [worker.score(brief, profile, sources[key])["score"] for key in keys]
        results.append(pair)
        write(f"  {index} " + "".join(f"{shown(score):>11}" for score in pair) + f"  {label(profile)}"
              f"{'  (own)' if profile['own'] else ''}{'  (twin)' if twin and same_profile(twin, profile) else ''}"
              f"  [{profile['uses']} matched]")
    ranked = [(score, i, key) for i, pair in enumerate(results) for key, score in zip(keys, pair) if score is not None]
    if ranked:
        score, index, key = min(ranked)
        name = {"best": "saved C", "draft": "draft", "near": "near-miss"}[key]
        write(f"Closest start: the {name} under profile {index} ({shown(score)}): "
              f"cp {files[key]} {root}/work.c")
        rows = worker.score(brief, profiles[index], sources[key], rows=True).get("diff")
        if rows:
            (folder / "rows.txt").write_text(rows)
            write(f"Its differing rows (target | yours):\n{differing(rows, folder / 'rows.txt')}")
    if allowed:
        write("\nThe C above came with scaffolding (" + ", ".join(sorted({i.split(":")[0] for i in allowed}))
              + "). `try` accepts what is already there, so start from it and keep your progress; adding more is "
              "refused. A match is only saved without any: remove it when you can.")
    if brief.get("declarations"):
        write("\n--- shared header declarations (already included; do not redeclare differently)")
        write("\n".join(brief["declarations"]))
    write("\n--- nearest matched neighbours")
    write("\n".join(f"{n['signature'] or n['name']};  // {n['name']}, {', '.join(n['relations'])}"
                    for n in brief.get("neighbours") or []) or "(none matched yet)")
    kept = next((pair[keys.index("near")] for profile, pair in zip(profiles, results)
                 if near and same_profile(near, profile)), None)
    return results, kept, allowed


def next_function(args):
    holder = setup.holder_name(args.holder)
    root = setup.session_dir(holder)
    if not (root / "haiku.json").exists():
        start = argparse.Namespace(slug=args.slug, function=args.function, holder=holder, home=args.home,
                                   repo=args.repo, interval=setup.RENEW_SECONDS, random=False, max_size=args.max_size,
                                   no_sync=args.no_sync, no_orient=True, picker=picker(args.function, args.max_size))
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said):  # setup's own summary: not this screen
                setup.start(start)
        except SystemExit:
            print(said.getvalue(), end="")
            if (root / "connection.json").exists():
                print(f"The session is kept: run `haiku.py next {args.slug} --holder {holder}` again.")
            raise
    info = setup.read_session(root)
    if not info.get("function"):
        sys.exit(f"{holder} holds no function any more: run `haiku.py next {args.slug}` without --holder")
    worker = Worker(info)
    try:
        brief = worker.ask({"op": "brief", "name": info["function"]})
        first = not (root / "haiku.json").exists()
        results, kept, allowed = screen(root, info, brief, worker)
        if first:
            write_state(root, {"function": {k: brief[k] for k in ("name", "vram", "target")},
                               "profiles": brief["profiles"], "tries": 0, "best": {}, "submitted": [],
                               "start": results, "app_score": brief["best_score"], "kept": kept,
                               "inherited": allowed})
    finally:
        worker.close()
    return 0


# --- try --------------------------------------------------------------------------------------------------------------

def app_function(info, name):
    return judge.App(info["home"]).function(info["slug"], name)


def wait_matched(info, name):
    deadline = time.time() + VERIFY_SECONDS
    while True:
        if app_function(info, name).get("status") == "matched":
            return True
        if time.time() >= deadline:
            return False
        time.sleep(3)


def digest(source):
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def submit(holder, root, state, source):
    """compile_and_diff as the session, once per source."""
    text = setup.submit(holder, source, state["function"]["name"], again="the same haiku.py command")
    state["submitted"].append(digest(source))
    write_state(root, state)
    return text


def apply_profile(info, name, profile, source):
    """`suggest` with the exact C for this function only: no ranges, so the app saves source, header and flags
    together for it and nothing else."""
    gateway = setup.Gateway(info["gateway_url"], info["token"])
    gateway.start()
    return gateway.call("suggest", {"function": name, "compiler": profile["toolchain"],
                                    "flags": " ".join(profile["flags"]), "source": source,
                                    "text": f"Exact under {label(profile)} for {name} only."})


def try_source(holder, path):
    holder = setup.holder_name(holder)
    root = setup.session_dir(holder)
    info, state = setup.read_session(root), read_state(root)
    function, profiles = state["function"], state["profiles"]
    name = function["name"]
    try:
        source = Path(path).read_text()
    except OSError as error:
        sys.exit(f"can't read {path}: {error}")
    problems = faults(source, state.get("inherited"))
    if problems:
        sys.exit("NOT judged. You added scaffolding the C you started from doesn't have; remove it:\n- "
                 + "\n- ".join(problems))
    claimlost.warn(root)
    if claimlost.taken(claimlost.read(root)):
        sys.exit(f"{name} is no longer yours (matched, or someone else holds it): run `haiku.py done {holder}`")
    worker = Worker(info)
    try:
        replies = scores(worker, function, profiles, source)
        found = [r["score"] for r in replies]
        state["tries"] += 1
        print(f"try {state['tries']} of about {TRIES} for {name}")
        for index, (profile, score) in enumerate(zip(profiles, found)):
            print(f"  {index}  {shown(score):>10}  {label(profile)}{'  (own)' if profile['own'] else ''}")
        if all(score is None for score in found):
            write_state(root, state)
            print("It did not compile:\n" + (replies[0].get("detail") or "")[-1500:])
            return 1
        best = min((score, index) for index, score in enumerate(found) if score is not None)[1]
        left = faults(source)  # inherited: fine for progress, never for a match
        for index, score in enumerate(found):
            if score == 0 and left:  # kept apart: `done` must not submit it as a match
                copy = root / f"exact-scaffolded-profile{index}.c"
                copy.write_text(source)
                state["scaffolded"] = {"profile": index, "path": str(copy)}
                continue
            kept = state["best"].get(str(index))
            if score is not None and (kept is None or score < kept["score"]):
                copy = root / f"best-profile{index}.c"
                copy.write_text(source)
                state["best"][str(index)] = {"score": score, "path": str(copy)}
        write_state(root, state)
        own = next(i for i, p in enumerate(profiles) if p["own"])
        current = app_function(info, name)
        if current.get("status") == "matched":
            print(f"{name} is ALREADY MATCHED. Run: haiku.py done {holder}")
            return 0
        exact = own if found[own] == 0 else next((i for i, s in enumerate(found) if s == 0), None)
        saved = current.get("best")
        if exact is not None and left:
            print(f"EXACT under profile {exact} ({label(profiles[exact])}) but NOT saved: it still has scaffolding.\n- "
                  + "\n- ".join(left) + "\nRemove it one piece at a time and `try` again. If it can't go, run: haiku.py "
                  f"done {holder}   (the C is kept in the function's note) and report it.")
            return 1
        if exact is not None:
            if exact == own:
                print(submit(holder, root, state, source))
            else:
                print(f"EXACT under profile {exact} ({label(profiles[exact])}): asking the app to save it for "
                      f"{name} only")
                try:
                    print(apply_profile(info, name, profiles[exact], source))
                except setup.McpError as error:
                    print(f"the app refused the profile change: {error}")
            if wait_matched(info, name):
                print(f"\nMATCHED: the app records {name} as matched"
                      f"{'' if exact == own else f' with {label(profiles[exact])}'}. Stop editing and run: "
                      f"haiku.py done {holder}")
                return 0
            print(f"\nExact here, but the app does not record {name} as matched yet (read its answer above). "
                  f"Run: haiku.py done {holder}   and report both.")
            return 1
        if found[own] is not None and (saved is None or found[own] < saved):
            text = submit(holder, root, state, source)
            print(f"submitted: it beats the saved score {saved} -> {found[own]}. {text.strip().splitlines()[0][:200]}")
        else:
            print(f"not submitted: the saved score under the own profile is {saved}")
        rows = worker.score(function, profiles[best], source, rows=True).get("diff") or ""
        (root / "rows.txt").write_text(rows)
        print(f"differing rows under profile {best} (target | yours):\n{differing(rows, root / 'rows.txt')}")
        if state["tries"] >= TRIES:
            print(f"That was {state['tries']} tries. Unless the score is still falling, run: haiku.py done {holder}")
        return 1
    finally:
        worker.close()


# --- done -------------------------------------------------------------------------------------------------------------

def summary(state, note, near=""):
    """The release note: how far each profile got."""
    profiles = state["profiles"]
    lines = [f"Small-model attempt at {state['function']['name']}: {state['tries']} tries."]
    for index, kept in sorted(state["best"].items(), key=lambda item: item[1]["score"]):
        lines.append(f"  best {kept['score']} under {label(profiles[int(index)])}")
    if near:
        lines.append(near)
    if note:
        lines.append(note)
    return "\n".join(lines)


def keep_work(holder, root, info, state):
    """Send the best C seen under the own profile when the app doesn't have it yet, before anything is removed."""
    own = str(next(i for i, p in enumerate(state["profiles"]) if p["own"]))
    mine = state["best"].get(own)
    if not mine:
        return
    saved = app_function(info, state["function"]["name"]).get("best")
    source = Path(mine["path"]).read_text()
    if (saved is None or mine["score"] < saved) and digest(source) not in state["submitted"]:
        print(f"submitting the best candidate ({mine['score']}) before ending")
        try:
            submit(holder, root, state, source)
        except SystemExit as error:  # refused: end anyway. Still in flight: stop here, nothing is removed.
            if not str(error).startswith("not submitted"):
                raise
            print(error)


def scaffolded_note(state):
    """The note's copy of an exact C that was not saved because it still has scaffolding."""
    kept = state.get("scaffolded")
    if not kept:
        return ""
    return (f"EXACT C under {label(state['profiles'][kept['profile']])}, not saved because it still has scaffolding:\n"
            f"{Path(kept['path']).read_text()[:6000]}")


def candidate(gateway, name):
    """(revision, source) of the C the app currently holds for this session, checked against the revision."""
    text = gateway.call("get_candidate", {"function": name})
    found = re.search(r"Candidate revision ([0-9a-f]{20})\n```c\n", text)
    if found:
        rest = text[found.end():]
        for end in re.finditer(r"\n```", rest):
            source = rest[:end.start()]
            if hashlib.sha256(source.encode()).hexdigest()[:20] == found[1]:
                return found[1], source
    raise setup.McpError(f"get_candidate gave no candidate to experiment on: {text[:200]}")


def keep_near(holder, root, info, state):
    """Keep the closest candidate of another profile where it survives the session: a `compiler_experiments` trial
    records the C, the profile and the score with the function and applies nothing; the next `next` shows it. Returns
    the note's line about it, or "". (Small-model runs lost dozens of -O1 near-misses of -O2 functions: only an
    own-profile improvement was ever submitted.)"""
    profiles = state["profiles"]
    others = [(kept["score"], int(i)) for i, kept in state["best"].items() if not profiles[int(i)]["own"]]
    if not others:
        return ""
    score, index = min(others)
    mine = next((kept["score"] for i, kept in state["best"].items() if profiles[int(i)]["own"]), None)
    if (mine is not None and score >= mine) or (state.get("kept") is not None and score >= state["kept"]):
        return ""
    name, profile = state["function"]["name"], profiles[index]
    source = Path(state["best"][str(index)]["path"]).read_text()
    try:
        gateway = setup.Gateway(info["gateway_url"], info["token"])
        gateway.start()
        revision, current = candidate(gateway, name)
        if not current:  # an experiment patches the candidate, and there is none yet
            submit(holder, root, state, source)
            revision, current = candidate(gateway, name)
        # the whole candidate is replaced by ours, so the trial is exactly the C that scored
        trial = {"compiler": profile["toolchain"], "flags": " ".join(profile["flags"]),
                 **({} if current == source else {"old": current, "new": source})}
        text = gateway.call("compiler_experiments", {"function": name, "revision": revision, "trials": [trial]})
        found = re.search(r"trial_id=([0-9a-f]+)", text)
        if not found:
            raise setup.McpError(text[:300])
    except (setup.McpError, SystemExit) as error:
        print(f"the near-miss could not be recorded as a trial ({error}); it goes into the note instead")
        return f"Near-miss {score} under {label(profile)}:\n{source[:3000]}"
    print(f"kept the near-miss ({score} under {label(profile)}) as compiler trial {found[1]}; nothing was applied")
    return f"Near-miss {score} under {label(profile)} kept as compiler trial {found[1]} (not applied)."


def done(holder, note=""):
    holder = setup.holder_name(holder)
    root = setup.session_dir(holder)
    info = setup.read_session(root)
    if not (root / "haiku.json").exists():  # a `next` that stopped half way
        return setup.stop_session(holder)
    state = read_state(root)
    name = state["function"]["name"]
    if not state["tries"] and not note:  # nothing was learned: no note and no give-up on the function
        return setup.stop_session(holder)
    current = app_function(info, name)
    lost = claimlost.taken(claimlost.read(root))
    near = ""
    if current.get("status") != "matched" and not lost:
        keep_work(holder, root, info, state)
        near = keep_near(holder, root, info, state)
        near = "\n".join(part for part in (near, scaffolded_note(state)) if part)
        current = app_function(info, name)
    if current.get("status") == "matched":
        setup.stop_session(holder)
        print(f"RESULT {name}: MATCHED")
        return 0
    text = summary(state, note, near)
    try:
        gateway = setup.Gateway(info["gateway_url"], info["token"])
        gateway.start()
        try:
            gateway.call("add_note", {"function": name, "text": text})
        except setup.McpError as error:
            print(f"the note was not saved: {error}")
        token = handoff.claim_token(gateway, name)
        setup.release_session(holder)  # first: the renewal must not claim it back
        if token is not None:
            # move_on with notes: the app's "compile more first" push-back never applies, whatever the count
            gateway.call("release_function", {"function": name, "claim_token": token, "move_on": True, "notes": text})
            deadline = time.time() + RELEASE_WAIT_SECONDS
            while handoff.claim_token(gateway, name) is not None and time.time() < deadline:
                time.sleep(5)  # releases lag behind their reply
    except setup.McpError as error:
        print(f"release: {error} (ending the session frees the claim)")
    setup.stop_session(holder)
    best = min((kept["score"] for kept in state["best"].values()), default=None)
    print(f"RESULT {name}: not matched; app best {current.get('best')}, best seen under any profile {best}, "
          f"{state['tries']} tries. Released."
          + (" An EXACT C with scaffolding is in the note (not saved as a match)." if state.get("scaffolded") else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    one = sub.add_parser("next")
    one.add_argument("slug")
    one.add_argument("function", nargs="?", default="")
    one.add_argument("--holder")
    one.add_argument("--home")
    one.add_argument("--repo")
    one.add_argument("--max-size", type=int, default=MAX_SIZE)
    one.add_argument("--no-sync", action="store_true", help=argparse.SUPPRESS)  # tests build no venv
    two = sub.add_parser("try")
    two.add_argument("holder")
    two.add_argument("source")
    for word in ("done", "giveup"):
        end = sub.add_parser(word)
        end.add_argument("holder")
        end.add_argument("--note", default="")
    a = ap.parse_args(argv)
    try:
        if a.command == "next":
            return next_function(a)
        if a.command == "try":
            return try_source(a.holder, a.source)
        return done(a.holder, a.note)
    except setup.McpError as error:
        sys.exit(f"the app refused or is not answering: {error}\nNothing was removed; run the same command again.")


if __name__ == "__main__":
    sys.exit(main())
