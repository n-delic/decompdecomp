#!/usr/bin/env python3
"""The matching loop a small model can run: one command to get a function, one to try C, one to end (HAIKU.md).

    haiku.py next SLUG [FUNC] [--repo CHECKOUT] [--home DIR] [--holder NAME] [--max-size N]
                         start a session (setup.py: private worktree, venv, MCP connection, claim renewal), claim FUNC or
                         the most promising open function of at most N=150 instructions, and print one screen: assembly,
                         saved C, a fresh machine draft, every candidate profile's score, neighbours' signatures
    haiku.py try HOLDER CAND.c
                         judge CAND.c under every candidate profile; submit it when it beats the saved score; when it is
                         exact, make it the match (under another profile: `suggest`, for this function only)
    haiku.py done HOLDER [--note TEXT]     (also: giveup)
                         submit the best candidate seen, write the note, release the claim, remove the session

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


def faults(source):
    """Why `source` may not be sent: scaffolding that scores without being a program anybody wrote."""
    code = _code(source)
    found = []
    if re.search(r"\bwhile\s*\(\s*0[uUlL]*\s*\)", code):
        found.append("a `do { } while (0)` fence (or `while (0)`): delete it and write the statements plainly")
    if "__repair_" in code:
        found.append("a `__repair_` local or helper from a search: rewrite it as ordinary C")
    if re.search(r"\bvolatile\b", code):
        found.append("`volatile`: remove it")
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
            found.append(f"the padding array `{name}[]` (declared but not used for anything): delete it")
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
    """The shortlist, most promising first: the lowest score any profile gives the saved C or the draft, per
    instruction, discounted for matched neighbours to learn from."""
    ranked = []
    for item in items:
        found = [s["score"] for source in dict.fromkeys((item["best"], item["draft"])) if source
                 for s in scores(worker, item, item["profiles"], source)]
        found = [s for s in found + [item["best_score"]] if s is not None]
        if found:
            value = min(found) / max(1, item["n_insns"] or 1) / (1 + 0.25 * min(4, item["matched_near"]))
            ranked.append((value, item["n_insns"], item["name"]))
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


def screen(root, info, brief, worker, write=print):
    folder = root / "start"
    folder.mkdir(exist_ok=True)
    files = {"asm": folder / "asm.s", "best": folder / "best.c", "draft": folder / "draft.c"}
    for key, path in files.items():
        path.write_text(brief[key] or "")
    holder, profiles = info["holder"], brief["profiles"]
    write(f"=== {brief['name']} ({info['slug']}), {brief['n_insns']} instructions   holder {holder}\n"
          f"Write your C in {root}/work.c, then: haiku.py try {holder} {root}/work.c\n"
          f"End (matched or not): haiku.py done {holder}")
    write(f"\n--- target assembly\n{clipped(brief['asm'], ASM_LINES, files['asm'])}")
    write(f"\n--- saved best C (app score {brief['best_score']})\n{clipped(brief['best'], C_LINES, files['best'])}")
    write(f"\n--- fresh machine draft\n{clipped(brief['draft'], C_LINES, files['draft'])}")
    write("\n--- scores per profile (0 = exact; 'own' is what the app uses now)\n      saved    draft  profile")
    results = []
    for index, profile in enumerate(profiles):
        pair = [worker.score(brief, profile, brief[key])["score"] for key in ("best", "draft")]
        results.append(pair)
        write(f"  {index}  {shown(pair[0]):>9} {shown(pair[1]):>8}  {label(profile)}"
              f"{'  (own)' if profile['own'] else ''}  [{profile['uses']} matched]")
    ranked = [(score, i, key) for i, pair in enumerate(results) for key, score in zip(("best", "draft"), pair)
              if score is not None]
    if ranked:
        score, index, key = min(ranked)
        write(f"Closest start: the {'saved C' if key == 'best' else 'draft'} under profile {index} ({shown(score)}).")
        rows = worker.score(brief, profiles[index], brief[key], rows=True).get("diff")
        if rows:
            (folder / "rows.txt").write_text(rows)
            write(f"Its differing rows (target | yours):\n{differing(rows, folder / 'rows.txt')}")
        problems = faults(brief[key] or "")
        if problems:
            write("It contains scaffolding you must remove before `try`: " + "; ".join(problems))
    if brief.get("declarations"):
        write("\n--- shared header declarations (already included; do not redeclare differently)")
        write("\n".join(brief["declarations"]))
    write("\n--- nearest matched neighbours")
    write("\n".join(f"{n['signature'] or n['name']};  // {n['name']}, {', '.join(n['relations'])}"
                    for n in brief.get("neighbours") or []) or "(none matched yet)")
    return results


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
        results = screen(root, info, brief, worker)
        if first:
            write_state(root, {"function": {k: brief[k] for k in ("name", "vram", "target")},
                               "profiles": brief["profiles"], "tries": 0, "best": {}, "submitted": [],
                               "start": results, "app_score": brief["best_score"]})
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
    problems = faults(source)
    if problems:
        sys.exit("NOT judged. Remove this scaffolding first:\n- " + "\n- ".join(problems))
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
        for index, score in enumerate(found):
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

def summary(state, note):
    """The release note: how far each profile got."""
    profiles = state["profiles"]
    lines = [f"Small-model attempt at {state['function']['name']}: {state['tries']} tries."]
    for index, kept in sorted(state["best"].items(), key=lambda item: item[1]["score"]):
        lines.append(f"  best {kept['score']} under {label(profiles[int(index)])}")
    if note:
        lines.append(note)
    return "\n".join(lines)


def keep_work(holder, root, info, state):
    """Send the best C seen that the app doesn't have yet, before anything is removed: the best under the own
    profile, and the closest one under another profile, when they beat the saved score (the app keeps both in the
    function's attempts)."""
    if not state["best"]:
        return
    own = str(next(i for i, p in enumerate(state["profiles"]) if p["own"]))
    saved = app_function(info, state["function"]["name"]).get("best")
    wanted = []
    mine = state["best"].get(own)
    if mine and (saved is None or mine["score"] < saved):
        wanted.append(mine)
    index, closest = min(state["best"].items(), key=lambda item: item[1]["score"])
    if index != own and (saved is None or closest["score"] < saved) and (not mine or closest["score"] < mine["score"]):
        wanted.insert(0, closest)  # first, so the own-profile best stays the latest candidate
    for kept in wanted:
        source = Path(kept["path"]).read_text()
        if digest(source) not in state["submitted"]:
            print(f"submitting the best candidate ({kept['score']}) before ending")
            try:
                submit(holder, root, state, source)
            except SystemExit as error:  # refused: end anyway. Still in flight: stop here, nothing is removed.
                if not str(error).startswith("not submitted"):
                    raise
                print(error)


def done(holder, note=""):
    holder = setup.holder_name(holder)
    root = setup.session_dir(holder)
    info = setup.read_session(root)
    if not (root / "haiku.json").exists():  # a `next` that stopped half way
        return setup.stop_session(holder)
    state = read_state(root)
    name = state["function"]["name"]
    current = app_function(info, name)
    lost = claimlost.taken(claimlost.read(root))
    if current.get("status") != "matched" and not lost:
        keep_work(holder, root, info, state)
        current = app_function(info, name)
    if current.get("status") == "matched":
        setup.stop_session(holder)
        print(f"RESULT {name}: MATCHED")
        return 0
    text = summary(state, note)
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
          f"{state['tries']} tries. Released.")
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
