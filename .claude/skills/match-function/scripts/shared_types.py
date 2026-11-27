#!/usr/bin/env python3
"""The project's shared header: list the types it declares, and add a real game type to it so later agents and m2c drafts start with it.

When you work out a real type for a game structure (a display-list record, a one-member record around a global, Vec3,
a record layout used by several functions), put it in the shared header instead of in your own candidate: every later
claim's compile and every `redraft` / machine draft then has it. A type only this function uses stays in the candidate.

    shared_types.py list SLUG [--function F] [--full] [--file PATH]
    shared_types.py add  SLUG TYPE.h [--function F] [-m MESSAGE] [--apply] [--file PATH] [--holder H]

`list` prints the types the header declares (typedefs, struct/union/enum tags and their first line), `--full` the whole
header with its version. `add` reads TYPE.h (only typedef / struct / union / enum definitions, no externs or functions),
checks it against the header and prints what it would append. It refuses, with the line it clashes with, a type the
header already declares (identical: nothing to add; different: a conflict, so rename yours or edit the header's by
hand), a name the header already uses for a macro, variable or function, and a member of a type the header
doesn't define. Without `--apply` that is all (a proposal); `--apply` appends it with the app's `edit_context`
(`--file PATH`: `edit_file` on that header, for projects of public decomps, which have no context.h).
The app itself then refuses the edit if any already-matched function stops matching with the new header (its regression
compiles run before the header changes), and says which; keep the declaration in your candidate then.

Read through, and a write sent over, the MCP connection of a `setup.py SLUG` session (the
only one for SLUG; with several sessions open pass `--holder NAME`, your own session's): a write, so `--apply` is the only thing here that changes the project. Never writes without it.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup  # noqa: E402  (the session's connection: setup.session_gateway, setup.McpError)

KEYWORDS = {"const", "volatile", "unsigned", "signed", "short", "long", "int", "char", "float", "double", "void", "static",
            "register", "extern", "_Bool", "struct", "union", "enum", "typedef", "inline"}
TAGGED = ("struct", "union", "enum")
IDENT = re.compile(r"[A-Za-z_]\w*")


class TypeError_(Exception):
    """The type text can't be added, said so the agent can fix it."""


def strip_comments(text):
    """The text without C comments (a `//` inside a string literal is not expected in a header's type declarations)."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def squash(text):
    """A whitespace-insensitive form, for telling an identical declaration from a different one."""
    return re.sub(r"\s*([{};,*\[\]()=])\s*", r"\1", " ".join(text.split()))


def statements(text):
    """(kind, text) for each top-level item of comment-free `text`: kind `pp` (a preprocessor line) or `decl`."""
    out, buffer, depth, lines = [], [], 0, text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if depth == 0 and not "".join(buffer).strip() and line.lstrip().startswith("#"):
            joined = line
            while joined.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                joined = joined.rstrip()[:-1] + " " + lines[i]
            out.append(("pp", joined.strip()))
            i += 1
            continue
        for char in line + "\n":
            buffer.append(char)
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
                if depth == 0 and not re.match(r"\s*(typedef|struct|union|enum)\b", "".join(buffer)):
                    out.append(("decl", "".join(buffer).strip()))  # a function body ends at its brace
                    buffer = []
            elif char == ";" and depth == 0:
                out.append(("decl", "".join(buffer).strip()))
                buffer = []
        i += 1
    if "".join(buffer).strip():
        out.append(("decl", "".join(buffer).strip()))
    return out


def split_braces(statement):
    """(head, body, tail) of a statement with a top-level `{ body }`, else (statement, None, "")."""
    start = statement.find("{")
    if start < 0:
        return statement, None, ""
    depth = 0
    for i in range(start, len(statement)):
        depth += statement[i] == "{"
        depth -= statement[i] == "}"
        if depth == 0:
            return statement[:start], statement[start + 1:i], statement[i + 1:]
    return statement, None, ""


def declarator_names(tail):
    """The names a declarator list declares: `Name, *Ptr, Arr[4]` -> Name, Ptr, Arr; `(*Fn)(int)` -> Fn."""
    names = []
    for part in re.split(r",(?![^(]*\))", tail.rstrip("; \n")):
        found = re.search(r"\(\s*\*\s*(\w+)\s*\)", part) or re.match(r"[\s*]*(\w+)", part)
        if found:
            names.append(found[1])
    return names


def declarations(text):
    """What a header (or a proposal) declares at top level: a list of dicts
    {key, name, kind, norm, body, text}: key `typedef:Name`, `tag:struct Tag`, `macro:NAME` or `name:ident`
    (a variable, function or other declaration, which only matters as a name a type must not reuse); `body` is None for a
    forward declaration (`struct Tag;`, `typedef struct Tag Name;`)."""
    found = []
    for kind, statement in statements(strip_comments(text)):
        if kind == "pp":
            macro = re.match(r"#\s*define\s+(\w+)", statement)
            if macro:
                found.append({"key": f"macro:{macro[1]}", "name": macro[1], "kind": "macro", "norm": squash(statement),
                              "body": statement, "text": statement})
            continue
        head, body, tail = split_braces(statement)
        head = head.rstrip().rstrip(";")
        words = head.split()
        norm = squash(statement)
        first = words[0] if words else ""
        if first == "typedef":
            agg = next((w for w in words[1:] if w in TAGGED), None)
            tag = re.search(r"\b(?:struct|union|enum)\s+(\w+)", head)
            if body is not None:
                if tag:
                    found.append({"key": f"tag:{agg} {tag[1]}", "name": tag[1], "kind": "tag", "agg": agg, "norm": norm,
                                  "body": body, "text": statement})
                for name in declarator_names(tail):
                    found.append({"key": f"typedef:{name}", "name": name, "kind": "typedef", "agg": agg, "norm": norm,
                                  "body": body, "text": statement})
            else:
                flat = re.sub(r"\[[^\]]*\]", "", head)
                func = re.search(r"\(\s*\*\s*(\w+)\s*\)", flat)
                names = [func[1]] if func else re.findall(r"\w+", flat)[-1:]
                alias_of_tag = bool(re.fullmatch(r"typedef\s+(struct|union|enum)\s+\w+\s+\w+\s*", head))
                for name in names:
                    found.append({"key": f"typedef:{name}", "name": name, "kind": "typedef", "agg": None, "norm": norm,
                                  "body": None if alias_of_tag else norm, "text": statement})
        elif first in TAGGED and len(words) >= 2 and IDENT.fullmatch(words[1]) and not declarator_names(tail):
            found.append({"key": f"tag:{first} {words[1]}", "name": words[1], "kind": "tag", "agg": first, "norm": norm,
                          "body": body, "text": statement})  # a body of None: struct Tag;
        else:
            for name in other_names(statement):
                found.append({"key": f"name:{name}", "name": name, "kind": "name", "agg": None, "norm": norm,
                              "body": norm, "text": statement})
    return found


def other_names(statement):
    """The names a non-type declaration declares (a variable, array, function or prototype), roughly."""
    text = split_braces(statement)[0] if split_braces(statement)[1] is not None else statement
    text = text.rstrip("; \n")
    before = text.split("=")[0]
    if "(" in before:  # a function or prototype (or a function-pointer variable): the name before the first parenthesis
        func = re.match(r"[^(]*\(\s*\*\s*(\w+)", before)
        name = func[1] if func else (re.findall(r"\w+", before.split("(")[0]) or [""])[-1]
        return [name] if name and name not in KEYWORDS else []
    names = []
    for part in re.split(r",", before if "," not in text else text):
        words = re.findall(r"\w+", re.sub(r"\[[^\]]*\]|=.*", "", part))
        if words and words[-1] not in KEYWORDS:
            names.append(words[-1])
    return names


def member_types(body):
    """The type names a struct/union body's members use (identifiers other than keywords, tags and the declarators)."""
    flat = re.sub(r"\b(?:struct|union)\s*\w*\s*\{", ";", body).replace("}", ";")
    used = set()
    for member in flat.split(";"):
        member = re.sub(r"\[[^\]]*\]|:\s*\d+", "", member)
        if "(" in member:  # a function pointer member: not checked
            continue
        first = member.split(",")[0]
        first = re.sub(r"\b(struct|union|enum)\s+\w+", " ", first)
        names = [n for n in IDENT.findall(first) if n not in KEYWORDS]
        if len(names) >= 2:  # the last is the declarator, the others are types
            used.update(names[:-1])
    return used


def check(header, proposal):
    """([(declaration, state)], [problem sentences], [names the proposal would add]) for adding `proposal` to `header`.
    state is "new", "duplicate" or "conflict"; problems is empty when the proposal is fit to append."""
    existing = declarations(header)
    new = declarations(proposal)
    problems = []
    if not new:
        raise TypeError_("TYPE.h declares nothing: give it a typedef, struct, union or enum definition")
    leftovers = [d for d in new if d["kind"] in ("macro", "name")]
    if leftovers:
        raise TypeError_("TYPE.h may hold type definitions only; " + ", ".join(d["name"] for d in leftovers)
                         + " is a macro or a variable/function: add those with edit_context yourself")
    by_key, results = {}, []
    for d in existing:
        by_key.setdefault(d["key"], []).append(d)
    for d in new:
        same = by_key.get(d["key"], [])
        if any(e["norm"] == d["norm"] for e in same):
            state = "duplicate"
            problems.append(f"{d['key'].split(':', 1)[1]} is already declared in the header, identically: nothing to add")
        elif any(e["body"] is not None for e in same):
            state = "conflict"
            problems.append(f"{d['key'].split(':', 1)[1]} is already declared differently: {same[0]['text'][:160]!r}; "
                            "rename yours or change the header's by hand (edit_context)")
        else:
            state = "new"
        clash = [e for e in by_key.get(f"macro:{d['name']}", []) + by_key.get(f"name:{d['name']}", [])]
        if d["kind"] == "typedef" and clash:
            state = "conflict"
            problems.append(f"{d['name']} is already used in the header as a {clash[0]['kind']}: {clash[0]['text'][:120]!r}")
        results.append((d, state))
    known = {e["name"] for e in existing if e["kind"] == "typedef"}
    for d in new:  # in order: a type must be defined before a later one uses it
        if d["body"] and d.get("agg") in ("struct", "union"):
            unknown = sorted(member_types(d["body"]) - known)
            if unknown:
                problems.append(f"{d['name']} has members of type {', '.join(unknown)}, which the header doesn't define "
                                "before this point (a typo, or define that type first)")
        if d["kind"] == "typedef":
            known.add(d["name"])
    names = list(dict.fromkeys(d["name"] for d, _ in results))
    return results, problems, names


def header_of(gateway, function="", file=""):
    """(the header text, its description) from the app: `get_context` (ROM projects), or the file by `read_file`."""
    if file:
        lines, start, total = [], 1, None
        while total is None or start <= total:
            text = gateway.call("read_file", {"path": file, "start": start, "count": 600})
            head, _, rest = text.partition("\n")
            found = re.search(r"of (\d+)\)", head)
            total = int(found[1]) if found else 0
            chunk = [re.sub(r"^\s*\d+  ?", "", line, count=1) for line in rest.split("\n")] if rest else []
            lines += chunk
            start += 600
            if not chunk:
                break
        return "\n".join(lines), file
    try:
        text = gateway.call("get_context", {"function": function})
    except setup.McpError as error:
        if "is for ROM projects" in str(error):
            raise SystemExit("this project's header is its own files (a project of a public decomp): pass --file PATH, "
                             "the header's path (search_code shows where types live)") from None
        raise
    head, _, body = text.partition("\n")
    return body, head.lstrip("/ ").strip()


def append_text(proposal):
    return proposal.strip("\n") + "\n"


def run_list(gateway, slug, function, file, full, write=print):
    header, what = header_of(gateway, function, file)
    write(f"{slug}: {what}" if file else f"{what}")
    if full:
        write(header)
        return 0
    rows = [d for d in declarations(header) if d["kind"] in ("typedef", "tag")]
    if not rows:
        write("no types declared")
    for d in rows:
        write(f"  {d['key']:<28} {' '.join(d['text'].split())[:90]}")
    return 0


def run_add(gateway, slug, proposal, function, file, message, apply, write=print):
    header, what = header_of(gateway, function, file)
    try:
        results, problems, names = check(header, proposal)
    except TypeError_ as error:
        write(f"not added: {error}")
        return 1
    write(f"Proposed for {slug}'s shared header ({what}): " + ", ".join(names))
    for d, state in results:
        write(f"  {state:<9} {d['key']}")
    if problems:
        write("Refused:")
        for problem in problems:
            write(f"  - {problem}")
        return 1
    write("\n" + append_text(proposal).rstrip())
    if not apply:
        write("\nA proposal only. Add --apply to append it (the app re-compiles every matched function with the new header "
              "first and refuses the edit if any would stop matching).")
        return 0
    message = message or "add the shared type" + ("s " if len(names) > 1 else " ") + ", ".join(names)
    if file:
        reply = gateway.call("edit_file", {"path": file, "old": "", "new": "\n" + append_text(proposal), "message": message})
    else:
        reply = gateway.call("edit_context", {"old": "", "new": append_text(proposal), "message": message, "function": function})
    write("\n" + reply.strip())
    return 1 if "REJECTED" in reply or reply.lstrip().startswith("❌") else 0


def main(argv=None, gateway=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("list", "add"):
        p = sub.add_parser(name)
        p.add_argument("slug")
        if name == "add":
            p.add_argument("types", help="a file holding the typedef/struct/union/enum definitions to add")
            p.add_argument("-m", "--message", default="", help="the commit message for the change (default: names the types)")
            p.add_argument("--apply", action="store_true", help="append it (default: only check and show the proposal)")
        else:
            p.add_argument("--full", action="store_true", help="print the whole header")
        p.add_argument("--function", default="", help="the function whose header it is (needed when each source file has its own)")
        p.add_argument("--file", default="", help="a project of a public decomp: the header file's path in the project")
        p.add_argument("--holder", default="",
                       help="your setup.py session's holder name; needed when several sessions of SLUG are open")
    a = ap.parse_args(argv)
    if gateway is None:
        gateway, _ = setup.session_gateway(a.slug, a.holder)
    try:
        if a.command == "list":
            return run_list(gateway, a.slug, a.function, a.file, a.full)
        try:
            proposal = Path(a.types).read_text()
        except OSError as error:
            sys.exit(f"can't read {a.types}: {error}")
        return run_add(gateway, a.slug, proposal, a.function, a.file, a.message, a.apply)
    except setup.McpError as error:
        sys.exit(f"the app refused: {error}")


if __name__ == "__main__":
    sys.exit(main())
