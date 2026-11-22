#!/usr/bin/env python3
"""Compiler-matching evidence, one file per entry: docs/learnings/<kind>/<name>.md.

docs/COMPILER_MATCHING_LEARNINGS.md grew to one 1.3 MB file that every matching agent appended to, so every pull request
that recorded evidence conflicted with the others. A new entry is now its own file, so two agents never touch the same
lines, and nothing generated is committed (a committed index would conflict again).

    learnings.py new KIND NAME [--title T] [--compiler C] [--evidence LEVEL]   write a skeleton, print its path
    learnings.py index [QUERY ...]                                               one line per entry, filtered
    learnings.py check                                                           every entry is well formed (CI test)
    learnings.py render                                                          all entries as one markdown document

KIND is `function` (one function's match: the compiler and flags, the source change, the measured result), `finding`
(a reusable compiler behaviour, with its rule) or `experiment` (a failed or negative result worth not repeating).
NAME is lowercase words and digits joined by `-`. An entry starts with a front matter block (title, kind, compiler,
evidence, date) and then free text; docs/learnings/README.md says what the text holds. The older entries stay in
COMPILER_MATCHING_LEARNINGS.md, whose sections other files cite by title.
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

KINDS = ("function", "finding", "experiment")
EVIDENCE = ("verified", "controlled", "observation", "hypothesis")  # the levels COMPILER_MATCHING_LEARNINGS.md defines
FIELDS = ("title", "kind", "compiler", "evidence", "date")
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 80
TEMPLATE = """---
title: {title}
kind: {kind}
compiler: {compiler}
evidence: {evidence}
date: {date}
---

What the function (or case) looked like and the exact compiler and flags:

The source change, and the measured result before and after (scores, the judge used):

Failed experiments that would otherwise be repeated:

What is controlled evidence and what is one function:
"""


def root_dir(explicit=None):
    if explicit:
        return Path(explicit)
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parent).stdout.strip()
    return Path(top or Path(__file__).resolve().parents[1])


def entries(root):
    folder = Path(root) / "docs" / "learnings"
    return sorted(p for kind in KINDS for p in (folder / kind).glob("*.md"))


def parse(path):
    """(front matter dict, body) of one entry, or raises ValueError saying what is wrong."""
    text = Path(path).read_text()
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not match:
        raise ValueError("starts with no front matter block (--- ... ---)")
    meta = {}
    for line in match[1].splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"front matter line {line!r} is not `key: value`")
        meta[key.strip()] = value.strip()
    return meta, match[2]


def problems(root):
    found = []
    seen = {}
    for path in entries(root):
        where = path.relative_to(root)
        try:
            meta, body = parse(path)
        except ValueError as error:
            found.append(f"{where}: {error}")
            continue
        for field in FIELDS:
            if not meta.get(field):
                found.append(f"{where}: front matter has no `{field}`")
        if meta.get("kind") and meta["kind"] != path.parent.name:
            found.append(f"{where}: kind {meta['kind']!r} but the file is in {path.parent.name}/")
        if meta.get("evidence") and meta["evidence"] not in EVIDENCE:
            found.append(f"{where}: evidence {meta['evidence']!r} is not one of {', '.join(EVIDENCE)}")
        if not NAME.match(path.stem) or len(path.stem) > MAX_NAME:
            found.append(f"{where}: name must be lowercase words joined by '-' (up to {MAX_NAME} characters)")
        if meta.get("date") and not re.match(r"^\d{4}-\d{2}-\d{2}$", meta["date"]):
            found.append(f"{where}: date {meta['date']!r} is not YYYY-MM-DD")
        if not body.strip():
            found.append(f"{where}: no text after the front matter")
        if path.stem in seen:
            found.append(f"{where}: the name is also used by {seen[path.stem]} (names are unique across kinds)")
        seen[path.stem] = where
        if re.search(r"(?m)^(<<<<<<<|>>>>>>>)", body):
            found.append(f"{where}: has merge conflict markers")
    return found


def new(root, kind, name, title=None, compiler="", evidence="observation", today=None):
    if kind not in KINDS:
        sys.exit(f"kind must be one of {', '.join(KINDS)}")
    if not NAME.match(name) or len(name) > MAX_NAME:
        sys.exit("name: lowercase words and digits joined by '-', e.g. one-shot-scope-before-loop")
    if evidence not in EVIDENCE:
        sys.exit(f"evidence must be one of {', '.join(EVIDENCE)}")
    taken = [p for p in entries(root) if p.stem == name]
    if taken:
        sys.exit(f"{taken[0].relative_to(root)} already uses that name: pick another, or extend that entry")
    path = Path(root) / "docs" / "learnings" / kind / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(title=title or name.replace("-", " "), kind=kind, compiler=compiler or "?",
                                    evidence=evidence, date=today or datetime.date.today().isoformat()))
    return path


def index_lines(root, words=()):
    lines = []
    for path in entries(root):
        try:
            meta, body = parse(path)
        except ValueError:
            continue
        line = f"{path.relative_to(root)}  [{meta.get('evidence', '?')}] {meta.get('compiler', '?')}: {meta.get('title', '')}"
        if all(w.lower() in (line + " " + body).lower() for w in words):
            lines.append(line)
    return lines


def render(root):
    out = ["# Compiler matching evidence (per-entry files)", ""]
    for kind in KINDS:
        files = [p for p in entries(root) if p.parent.name == kind]
        if not files:
            continue
        out += [f"## {kind}", ""]
        for path in files:
            meta, body = parse(path)
            out += [f"### {meta['title']}", f"*{meta['compiler']}; {meta['evidence']}; {meta['date']}; `{path.stem}`*", "",
                    body.strip(), ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="command", required=True)
    n = sub.add_parser("new")
    n.add_argument("kind")
    n.add_argument("name")
    n.add_argument("--title")
    n.add_argument("--compiler", default="")
    n.add_argument("--evidence", default="observation")
    sub.add_parser("index").add_argument("query", nargs="*")
    sub.add_parser("check")
    sub.add_parser("render")
    a = ap.parse_args(argv)
    root = root_dir(a.root)
    if a.command == "new":
        print(new(root, a.kind, a.name, a.title, a.compiler, a.evidence))
    elif a.command == "index":
        print("\n".join(index_lines(root, a.query)))
    elif a.command == "render":
        print(render(root))
    else:
        found = problems(root)
        print("\n".join(found) if found else f"{len(entries(root))} entries, all well formed")
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
