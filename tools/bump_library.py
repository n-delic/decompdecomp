#!/usr/bin/env python3
"""Move decompdecomp's pin of the decomp-repair library to its newest release tag (the library-pin workflow's step).

Agents used to tag the library and bump the pin by hand, racing each other for tag numbers (AGENTS.md, "Agents"). The
library now releases itself (its release workflow tags each batch of merged changes), and .github/workflows/library-pin.yml
calls this to find the newest tag and rewrite the pin in pyproject.toml; the workflow then runs `uv lock`, the tests,
and opens and merges the pull request.

    bump_library.py             rewrite pyproject.toml to the newest tag when it is newer than the pin; print the version
    bump_library.py --check     only say whether a newer tag exists (exit 0 either way)

With GITHUB_OUTPUT set it also writes `version=` (empty when the pin is current) and `summary=` (what the release is,
from the tagged commit's subject) for the workflow's later steps.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = "RetroReversing/decomp-repair"
PIN = re.compile(r'(decomprepair @ https://github\.com/RetroReversing/decomp-repair/archive/refs/tags/v)(\d+\.\d+\.\d+)(\.tar\.gz)')
TAG = re.compile(r"refs/tags/v(\d+\.\d+\.\d+)$")
RELEASE_SUBJECT = re.compile(r"^Release decomprepair \S+: ")


def key(version):
    return tuple(int(part) for part in version.split("."))


def pinned(text):
    match = PIN.search(text)
    if not match:
        sys.exit("pyproject.toml has no decomprepair pin of the form .../decomp-repair/archive/refs/tags/vX.Y.Z.tar.gz")
    return match[2]


def newest(ls_remote_output):
    """The highest vX.Y.Z among `git ls-remote --tags` lines (other tags, annotated-tag `^{}` entries, are ignored)."""
    versions = {m[1] for line in ls_remote_output.splitlines() if (m := TAG.search(line.split("\t")[-1].strip()))}
    return max(versions, key=key) if versions else None


def rewrite(text, version):
    return PIN.sub(rf"\g<1>{version}\g<3>", text, count=1)


def summary(version):
    """The release's one-line description (its commit subject without the `Release decomprepair X:` prefix), or ''."""
    try:
        done = subprocess.run(["gh", "api", f"repos/{REPO}/commits/v{version}", "--jq", ".commit.message"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    subject = done.stdout.strip().splitlines()[0] if done.returncode == 0 and done.stdout.strip() else ""
    return RELEASE_SUBJECT.sub("", subject)


def output(**values):
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a") as out:
            out.writelines(f"{k}={v}\n" for k, v in values.items())


def main(argv):
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text()
    current = pinned(text)
    tags = subprocess.run(["git", "ls-remote", "--tags", f"https://github.com/{REPO}", "refs/tags/v*"],
                          capture_output=True, text=True, check=True).stdout
    latest = newest(tags)
    if latest is None or key(latest) <= key(current):
        output(version="", summary="")
        print(f"decomprepair {current} is the newest release")
        return 0
    if "--check" in argv:
        print(f"decomprepair {latest} is newer than the pin {current}")
        return 0
    pyproject.write_text(rewrite(text, latest))
    output(version=latest, summary=summary(latest).replace("\n", " "))
    print(f"pin: decomprepair {current} -> {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
