#!/usr/bin/env python3
"""Judge and search through a running decompdecomp app, as the user sees it (credited to "you").

Standard library only: it reads the app's own connection file and calls the same local API the window does.

    judge.py best SLUG FUNC OUT.c        the function's saved best C, to a file
    judge.py diff SLUG FUNC              the diff of the saved best (rows), and the app's verdict
    judge.py try SLUG FUNC CAND.c        judge a candidate; release the claim afterwards
    judge.py seed SLUG FUNC              judge the app's own untouched draft, so a search has scored C to start from
    judge.py permute SLUG FUNC MIN VAR   start the Deep Permuter (stock settings), print the job id
    judge.py job ID                      state, progress and the log's improvements for a job
    judge.py wait ID SECONDS             poll a job until it ends (or the time is up), then show it

Options before the command: --home DIR (default: the dev home ~/Decomp-RetroReversing-dev, else ~/Decomp-RetroReversing).
Use --home for a scratch app too (scratch_run.py does). Never prints the secret.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_AGENT = "decompdecomp-window/"  # window.TOKEN_AGENT: the user agent that carries the secret
HOMES = ("~/Decomp-RetroReversing-dev", "~/Decomp-RetroReversing")


def connection(home=None):
    for candidate in ([home] if home else HOMES):
        path = Path(candidate).expanduser() / ".decomp" / "server.json"
        if path.exists():
            return json.loads(path.read_text())
    sys.exit("no running decompdecomp app found (no .decomp/server.json); pass --home DIR")


class App:
    def __init__(self, home=None):
        self.c = connection(home)

    def call(self, path, body=None, timeout=600):
        port = self.c["port"]
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"User-Agent": f"{TOKEN_AGENT}{self.c['secret']}", "X-Decomp": "1", "Host": f"127.0.0.1:{port}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as error:
            return {"HTTP": error.code, "error": error.read().decode()[:1500]}

    def function(self, slug, name):
        return self.call("/api/function?" + urllib.parse.urlencode({"slug": slug, "name": name}))

    def release(self, slug, name):
        return self.call("/api/release", {"slug": slug, "name": name})

    def try_source(self, slug, name, source, attempts=8):
        """Judge a candidate. "you hold another claim: f" is another of your attempts (or an app job) holding a claim
        under the same identity: release that one and ask again."""
        for _ in range(attempts):
            result = self.call("/api/try", {"slug": slug, "name": name, "source": source}, timeout=900)
            text = json.dumps(result)
            if "you hold another claim: " not in text:
                break
            other = text.split("you hold another claim: ", 1)[1].split(".", 1)[0].split()[0].strip('",')
            self.release(slug, other)
        self.release(slug, name)
        return result

    def job(self, job_id):
        return self.call("/api/job?id=" + urllib.parse.quote(str(job_id)))


def improvements(job):
    log = job.get("log") or []
    lines = log if isinstance(log, list) else str(log).split("', '")
    keep = ("Improved with", "Matched", "exact;", "Host verified")
    return [str(line)[:220] for line in lines if any(k in str(line) for k in keep)]


def show_job(app, job_id):
    job = app.job(job_id)
    progress = job.get("progress") or {}
    print(job.get("state"), progress.get("phase"), "best", progress.get("best_score"), "tested", progress.get("tested"),
          "family", progress.get("current_family"), "elapsed", progress.get("elapsed"))
    print("\n".join(improvements(job)[-8:]))
    if job.get("state") != "running":
        print(str(job.get("result"))[:600], job.get("error") or "")
    return job


def main(argv):
    home = None
    if argv[:1] == ["--home"]:
        home, argv = argv[1], argv[2:]
    if not argv:
        sys.exit(__doc__)
    app, (command, *args) = App(home), argv
    if command == "best" and len(args) == 3:
        slug, name, out = args
        source = app.function(slug, name).get("source") or ""
        Path(out).write_text(source)
        print(f"{len(source)} bytes of the saved C for {name} -> {out}")
    elif command == "diff" and len(args) == 2:
        slug, name = args
        function = app.function(slug, name)
        print("status", function.get("status"), "best", function.get("best"))
        diff = app.call("/api/diff?" + urllib.parse.urlencode({"slug": slug, "name": name, "which": "best"}))
        for row in diff.get("rows", []):
            print(" | ".join(str(part) for part in row))
    elif command == "try" and len(args) == 3:
        slug, name, path = args
        result = app.try_source(slug, name, Path(path).read_text())
        print(result.get("text") or result)
        print("score", result.get("score"), "matched", result.get("matched"), "exact", result.get("exact"))
    elif command == "seed" and len(args) == 2:
        # The Deep Permuter refuses a function with no scored C ("Choose an unmatched function with saved, scored
        # C first"). In normal use the autopilot's draft is that C; here the app's own draft is judged, unedited.
        slug, name = args
        function = app.function(slug, name)
        if function.get("best") is not None:
            print(f"{name} already has a scored best ({function.get('best')}); nothing to seed")
            return
        result = app.try_source(slug, name, function.get("source") or "")
        print("baseline score", result.get("score"), "- the draft is now the saved best" if result.get("score") else "")
    elif command == "permute" and len(args) == 4:
        slug, name, minutes, variants = args
        result = app.call("/api/deep-permuter", {"slug": slug, "function": name, "minutes": int(minutes),
                                                   "max_variants": int(variants), "compiler_profiles": 1})
        print(result.get("job") or result)
    elif command == "job" and len(args) == 1:
        job = show_job(app, args[0])
        sys.exit(0 if job.get("state") in ("running", "done") else 1)
    elif command == "wait" and len(args) == 2:
        deadline = time.time() + float(args[1])
        while time.time() < deadline:
            if app.job(args[0]).get("state") != "running":
                break
            time.sleep(10)
        show_job(app, args[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
