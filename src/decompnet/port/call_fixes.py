"""Calls whose C disagrees with the function they call, put right where the judge allows (docs/PORTING.md).

The WebAssembly build lists every call whose C disagrees with its callee (port/wasm/calls.py): matched C passes
arguments the callee doesn't declare, takes a result from a function declared void, leaves arguments out. On MIPS
that's only registers and the game's bytes come out right; off MIPS the build has to bridge each one. Mostly the
callee's own C is the loose side: a machine draft that matched as `void f(void *arg0)` while every caller (and the
header) knows it returns a value and takes two arguments. An N64 shooter had 912 such calls in 333 functions.

So the callee's definition is rewritten to agree with how its callers call it:
- a result its callers take: the return type they expect (s32 without a prototype), and its trailing call returned
  (`g(x);` → `return g(x);`, the value already in $v0 when the function ends, so the same bytes);
- arguments its callers pass that it doesn't declare: declared, unused;
- arguments it declares that no caller passes and its body never reads: dropped;
- where the callers' prototype has the same number of arguments, its types, whole.

The callers' types are read in the callee's own file: a machine draft's `M2C_UNK` is the s32 it stands for, a pointer
to a struct the callee's header doesn't define is `void *`, and a type it can't name is left as the callee had it (43
rewrites of an N64 shooter stopped at `Syntax Error` on its callers' types). When the callers' result type doesn't
match, the trailing call's own type is tried: callers of 57 input checks declared them `float` while the call they end
with returns an int in their file, so `return (float)g()` added a conversion and `s32` kept the bytes.

Each rewrite is judged as any rewrite of matched C: the game's own compiler must still build the function's exact
bytes, or it keeps its C and is listed with why. A rewrite the console's compiler can't take (its bytes move) is
tried once more behind `#if defined(PORT) || defined(PORT_WASM)`, with the C as it was in the `#else`: the console
build defines neither, so the match can't change, and a port (the 64-bit PC's PORT, the browser build's PORT_WASM)
reads the agreeing one. Nothing else of the C changes, and no caller is touched. The next
WebAssembly build reads the new definitions, and its list of disagreeing calls shrinks by what was put right.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from ..network import NetworkError

_NOT_CODE = re.compile(r'(//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', re.S)


def _blank(source: str) -> str:
    """`source` with comments and literals turned to spaces, the same length: positions stay."""
    return _NOT_CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), source)


def _split_params(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return [] if out in ([], ["void"]) else out


def _param_name(param: str) -> str | None:
    m = re.search(r"(\w+)\s*(?:\[[^\]]*\]\s*)*$", param)
    if not m or re.fullmatch(r"(?:void|char|short|int|long|float|double|signed|unsigned|[su](?:8|16|32|64)|f32|f64)",
                             m.group(1)):
        return None
    return m.group(1)


def definition(source: str, name: str):
    """(return type, [parameters], (start, open paren, close paren, body start, body end)) of `name`'s definition in
    `source`, or None."""
    clean = _blank(source)
    m = re.search(rf"(^|[;}}]\s*)([A-Za-z_][\w \t\*]*?)\b{re.escape(name)}\s*\(", clean, re.M)
    while m:
        open_p = m.end() - 1
        depth, i = 0, open_p
        while i < len(clean):
            depth += clean[i] == "("
            depth -= clean[i] == ")"
            if depth == 0:
                break
            i += 1
        after = re.match(r"\s*\{", clean[i + 1:])
        if after:
            body = i + 1 + after.end() - 1
            depth, j = 0, body
            while j < len(clean):
                depth += clean[j] == "{"
                depth -= clean[j] == "}"
                if depth == 0:
                    break
                j += 1
            ret = " ".join(m.group(2).replace("static", " ").replace("inline", " ").split())
            return ret, _split_params(source[open_p + 1:i]), (m.start(2), open_p, i, body, j)
        m = re.compile(rf"(^|[;}}]\s*)([A-Za-z_][\w \t\*]*?)\b{re.escape(name)}\s*\(", re.M).search(clean, m.end())
    return None


def prototype(text: str, name: str):
    """(return type, [parameters] or None when unprototyped) of a declaration of `name` in `text`, or None."""
    clean = _blank(text)
    m = re.search(rf"(?:^|[;{{}}])\s*((?:extern\s+)?[A-Za-z_][\w \t\*]*?)\b{re.escape(name)}\s*"
                  r"\(([^(){};]*(?:\([^(){};]*\)[^(){};]*)*)\)\s*;", clean, re.M)
    if not m:
        return None
    ret = " ".join(m.group(1).replace("extern", " ").replace("static", " ").split())
    if not ret or re.search(r"\b(?:return|else|if)\b", ret):
        return None
    params = text[m.start(2):m.end(2)]
    return ret, (None if not params.strip() else _split_params(params))


def _trailing_call(source: str, body: int, end: int) -> tuple[int, int] | None:
    """(start, end) of the function's last statement when it's a plain call `g(...);`."""
    clean = _blank(source)
    inner = clean[body + 1:end].rstrip()
    m = re.search(r"(?:^|[;{}])\s*([A-Za-z_]\w*\s*\([^;]*\)\s*;)$", inner, re.S)
    if not m or re.match(r"(?:if|while|for|switch|return|sizeof)\b", m.group(1)):
        return None
    return body + 1 + m.start(1), body + 1 + m.end(1)


def rewrite(source: str, name: str, view, *, passed: tuple[int, int] | None, takes_result: bool,
            whole: bool) -> str | None:
    """`name`'s definition in `source` agreeing with its callers: `view` (return type, [parameters] or None) is how
    they declare it, `passed` the (fewest, most) arguments they pass, `takes_result` whether one takes its result.
    `whole`: take the view's parameter types too (same count only). None when nothing changes or it can't."""
    found = definition(source, name)
    if found is None:
        return None
    ret, params, (start, open_p, close_p, body, end) = found
    new_ret, new_params, returned = ret, list(params), None
    if takes_result and ret == "void":
        call = _trailing_call(source, body, end)
        if call is None:
            return None  # its result would be whatever $v0 held: no C states that
        new_ret = view[0] if view and view[0] and view[0] != "void" else "s32"
        returned = call
    elif whole and view and view[0] and view[0] != ret and ret != "void" and view[0] != "void":
        new_ret = view[0]
    if passed is not None:
        fewest, most = passed
        if most > len(new_params):
            new_params += [f"s32 arg{i}" for i in range(len(new_params), most)]
        elif fewest < len(new_params):
            body_text = _blank(source)[body:end]
            dropped = new_params[fewest:]
            if all((n := _param_name(p)) and not re.search(rf"\b{n}\b", body_text) for p in dropped):
                new_params = new_params[:fewest]
    if whole and view and view[1] is not None and len(view[1]) == len(new_params):
        names = [(_param_name(p) or f"arg{i}") for i, p in enumerate(new_params)]
        typed = []
        for i, p in enumerate(view[1]):
            own = _param_name(p)
            typed.append(re.sub(rf"\b{own}\s*((?:\[[^\]]*\]\s*)*)$", rf"{names[i]}\1", p) if own
                         else f"{p} {names[i]}")
        new_params = typed
    if new_ret == ret and new_params == params:
        return None
    out = source
    if returned:
        out = out[:returned[0]] + "return " + out[returned[0]:]
    out = out[:open_p + 1] + (", ".join(new_params) or "void") + out[close_p:]
    if new_ret != ret:
        head = re.sub(rf"(\b(?:static\s+|inline\s+)*)(.*?)(\s*\b{re.escape(name)}\s*)$",
                      lambda m: m.group(1) + new_ret + (" " if not new_ret.endswith("*") else "") + m.group(3).lstrip(),
                      out[start:open_p], flags=re.S)
        out = out[:start] + head + out[open_p:]
    return out


def plan(found: dict) -> dict[str, dict]:
    """{callee: {"callers", "passed": [counts], "takes_result", "kinds": Counter}} from a build's calls.json."""
    out: dict[str, dict] = {}
    for records in found.values():
        for r in records:
            p = out.setdefault(r["callee"], {"callers": set(), "passed": [], "declared": None,
                                             "takes_result": False, "kinds": Counter()})
            p["callers"].add(r["caller"])
            p["kinds"][r["kind"]] += 1
            if r["kind"] in ("extra-arguments", "missing-arguments"):
                p["passed"].append(int(r["passed"]))
                p["declared"] = int(r["declared"])
            if r["kind"] == "void-result":
                p["takes_result"] = True
    return out


def _view(rows, header, callee: str, callers) -> tuple | None:
    """How the callers declare `callee`: the commonest of their own declarations, else their header's."""
    views = []
    for caller in sorted(callers):
        row = rows.get(caller)
        if row is None:
            continue
        v = prototype(row["matched_source"], callee) or prototype(header(row), callee)
        if v:
            views.append((v[0], tuple(v[1]) if v[1] is not None else None))
    if not views:
        return None
    ret, params = Counter(views).most_common(1)[0][0]
    return ret, (list(params) if params is not None else None)


_M2C = {"M2C_UNK": "s32", "M2C_UNK8": "s8", "M2C_UNK16": "s16", "M2C_UNK32": "s32"}
_BUILTIN = re.compile(r"(?:void|char|short|int|long|float|double|signed|unsigned|const|volatile|struct|union|enum|"
                      r"[su](?:8|16|32|64)|f32|f64)")


def local_type(t: str, text: str) -> str | None:
    """`t`, a type the callers wrote, as the callee's file (`text`: its header and source) can name it, or None."""
    t = " ".join(_M2C.get(w, w) for w in t.replace("*", " * ").split()).replace(" *", "*").replace("*", " *")
    t = t.replace(" * *", " **").strip()
    for word in re.findall(r"[A-Za-z_]\w*", t):
        if _BUILTIN.fullmatch(word):
            continue
        if re.search(rf"\b(?:typedef\b[^;]*\b{word}\s*;|(?:struct|union|enum)\s+{word}\s*\{{|#\s*define\s+{word}\b)",
                     text):
            continue
        return "void *" if "*" in t else None
    return t


def _local_view(view, text: str):
    """The callers' view with each type as the callee's file names it; a parameter list with a type it can't name
    is dropped (the callee's own types stay)."""
    if not view:
        return view
    ret = local_type(view[0], text) if view[0] else None
    params = None
    if view[1] is not None:
        params = []
        for p in view[1]:
            name = _param_name(p)
            typ = p[:p.rfind(name)].strip() if name else p
            local = local_type(typ, text)
            if local is None:
                params = None
                break
            params.append((local + ("" if local.endswith("*") else " ") + name) if name else local)
    return ret, params


def _trailing_type(source: str, name: str, text: str) -> str:
    """The return type of the call `name` ends with, as its own file declares it (s32 without a prototype)."""
    found = definition(source, name)
    call = found and _trailing_call(source, found[2][3], found[2][4])
    callee = call and re.match(r"[A-Za-z_]\w*", source[call[0]:call[1]])
    proto = callee and prototype(text, callee.group(0))
    return (proto and proto[0] != "void" and local_type(proto[0], text)) or "s32"


def behind_port(old: str, new: str) -> str:
    """`new` for a port's build and `old` for the console's: the console build defines neither macro."""
    return f"#if defined(PORT) || defined(PORT_WASM)\n{new.rstrip()}\n#else\n{old.rstrip()}\n#endif"


def fix(net, slug: str, log=print) -> dict:
    """Put right every callee the last WebAssembly build lists, judged: {"callees", "kept", "refused"}."""
    from .wasm import play
    with net.progress.view():
        proj = net.project_row(slug)
        if not net.is_rom(proj):
            raise NetworkError("Disagreeing calls are put right in projects decompdecomp split from a ROM, which the "
                               "WebAssembly build is made from.")
        repo = play.game(net, slug)[0]
        path = repo / "port" / "wasm" / "build" / "calls.json"
        if not path.is_file():
            raise NetworkError("Build the game for the browser first (Play in browser or an export): its build lists "
                               "the calls that disagree.")
        found = json.loads(path.read_text(encoding="utf-8"))
        store = net.project_store(proj["id"])
        rows = {r["name"]: r for r in store.all(
            "SELECT * FROM functions WHERE project_id=? AND status='matched' AND gone_mark IS NULL", proj["id"])}
        units = net._unit_contexts(proj["id"])
    contexts: dict = {}

    def header(fn) -> str:
        unit = net._effective_unit(fn, units)
        if unit not in contexts:
            with net.progress.view():
                context = net._context(proj["id"], unit)
            contexts[unit] = context["header"] if context is not None else ""
        return contexts[unit]

    every = plan(found)
    plans = {c: p for c, p in every.items() if c in rows}
    log(f"{sum(sum(p['kinds'].values()) for p in plans.values())} disagreeing calls to {len(plans)} functions with "
        "matched C: rewriting each one's definition the way its callers call it, judged by the game's own compiler")
    refused: dict[str, str] = {c: "it has no matched C to rewrite" for c in every if c not in rows}
    kept: dict[int, str] = {}
    moved: dict[int, str] = {}  # rewrites the console's compiler can't take, for the #ifdef pass
    # the callers' prototype whole first, then only what has to change, then the trailing call's own result type
    for whole, own_result in ((True, False), (False, False), (False, True)):
        candidates = {}
        for callee, p in sorted(plans.items()):
            row = rows[callee]
            if row["id"] in kept or (own_result and not p["takes_result"]):
                continue
            passed = (min(p["passed"]), max(p["passed"])) if p["passed"] else None
            text = header(row) + "\n" + row["matched_source"]
            view = _local_view(_view(rows, header, callee, p["callers"]), text)
            if own_result:
                view = (_trailing_type(row["matched_source"], callee, text), view[1] if view else None)
            new = rewrite(row["matched_source"], callee, view, passed=passed, takes_result=p["takes_result"],
                          whole=whole)
            if new is None or new in candidates.values():
                if not whole and not own_result and callee not in refused:
                    refused[callee] = "no rewrite states what its callers expect (a result from no trailing call, " \
                                      "or arguments it reads that a caller leaves out)"
                continue
            candidates[row["id"]] = new
        if not candidates:
            continue
        judged = [r for r in rows.values() if r["id"] in candidates]
        log(f"judging {len(judged)} rewritten definitions" + (" (callers' prototypes)" if whole else
                                                              " (the trailing call's result type)" if own_result else ""))
        broken = dict(net.regression_test(proj, None, sources=candidates, only=judged, header_for=header))
        for fn in judged:
            if fn["name"] in broken:
                refused[fn["name"]] = f"the console build {broken[fn['name']]}"
                moved[fn["id"]] = candidates[fn["id"]]
            else:
                kept[fn["id"]] = candidates[fn["id"]]
                refused.pop(fn["name"], None)
                moved.pop(fn["id"], None)
    behind = _behind_port(net, proj, rows, moved, header, refused, log)
    kept.update(behind)
    done = _keep(net, proj, rows, kept)
    names = {r["id"]: r["name"] for r in rows.values()}
    behind_names = sorted(names[i] for i in behind if names[i] in done)
    for name in behind_names:  # still listed until the next build reads them
        refused[name] = "rewritten behind #ifdef PORT (the console keeps its C); build again to count it"
    calls = sum(sum(plans[n]["kinds"].values()) for n in done)
    log(f"{len(done)} functions now agree with their callers ({calls} of the listed calls"
        + (f", {len(behind_names)} behind #ifdef PORT" if behind_names else "") + "); "
        f"{len(refused) - len(behind_names)} kept their C" + (": " + "; ".join(f"{n} ({w})" for n, w in sorted(refused.items()) if n not in behind_names)[:900]
                                          if len(refused) > len(behind_names) else "")
        + ". Build again (Play in browser or an export) to see the calls left.")
    from .wasm import calls as wasm_calls
    # what the next report lists beside each disagreeing function, so the ones left can be fixed another way
    (path.parent / wasm_calls.REFUSED).write_text(json.dumps(dict(sorted(refused.items())), indent=1), encoding="utf-8")
    return {"callees": len(plans), "kept": done, "calls": calls, "behind_port": behind_names,
            "refused": [{"function": n, "why": w} for n, w in sorted(refused.items()) if n not in behind_names]}


def _behind_port(net, proj, rows, moved: dict[int, str], header, refused: dict[str, str], log) -> dict[int, str]:
    """The rewrites the console's compiler couldn't take, each as `behind_port`: kept when the console build still
    matches (it builds the old C) and the PC compiler finds no more errors than it did, else its refusal stays."""
    if not moved:
        return {}
    from . import pc_compile, port_fix
    by_id = {r["id"]: r for r in rows.values()}
    wrapped = {i: behind_port(by_id[i]["matched_source"], new) for i, new in moved.items()}
    judged = [by_id[i] for i in wrapped]
    log(f"judging {len(judged)} rewrites the console can't take, behind #ifdef PORT")
    broken = dict(net.regression_test(proj, None, sources=wrapped, only=judged, header_for=header))
    cc = pc_compile.compiler(log)
    out: dict[int, str] = {}
    for fn in judged:
        if fn["name"] in broken:
            continue
        with net.progress.view():
            before = pc_compile.function(net, proj, fn["name"]) or []
        found = pc_compile.compile_one(header(fn), wrapped[fn["id"]], cc=cc)
        if port_fix._tally(found)[0] > port_fix._tally(before)[0]:
            refused[fn["name"]] += "; behind #ifdef PORT the PC compiler finds more errors"
            continue
        out[fn["id"]] = wrapped[fn["id"]]
    return out


def _keep(net, proj, rows, kept: dict[int, str]) -> list[str]:
    """Write each judged definition, if nobody changed that function meanwhile."""
    by_id = {r["id"]: r for r in rows.values()}
    done = []
    store = net.project_store(proj["id"])
    with net.progress._project_lock(proj["slug"]), store.tx() as db:
        net.progress.check_result()
        for fid, source in kept.items():
            fn = by_id[fid]
            latest = db.execute("SELECT status, matched_source FROM functions WHERE id=?", (fid,)).fetchone()
            if latest is None or latest["status"] != "matched" or latest["matched_source"] != fn["matched_source"]:
                continue
            db.execute("UPDATE functions SET matched_source=?, best_source=? WHERE id=?", (source, source, fid))
            done.append(fn["name"])
        if done:
            net._event(db, proj["id"], None, "port_fix_calls", f"{len(done)} functions agree with their callers")
    return sorted(done)
