"""The API helper: decompdecomp's own agent loop for any OpenAI-compatible API (DeepSeek, OpenRouter,
OpenAI, a local llama.cpp, Ollama or LM Studio). No program to install: the app sends the conversation to the model's
chat-completions endpoint and calls the tools the model asks for directly (app/tools.py `call`), with the same tools
the CLI agents get over MCP.

Its model, address and key come from an API endpoint the user saved in Agents & models (`endpoint_entry`; the
settings keep them, app/support.py), never from its record. The conversation stays small: once the helper is done
with a function, that function's messages fold into one line (`fold`), and what still outgrows the model's context is
trimmed (`fit`).
"""

from __future__ import annotations

import json
import re
import time

import httpx

from . import Agent, args_brief, number

API_PRESETS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": True},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat", "key": True},
    "ollama": {"name": "Ollama (local)", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3-coder", "key": False},
    "lmstudio": {"name": "LM Studio (local)", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "key": False},
    "llamacpp": {"name": "llama.cpp (local)", "base_url": "http://127.0.0.1:8080/v1", "model": "local", "key": False},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-5", "key": True},
    "custom": {"name": "Custom", "base_url": "", "model": "", "key": True},
}

# Tool calls per session, the share of the model's context the conversation may fill (the rest is the reply's), how
# often a request the server finds too long is trimmed harder (by API_TRIM_FACTOR) and sent again.
API_STEPS = 120
API_CONTEXT_SHARE = 0.7
API_TRIM_RETRIES = 4
API_TRIM_FACTOR = 0.6
API_TIMEOUT, API_CONNECT_TIMEOUT = 300, 15
PROPS_TIMEOUT = 5  # asking a llama.cpp server its context
MIN_CONTEXT = 2048
LOCAL_CONTEXT = 16384  # what a local server runs when it doesn't say (they often run small contexts)
REMOTE_CONTEXT = 128000
CHARS_PER_TOKEN = 3  # code and assembly run about 3 characters a token
TOKENS_PER_MESSAGE = 4  # what a message costs beyond its text
TRIM_ABOVE, TRIM_TO = 300, 240  # characters: an old message longer than this is cut to this
REASONING_SHOWN = 2000  # characters of a model's reasoning the feed shows
ERROR_SHOWN = 300  # characters of an API error the helper's record keeps
PAYMENT_REQUIRED = 402
TOO_MANY_REQUESTS = 429
TOO_LONG_STATUSES = (400, 413, 422, 500)  # what servers answer a request too long for the model's context
TOO_LONG_READ = 2000  # characters of that answer read for why
# a function is done with when the helper compiled it to a match or released it: its work then folds into a line
FINISHING = ("compile_and_diff", "release_function")
FOLDED = 2  # messages a fold leaves: one call and its result


class Api(Agent):
    id = "api"
    name = "API key"
    vendor = "DeepSeek, OpenRouter, Ollama, any OpenAI-compatible API"
    docs = "https://api-docs.deepseek.com"
    recommended = True  # decompdecomp runs its whole loop, and no other program's update can break it
    uses_endpoints = True
    takes_effort = True  # OpenAI's reasoning_effort, which OpenAI-compatible servers with reasoning models take
    efforts = ("minimal", "low", "medium", "high")

    def label(self, name: str, options: dict, used: set[str]) -> str:
        """By its endpoint, numbered: "gpu-box #2" says which machine it runs on."""
        machine = options.get("provider_name") or "API"
        n = 1
        while f"{machine} #{n}" in used:
            n += 1
        return f"{machine} #{n}"

    def run(self, manager, s, agent) -> bool:
        """The agent loop for any OpenAI-compatible API. Once the helper is done with a function (it matched or
        released everything it held), that function's messages fold into one line: the conversation stays small
        without changing anything before that point, so the provider's prompt cache keeps serving the start of it (a
        conversation trimmed in place was sent again whole every step: 240K tokens per match on Ridge Racer V).
        Should one function's work still outgrow the model's context (`context_tokens`, or what a local llama.cpp
        server says it has), its older tool output is trimmed, harder when the server still says it's too long."""
        # (imported here: app/agents.py, which runs this, imports this package)
        from ..app import tools
        from ..app.agents import LIMIT, PER_SESSION, THROTTLED, Fatal, limit_reset, no_credit

        net = manager.net
        base = (s.options.get("base_url") or "").rstrip("/")
        model = s.options.get("model") or ""
        if not base or not model:
            raise RuntimeError("set the API address and model first")
        headers = {"Content-Type": "application/json"}
        if s.options.get("api_key"):
            headers["Authorization"] = f"Bearer {s.options['api_key']}"
        schema = api_tool_schemas(fork=bool(net.fork_slug(s.slug)))
        messages = [{"role": "system", "content": tools.INSTRUCTIONS},
                    {"role": "user", "content": manager.prompt(s)}]
        count = int(s.options.get("per_session", PER_SESSION))
        used_tool, start, done = False, len(messages), 0
        with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT, connect=API_CONNECT_TIMEOUT)) as client:
            window = context_window(client, base, s.options)
            s.push("info", f"session {s.runs}: {model} via {base} ({window // 1000}K tokens of context)")
            budget = int(window * API_CONTEXT_SHARE) - _tokens(schema)  # room for the reply and the tools' definitions
            for _step in range(int(s.options.get("max_steps", API_STEPS))):
                if s.stop.is_set():
                    return True
                for _attempt in range(API_TRIM_RETRIES):
                    fit(messages, budget, keep=start)
                    request = {"model": model, "messages": messages, "tools": schema, "tool_choice": "auto"}
                    if s.options.get("effort"):
                        request["reasoning_effort"] = s.options["effort"]
                    r = client.post(f"{base}/chat/completions", headers=headers, json=request)
                    if r.status_code == 200 or not _too_long(r):
                        break
                    budget = int(budget * API_TRIM_FACTOR)  # the server counts differently, or knows better: trim more
                if r.status_code != 200:
                    if (why := no_credit(r.text)) or r.status_code == PAYMENT_REQUIRED:
                        raise Fatal(why or no_credit("payment required"))
                    s.limited = bool(LIMIT.search(r.text))
                    s.limit_until = (limit_reset(r.text) or _retry_after(r)) if s.limited else None
                    s.throttled = not s.limited and (r.status_code == TOO_MANY_REQUESTS or bool(THROTTLED.search(r.text)))
                    s.error = f"API error {r.status_code}: {r.text[:ERROR_SHOWN]}"
                    if _too_long(r):
                        s.error = ("the model's context is too small for this work even after trimming: give it at "
                                   "least 16K tokens (llama.cpp: -c 16384; Ollama: OLLAMA_CONTEXT_LENGTH=16384; LM "
                                   "Studio: Context Length) and set it in Agents & models")
                    s.push("error", s.error)
                    return False
                try:
                    reply = r.json()
                    msg = reply["choices"][0]["message"]
                except (ValueError, KeyError, IndexError, TypeError):  # LM Studio's address without /v1 answers so
                    s.error = (f"{base}/chat/completions answered with no reply from a model ({r.text[:ERROR_SHOWN]}): "
                               "the address must be the API's own, which often ends in /v1 (LM Studio: "
                               "http://127.0.0.1:1234/v1)")
                    s.push("error", s.error)
                    return False
                if isinstance(reply.get("usage"), dict):
                    manager.account(s, api_usage(reply["usage"]))
                if msg.get("reasoning_content"):
                    s.push("think", msg["reasoning_content"][:REASONING_SHOWN])
                if msg.get("content"):
                    s.push("text", msg["content"])
                calls = msg.get("tool_calls") or []
                # its reasoning goes back with the calls it made: DeepSeek's thinking mode refuses the next request
                # without it ("The reasoning_content in the thinking mode must be passed back to the API": 106 times
                # in a day, each a 5-10 minute wait); servers that don't send one get none back
                messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "reasoning_content")}
                                | {"role": "assistant"})
                if not calls:
                    if not used_tool:  # a model that can't call tools answers in words, and never starts
                        s.error = (f"{model} answered in words without calling a tool: helpers work through tools "
                                   "(claim_function, compile_and_diff...), so the model must support tool calls "
                                   "(function calling). A model trained only to write C can't be a helper")
                        s.push("error", s.error)
                    return used_tool
                finishing = False
                for c in calls:
                    name = c["function"]["name"]
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    used_tool = True
                    s.usage["tool_calls"] += 1
                    s.push("tool", f"{name}({args_brief(args)})")
                    result = tools.call(net, agent, s.slug, name, args)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                    finishing |= name in FINISHING
                # Done with what it held (in a room the host holds the claim: nothing here says when): what led up
                # to this turn folds into a line, and this turn stays whole (a fork's match shows the file's data to
                # fix next) until the next fold
                if finishing and s.slug not in net.links and not _holds_claim(net, agent.id):
                    done += 1
                    turn = len(messages) - len(calls) - 1
                    if start < turn:
                        fold(messages, start, turn, done, _done_line(net, s.slug, messages[start:], done, count))
                        start += FOLDED
                    if done >= count:  # the session's claims are done: the next one starts fresh
                        return True
        return used_tool


API = Api()


def endpoint_entry(e: dict) -> dict:
    """A saved API endpoint, with what's left out filled in from its provider (a name: the provider's, or for a
    custom server the address's host, so sessions say which machine they run on)."""
    provider = e.get("provider") if e.get("provider") in API_PRESETS else "custom"
    preset = API_PRESETS[provider]
    base = (e.get("base_url") or preset["base_url"]).strip()
    host = re.sub(r"^\w+://", "", base).split("/", 1)[0]
    moved = provider == "custom" or base.rstrip("/") != preset["base_url"].rstrip("/")  # Ollama on another box...
    out = {"id": str(e.get("id") or ""), "provider": provider, "base_url": base,
           "model": (e.get("model") or preset["model"]).strip(),
           "name": (e.get("name") or "").strip() or (host if moved and host else preset["name"]) or "API"}
    for k in ("api_key", "context_tokens", "max_sessions"):
        if e.get(k):
            out[k] = e[k]
    return out


def is_local(base: str, resolve: bool = True) -> bool:
    """An address on this computer or the local network (a llama.cpp box, Ollama on another machine): a private,
    loopback, link-local or Tailscale address, a name without dots or under .local/.lan/.internal/.home.arpa, or
    (`resolve`) a name that resolves to such addresses only."""
    import ipaddress
    import socket

    host = re.sub(r"^\w+://", "", base.strip()).split("/", 1)[0].rsplit("@", 1)[-1]
    host = host[1:].split("]", 1)[0] if host.startswith("[") else host.split(":", 1)[0] if host.count(":") == 1 else host
    if not host:
        return False

    def private(ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        tailscale = a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")
        return a.is_private or a.is_loopback or a.is_link_local or tailscale or a.is_unspecified

    if private(host):
        return True
    if "." not in host or re.search(r"\.(local|lan|home|internal|localdomain|home\.arpa)$", host, re.I) \
            or host.lower() == "localhost":
        return True
    if not resolve or re.fullmatch(r"[\d.]+|[0-9a-fA-F:]+", host):
        return False
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return False
    return bool(addrs) and all(private(a) for a in addrs)


def _holds_claim(net, agent_id: int) -> bool:
    return net.store.one("SELECT 1 FROM claims WHERE agent_id=?", agent_id) is not None


def _done_line(net, slug: str, span: list[dict], done: int, count: int) -> str:
    """One line for a function the API helper is done with: what became of it, and what's next."""
    from ..network import NetworkError  # (here: network imports this package)

    names, compiles = [], 0
    for m in span:
        for c in m.get("tool_calls") or []:
            fn = c["function"]
            if fn["name"] not in FINISHING:
                continue
            compiles += fn["name"] == "compile_and_diff"
            try:
                said = json.loads(fn.get("arguments") or "{}").get("function") or ""
            except ValueError:
                said = ""
            names += [n.strip() for n in str(said).split(",") if n.strip() and n.strip() not in names]
    matched = []
    for name in names:
        try:
            if net._function(name, slug)["status"] == "matched":
                matched.append(name)
        except NetworkError:
            continue
    aside = [n for n in names if n not in matched]
    what = "; ".join(bit for bit in (f"matched {', '.join(matched)}" if matched else "",
                                     f"set {', '.join(aside)} aside with notes" if aside else "") if bit)
    nxt = "claim the next one" if done < count else "that was the last one for this session: stop now"
    return (f"(The work up to your last call, folded into this line to keep the conversation short: "
            f"{plural(compiles, 'compile')}; {what or 'nothing held any more'}.) {done} of {count} claims this "
            f"session are done: {nxt}.")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def api_usage(usage: dict) -> dict:
    """An OpenAI-compatible reply's usage as `usage` items count it: its prompt tokens include those served from the
    provider's cache (OpenAI: prompt_tokens_details.cached_tokens, DeepSeek: prompt_cache_hit_tokens)."""
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = number(details, "cached_tokens") or number(usage, "prompt_cache_hit_tokens")
    prompt, output = number(usage, "prompt_tokens"), number(usage, "completion_tokens")
    if not (prompt or output):
        return {"tokens": number(usage, "total_tokens"), "turns": 1}
    return {"input": max(0, prompt - cached), "cache_read": cached, "output": output, "turns": 1}


def _retry_after(r) -> float | None:
    """A server's Retry-After header (in seconds) as a time."""
    said = (r.headers.get("retry-after") or "").strip()
    return time.time() + float(said) if re.fullmatch(r"\d+(?:\.\d+)?", said) else None


def fold(messages: list[dict], start: int, end: int, n: int, line: str) -> None:
    """Replace messages[start:end] (a finished function's work) with one call and `line` as its result: a call and
    its result keep the conversation valid for every API, and what came before stays as it was sent."""
    call = f"folded-{n}"
    messages[start:end] = [{"role": "assistant", "content": None, "tool_calls": [
                               {"id": call, "type": "function", "function": {"name": "project_info", "arguments": "{}"}}]},
                           {"role": "tool", "tool_call_id": call, "content": line}]


def _tokens(obj) -> int:
    """A cautious token estimate (CHARS_PER_TOKEN)."""
    return len(obj if isinstance(obj, str) else json.dumps(obj)) // CHARS_PER_TOKEN + TOKENS_PER_MESSAGE


def _too_long(r) -> bool:
    """The server refused the request because it doesn't fit the model's context."""
    return r.status_code in TOO_LONG_STATUSES and bool(re.search(
        r"context|too long|exceed|maximum.*tokens|n_ctx|prompt is too|reduce the length", r.text[:TOO_LONG_READ], re.I))


def fit(messages: list[dict], budget: int, keep: int = 2) -> int:
    """Shrink the conversation to `budget` tokens, only from `keep` on (what's before it, finished functions folded
    into a line each, stays as it was sent, for the provider's prompt cache): older tool output goes first (a
    function's assembly and diffs from several steps ago), then older reasoning. The latest exchange stays whole.
    Returns the estimate after."""
    total = sum(_tokens(m) for m in messages)
    keep_from = max(keep, len(messages) - 2)
    for kinds in (("tool",), ("assistant",)):
        for m in messages[max(2, keep):keep_from]:
            if total <= budget:
                return total
            text = m.get("content")
            if m.get("role") in kinds and isinstance(text, str) and len(text) > TRIM_ABOVE:
                before = _tokens(m)
                m["content"] = text[:TRIM_TO] + "\n[... trimmed to save context: call the tool again if you need this]"
                total -= before - _tokens(m)
    return total


def context_window(client, base: str, options: dict) -> int:
    """The model's context in tokens: what the user set, else what a llama.cpp server reports (/props), else a
    guess (local servers often run small contexts)."""
    try:
        if int(options.get("context_tokens") or 0) >= MIN_CONTEXT:
            return int(options["context_tokens"])
    except (TypeError, ValueError):
        pass
    if is_local(base):
        try:
            r = client.get(re.sub(r"/v1$", "", base) + "/props", timeout=PROPS_TIMEOUT)
            n = (r.json().get("default_generation_settings") or {}).get("n_ctx") if r.status_code == 200 else None
            if isinstance(n, int) and n >= MIN_CONTEXT:
                return n
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return LOCAL_CONTEXT
    return REMOTE_CONTEXT


def api_tool_schemas(fork: bool = False) -> list[dict]:
    """OpenAI-style tool definitions for the built-in agent, from the same tools CLI agents get."""
    from ..app import tools  # (here: app/tools.py imports this package)

    s = lambda **props: {"type": "object", "properties": props, "required": [k for k, v in props.items() if v.pop("req", False)]}
    st = lambda desc, req=False: {"type": "string", "description": desc, "req": req}
    it = lambda desc: {"type": "integer", "description": desc}
    bo = lambda desc: {"type": "boolean", "description": desc}
    defs = {
        "project_info": ("Which game, compiler and progress.", s()),
        "claim_function": ("Claim the next function (or one by name/address). Returns assembly, data, best attempt, "
                           "notes and a draft. count > 1: a batch of small functions to judge together.",
                           s(function=st("optional name or address"), strategy=st("finish | easiest | closest | hardest | random"),
                             count=it("up to 6 small functions at once"))),
        "get_function": ("Show any function by name or address.", s(function=st("name or address", True))),
        "compile_and_diff": ("Compile C for the function, link it at its address and diff against the ROM. "
                             "Score 0 = matched. Several functions: names comma-separated, all definitions in source.",
                             s(function=st("function name, or several: a, b, c", True), source=st("C source", True))),
        "release_function": ("Give up a function with notes for the next agent.",
                             s(function=st("function", True), notes=st("what you tried, what is left", True))),
        "get_context": ("The shared header included in every compile.", s()),
        "edit_context": ("Replace `old` (exactly once) with `new` in the shared header, or append when old is empty.",
                         s(old=st("text to replace, or empty", True), new=st("replacement", True),
                           message=st("why", True))),
        "rename_symbol": ("Give a function or data address a real name.",
                          s(old=st("current name", True), new=st("new name", True), message=st("why"))),
        "split_function": ("Split a merged function at an address.",
                           s(function=st("function", True), at=st("address like 0x80001234", True), new_name=st("name"))),
        "read_data": ("Hex dump of the original's data at an address or symbol.", s(what=st("address or symbol", True),
                                                                                    length=it("bytes, up to 512"))),
        "get_class": ("C++ class layout found in the game's code: fields by offset and virtual functions by slot. "
                      "Give a vtable address or one of its functions; empty lists the classes.",
                      s(what=st("vtable address, or a virtual function / constructor"))),
        "guess_struct": ("The likely struct behind a function's pointer, as C with offsets, from every function the same "
                         "pointer reaches; where else it flows; the type matched code already uses.",
                         s(function=st("function", True), argument=st("arg0-arg3, return, or a global; +0x14 follows "
                                                                       "the pointer at 0x14"))),
        "redraft": ("m2c's draft of a function made again with your declarations (a struct, a prototype) added to "
                    "the shared header's, which they replace where they declare the same names. Recorded nowhere.",
                    s(function=st("function", True), context=st("C declarations", True))),
        "compiler_map": ("Which compiler built each part of the game, where it differs from today's. survey=true starts "
                         "the judge's survey of other compilers in the background; check_ranges=true looks at the "
                         "applied ranges again.", s(survey=bo("start the survey"), check_ranges=bo("check applied ranges"))),
        "list_functions": ("List functions by status.", s(status=st("open | matched | all"), limit=it("max rows"))),
        "add_note": ("Leave a note on a function for the next agent on it.", s(text=st("note", True), function=st("function"))),
        "search_flags": ("Build the function's C with each flag variant of its compiler, ranked: for C that looks right "
                         "but won't match.", s(function=st("function", True), compiler=st("another compiler"),
                                                source=st("C (default: the best attempt)"))),
        "claim_readable": ("Take the next matched function that isn't readable yet.", s()),
        "make_readable": ("A readable rewrite of a matched function (names, types, a comment); kept only if it still "
                          "matches byte for byte.", s(function=st("function", True), source=st("readable C", True))),
        "share_knowledge": ("Propose something every agent on this game should know, or back proposal #N "
                            "(supports=N). It counts once two different models agree. Not for compiler questions.",
                            s(text=st("what every agent should know"), supports=it("a proposal's number to back"))),
        "suggest": ("Suggest a setup change for the user (another compiler or flags), with evidence.",
                    s(text=st("what and why", True), compiler=st("toolchain id"), flags=st("flags"),
                      function=st("a function whose best attempt should match with it: checked now"),
                      ranges=st("address ranges it built, when not the whole game: 0x10BFD0-0x10CD40, ..."),
                      source=st("the C to check it with (default: the best attempt)"))),
        "try_compiler": ("Compile a function's C with another compiler and flags, recording nothing: test before suggesting.",
                         s(function=st("function", True), compiler=st("toolchain id", True), flags=st("flags"),
                           source=st("the C (default: the best attempt)"))),
        "search_notes": ("Search notes.", s(query=st("text", True))),
        "find_similar": ("Functions that look like this one, matched ones first, with the best one's source.",
                         s(function=st("function", True), limit=it("how many"))),
        "guide": ("Decompilation know-how; no topic lists topics.", s(topic=st("topic"))),
        "read_file": ("Read a project file with line numbers.", s(path=st("path from the project root", True),
                                                                 start=it("first line"), count=it("lines"))),
        "search_code": ("Regex search over the project's C/C++ sources and headers.",
                        s(pattern=st("regular expression", True), glob=st("optional file glob"))),
        "edit_file": ("Replace `old` (exactly once) with `new` in a source/header file, or append when old is empty. "
                      "Undone if any match breaks.", s(path=st("file", True), old=st("text to replace, or empty", True),
                                                       new=st("replacement", True), message=st("why", True))),
        "file_status": ("How far a source file (or a function's file) is from being linked: functions left, its data "
                        "next to the original's, why the last link failed.", s(path=st("file or function", True))),
        "split_file": ("Divide a source file at a function: it and the rest of the file's code (and data from the given "
                       "offsets) go to a new file. Kept only if the game still builds identically.",
                       s(path=st("file", True), at=st("function name or .text offset", True),
                         new_name=st("new file path (optional)"), data=st("e.g. .rodata:0x88 .data:0x28 (optional)"))),
    }
    if fork:
        defs["compile_and_diff"] = ("Write one complete function definition into its source file, build it and diff "
                                    "with objdiff. 100% / score 0 = matched.",
                                    s(function=st("function name", True), source=st("the function definition", True)))
    names = set(tools.tool_names(fork))
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}}
            for n, (d, p) in defs.items() if n in names]
