#!/usr/bin/env python3
"""Compiler-matching evidence, one file per entry: docs/learnings/<kind>/<name>.md.

docs/COMPILER_MATCHING_LEARNINGS.md grew to one 1.3 MB file that every matching agent appended to, so every pull request
that recorded evidence conflicted with the others. A new entry is now its own file, so two agents never touch the same
lines, and nothing generated is committed (a committed index would conflict again).

    learnings.py new KIND NAME [--title T] [--compiler C] [--evidence LEVEL]   write a skeleton, print its path
    learnings.py index [QUERY ...]                                               one line per entry, filtered
    learnings.py check                                                           every entry is well formed (CI test)
    learnings.py render                                                          all entries as one markdown document
    learnings.py search "register swap" [--top N] [--per-file] [--library DIR]   rank entries by a symptom

KIND is `function` (one function's match: the compiler and flags, the source change, the measured result), `finding`
(a reusable compiler behaviour, with its rule) or `experiment` (a failed or negative result worth not repeating).
NAME is lowercase words and digits joined by `-`. An entry starts with a front matter block (title, kind, compiler,
evidence, date) and then free text; docs/learnings/README.md says what the text holds. The older entries stay in
COMPILER_MATCHING_LEARNINGS.md, whose sections other files cite by title.
"""
import argparse
import datetime
import importlib.util
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


# ---------------------------------------------------------------- search by symptom
# A symptom is a few words from what the diff shows ("register swap", "store too early", "frame size"). Entries say the
# same thing in other words, so each known phrase or word also looks for its synonyms (a plain substring, lowercase).
SYNONYMS = {
    "register swap": ["swapped register", "registers swapped", "register exchange", "exchanged", "swap of saved",
                      "allocation priorit", "priority", "$s1/$s2", "live range"],
    "swap": ["swapped", "exchanged", "exchange"],
    "register": ["registers", "allocat", "saved register", "greg", "lreg"],
    "delay slot": ["delay-slot", "branch delay", "delay slots", "nop", "filled", "reorg"],
    "store too early": ["early store", "store early", "stores early", "store before", "scheduler", "sched1", "sched2",
                        "schedule", "hoists the store", "moved before"],
    "too early": ["early", "before the", "hoist", "scheduler"],
    "frame size": ["stack frame", "frame layout", "addiu $sp", "frame", "spill", "stack slot", "locals take"],
    "hoisted constant": ["hoist", "hoisted", "loop-invariant", "invariant", "constant", "literal", "lui"],
    "load order": ["order of the loads", "loads reordered", "load before", "loads"],
    "branch": ["jump", "beq", "bne", "goto", "inverted", "inversion"],
    "inline": ["inlined", "helper", "macro"],
}
STOP = {"too", "very", "a", "an", "the", "of", "in", "on", "is", "it", "to", "and", "or", "with", "for", "at", "by", "my", "this", "that"}
FIX_VERBS = re.compile(r"\b(replac|mov(?:e|ed|ing)|split|rewr|chang|spell|hoist|sink|swap|reorder|declar|add(?:ed|ing)?|"
                       r"remov|insert|wrap|merge|copy|cast|scored|scores?|made|make|use[ds]?|using|write|written)", re.I)
TITLE_WEIGHT = 4
BODY_CAP = 4  # a word repeated forty times in one long entry is not forty times as relevant
EXCERPT = 420


def groups_for(query):
    """The query's search groups: each non-trivial word (with its plural-less stem) and each known phrase, every one
    with the synonyms it brings. A group matches an entry when any of its terms occurs; more groups matched ranks higher."""
    text = " ".join(query.lower().split())
    words = [w for w in re.findall(r"[a-z0-9$_]+", text) if w not in STOP]
    groups = []
    for phrase, extra in SYNONYMS.items():
        if " " in phrase and phrase in text:
            groups.append({"label": phrase, "terms": [phrase, *extra], "weight": 2})
    for word in dict.fromkeys(words):
        stem = word[:-1] if word.endswith("s") and len(word) > 3 else word
        groups.append({"label": word, "terms": [word, stem, *SYNONYMS.get(word, [])], "weight": 1})
    return groups


def count_term(term, text):
    """Occurrences of `term` in lowercase `text`: a whole word for plain words, a substring for anything else."""
    if re.fullmatch(r"[a-z0-9_]+", term):
        return len(re.findall(rf"\b{re.escape(term)}", text))
    return text.count(term)


def legacy_sections(root):
    """(title, body) of each `### ` section of the older COMPILER_MATCHING_LEARNINGS.md."""
    path = Path(root) / "docs" / "COMPILER_MATCHING_LEARNINGS.md"
    if not path.exists():
        return []
    found, title, lines = [], None, []
    for line in path.read_text().splitlines():
        if line.startswith("### ") or line.startswith("## "):
            if title is not None:
                found.append((title, "\n".join(lines).strip()))
            title, lines = (line[4:].strip() if line.startswith("### ") else None), []
        elif title is not None:
            lines.append(line)
    if title is not None:
        found.append((title, "\n".join(lines).strip()))
    return found


def known_families(root, library=None):
    """The decomp-repair library's family names (the skill's families.py reads the installed copy), or None when it
    isn't installed in this python."""
    script = Path(root) / ".claude" / "skills" / "match-function" / "scripts" / "families.py"
    if not script.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("learnings_families", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {row[0] for row in module.catalogue(module.library_dir(library))}
    except (SystemExit, Exception):  # no library, or one this script can't read: fall back to what the text says
        return None


def families_in(text, known):
    """The family names an entry mentions: the library's own names when known, else backticked snake_case words."""
    if known:
        return sorted(name for name in known if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text))
    return sorted(set(re.findall(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`", text)))


def fix_excerpt(body, groups):
    """The paragraph that most likely says what changed: the one with the most symptom words, and fix verbs."""
    best, best_score = "", -1
    for paragraph in re.split(r"\n\s*\n", body):
        flat = " ".join(paragraph.split())
        if not flat or flat.startswith("```") or flat.startswith("|"):
            continue
        low = flat.lower()
        score = sum(2 for g in groups if any(count_term(t, low) for t in g["terms"])) + (3 if FIX_VERBS.search(flat) else 0)
        if score > best_score:
            best, best_score = flat, score
    if len(best) > EXCERPT:
        cut = best.rfind(". ", 0, EXCERPT)
        best = best[:cut + 1] if cut > EXCERPT // 2 else best[:EXCERPT].rstrip() + "..."
    return best


def search(root, query, top=5, known=None):
    """Ranked hits [{score, groups, where, title, fix, families, note}] over the per-entry files and the older document."""
    groups = groups_for(query)
    if not groups:
        return []
    pool = []
    for path in entries(root):
        try:
            meta, body = parse(path)
        except ValueError:
            continue
        pool.append((str(path.relative_to(root)), meta.get("title", path.stem), body,
                     f"[{meta.get('evidence', '?')}] {meta.get('compiler', '?')}"))
    pool += [(f"docs/COMPILER_MATCHING_LEARNINGS.md  ### {title}", title, body, "[older entry]")
             for title, body in legacy_sections(root)]
    hits = []
    for where, title, body, note in pool:
        low_title, low_body = title.lower(), body.lower()
        matched, score = [], 0.0
        for group in groups:
            in_title = sum(count_term(t, low_title) for t in group["terms"])
            in_body = sum(count_term(t, low_body) for t in group["terms"])
            if in_title or in_body:
                matched.append(group["label"])
                score += group["weight"] * (TITLE_WEIGHT * min(in_title, 2) + min(in_body, BODY_CAP))
        if matched:
            hits.append({"score": score, "groups": len(matched), "matched": matched, "where": where, "title": title,
                         "note": note})
    hits.sort(key=lambda h: (-h["groups"], -h["score"], h["where"]))
    bodies = {where: body for where, _, body, _ in pool}
    out = []
    for hit in hits[:max(1, top)]:
        body = bodies[hit["where"]]
        hit["fix"] = fix_excerpt(body, groups)
        hit["families"] = families_in(body, known)
        out.append(hit)
    return out


def render_search(hits):
    if not hits:
        return "no entry matches; try fewer or different words (e.g. the rows' register, `sched`, `frame`, `hoist`)"
    lines = []
    for number, hit in enumerate(hits, 1):
        lines += [f"{number}. {hit['title']}   {hit['note']}", f"   {hit['where']}",
                  f"   matched: {', '.join(hit['matched'])}  (score {hit['score']:g})",
                  f"   fix: {hit['fix']}"]
        if hit["families"]:
            lines.append(f"   families: {', '.join(hit['families'])}")
        lines.append("")
    return "\n".join(lines).rstrip()


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
    q = sub.add_parser("search")
    q.add_argument("query", nargs="+", help="the symptom, in a few words")
    q.add_argument("--top", type=int, default=5)
    q.add_argument("--library", help="the decomp-repair package folder, to know which names are families")
    sub.add_parser("check")
    sub.add_parser("render")
    a = ap.parse_args(argv)
    root = root_dir(a.root)
    if a.command == "new":
        print(new(root, a.kind, a.name, a.title, a.compiler, a.evidence))
    elif a.command == "index":
        print("\n".join(index_lines(root, a.query)))
    elif a.command == "search":
        print(render_search(search(root, " ".join(a.query), a.top, known_families(root, a.library))))
    elif a.command == "render":
        print(render(root))
    else:
        found = problems(root)
        print("\n".join(found) if found else f"{len(entries(root))} entries, all well formed")
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
