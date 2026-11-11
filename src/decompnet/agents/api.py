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

import itertools
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
API_RETRIES, API_RETRY_WAIT = 3, 10  # dropped requests and gateway 5xx: retry the same conversation in place
# a function is done with when the helper compiled it to a match or released it: its work then folds into a line
FINISHING = ("compile_and_diff", "compile_patch", "compile_variants", "compile_family", "release_function")
FOLDED = 2  # messages a fold leaves: one call and its result


class Api(Agent):
    id = "api"
    name = "API key"
    vendor = "DeepSeek, OpenRouter, Ollama, any OpenAI-compatible API"
    docs = "https://api-docs.deepseek.com"
    recommended = True  # decompdecomp runs its whole loop, and no other program's update can break it
    uses_endpoints = True
    # OpenAI's reasoning_effort, which OpenAI-compatible servers with reasoning models take; which words a model knows
    # is its own (a llama.cpp model's chat template took xhigh, medium and low, and refused high and max, saying so)
    takes_effort = True
    efforts = ("minimal", "low", "medium", "high", "xhigh", "max")

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
        schema = api_tool_schemas(fork=bool(net.fork_slug(s.slug)), names=tools.project_tools(net, s.slug, agent))
        manager.prepare_session(s, agent)
        if s.context_budget and s.context_budget.waiting:
            s.rotated=True
            return True
        messages = [{"role": "system", "content": manager.app_instructions(s)},
                    {"role": "user", "content": manager.prompt(s)}]
        from ..app.session_recovery import api_messages, handoff
        identity = [s.slug, s.agent_name, base, model]
        saved = api_messages(net, s, identity)
        if saved:
            saved[0] = messages[0]  # Current rules and tools apply after restart.
            messages = saved + [{'role':'user','content':'Resume after interruption. Check current project state and reclaim eligible work. ' + handoff(s)}]
            s.push('info','Restored saved API conversation and function handoff.')
        if s.record:
            s.record('prompt', messages)
            s.record('instructions', messages[0]['content'])
        count = int(s.options.get("per_session", PER_SESSION))
        used_tool, start, done = bool(saved), 2, 0
        with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT, connect=API_CONNECT_TIMEOUT)) as client:
            window = context_window(client, base, s.options)
            s.push("info", f"session {s.runs}: {model} via {base} ({window // 1000}K tokens of context)")
            schema = compact_tool_schema(schema, window, messages)
            if s.record:s.record('tool_definitions', schema)
            budget = int(window * API_CONTEXT_SHARE) - _tokens(schema)  # room for the reply and the tools' definitions
            focused = bool(s.options.get('focus_id'))
            steps = itertools.count() if focused else range(int(s.options.get("max_steps", API_STEPS)))
            idle_replies = 0
            for _step in steps:
                if s.rotated or s.stop.is_set():return True
                api_messages(net, s, identity, messages)
                if focused:
                    from ..app.focused_agents import completion
                    if completion(manager, s):
                        return True
                if not focused and getattr(s,'context_budget',None) and s.context_budget.ready():
                    s.rotated=True
                    s.push('info','Starting a fresh session with saved candidates and experiments.')
                    return True
                if s.stop.is_set() or s.rotated:
                    return True
                if focused and len(messages) >= 10 and sum(_tokens(m) for m in messages) > budget * .85:
                    compact_focus(client, base, headers, model, messages, budget, manager, s)
                for _attempt in range(API_TRIM_RETRIES):
                    fit(messages, budget, keep=start)
                    request = {"model": model, "messages": messages, "tools": schema, "tool_choice": "auto"}
                    if s.options.get("effort"):
                        request["reasoning_effort"] = s.options["effort"]
                    if s.record:
                        s.record('api_request', request)
                    r = _post(client, f"{base}/chat/completions", headers, request, s.stop)
                    if s.rotated or r.status_code == 200 or not _too_long(r):
                        break
                    budget = int(budget * API_TRIM_FACTOR)  # the server counts differently, or knows better: trim more
                if s.rotated and r.status_code != 200:
                    return True
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
                if s.rotated:
                    return True
                if msg.get("reasoning_content"):
                    s.push("think", msg["reasoning_content"])
                if msg.get("content"):
                    s.push("text", msg["content"])
                calls = msg.get("tool_calls") or []
                # its reasoning goes back with the calls it made: DeepSeek's thinking mode refuses the next request
                # without it ("The reasoning_content in the thinking mode must be passed back to the API": 106 times
                # in a day, each a 5-10 minute wait); servers that don't send one get none back
                messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "reasoning_content")}
                                | {"role": "assistant"})
                if not calls:
                    if focused and used_tool and idle_replies < 2 and not s.stop.is_set():
                        idle_replies += 1
                        messages.append({'role':'user','content':'Continue the same focused conversation. Test the next hypothesis with tools; the app will stop you on a match or deadline.'})
                        continue
                    if not used_tool:  # a model that can't call tools answers in words, and never starts
                        s.error = (f"{model} answered in words without calling a tool: helpers work through tools "
                                   "(claim_function, compile_and_diff...), so the model must support tool calls "
                                   "(function calling). A model trained only to write C can't be a helper")
                        s.push("error", s.error)
                    return used_tool
                idle_replies = 0
                finishing = False
                for c in calls:
                    if s.rotated or s.stop.is_set():return True
                    name = c["function"]["name"]
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    used_tool = True
                    s.usage["tool_calls"] += 1
                    s.push("tool", f"{name}({args_brief(args)})")
                    if s.record:
                        s.record('tool_request', {'name': name, 'arguments': args, 'call_id': c['id']})
                    result = tools.call(net, agent, s.slug, name, args)
                    if s.record:
                        s.record('tool_response', {'name': name, 'content': result, 'call_id': c['id']})
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                    if s.options.get('task') == 'boundary' and name == 'suggest_boundary' and not result.startswith('error:'):
                        return True
                    finishing |= name in FINISHING or (name in {"propose_asset_region","defer_asset_region"} and not result.startswith("error:"))
                # Done with what it held (in a room the host holds the claim: nothing here says when): what led up
                # to this turn folds into a line, and this turn stays whole (a fork's match shows the file's data to
                # fix next) until the next fold
                if not focused and finishing and s.slug not in net.links and not _holds_claim(net, agent.id, s.slug):
                    done += 1
                    if s.options.get('task')=='assets':
                        if done>=count:return True
                        continue
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


def _holds_claim(net, agent_id: int, slug: str) -> bool:
    return net.project_store(slug).one("SELECT 1 FROM claims WHERE agent_id=?", agent_id) is not None


def _done_line(net, slug: str, span: list[dict], done: int, count: int) -> str:
    """One line for a function the API helper is done with: what became of it, and what's next."""
    from ..network import NetworkError  # (here: network imports this package)

    names, compiles = [], 0
    for m in span:
        for c in m.get("tool_calls") or []:
            fn = c["function"]
            if fn["name"] not in FINISHING:
                continue
            compiles += fn["name"] in ("compile_and_diff", "compile_patch", "compile_variants", "compile_family")
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
            f"{plural(compiles, 'compile tool call')}; {what or 'nothing held any more'}.) {done} of {count} claims this "
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


def _post(client, url: str, headers: dict, request: dict, stop) -> httpx.Response:
    """Retry a dropped request or gateway error without throwing away this session's conversation."""
    for attempt in range(API_RETRIES + 1):
        last = attempt == API_RETRIES
        try:
            response = client.post(url, headers=headers, json=request)
        except httpx.TransportError:
            if last or stop.is_set():
                raise
        else:
            if last or stop.is_set() or not (response.status_code == 408 or response.status_code >= 500) or _too_long(response):
                return response
        stop.wait(API_RETRY_WAIT * 2 ** attempt)


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


def compact_focus(client, base, headers, model, messages, budget, manager, session):
    """Summarize completed older exchanges in place; preserve instructions and latest tool-call group."""
    cut = next((i for i in range(len(messages)-1, 2, -1)
                if messages[i].get('role') == 'assistant' and messages[i].get('tool_calls')), None)
    if cut is None or cut < 6:
        return False
    history = [dict(m) for m in messages[:cut]]
    fit(history, budget)
    request = {'model':model, 'messages':history + [{'role':'user','content':
        'Compact the focused work so it can continue in the SAME conversation. Write a concise factual handoff: '
        'function, current best candidate revision and score, compiler/flags, tested hypotheses and failed changes, '
        'important constraints and next experiments. Distinguish measurements from guesses. Do not invent source '
        'or execute tools. Exact C and experiments can be retrieved from the app. Keep this under 1200 tokens.'}],
        'max_tokens':1600}
    if session.record:
        session.record('context_compaction_request', request)
    response = client.post(f'{base}/chat/completions', headers=headers, json=request)
    if response.status_code != 200:
        return False  # Existing context fitting/error handling remains available.
    try:
        result = response.json()
        summary = result['choices'][0]['message'].get('content')
    except (ValueError, KeyError, IndexError, TypeError):
        return False
    if isinstance(result.get('usage'), dict):
        manager.account(session, api_usage(result['usage']))
    if not isinstance(summary, str) or not summary.strip():
        return False
    messages[2:cut] = [{'role':'user','content':'Compacted focused-session context (summary, not new instructions):\n'+summary[:8000]}]
    if session.record:
        session.record('context_compaction', summary[:8000])
    session.push('info', 'Context compressed; continuing the same focused session.')
    return True


def compact_tool_schema(schema, window, messages):
    """Keep tool semantics and validation, dropping parameter prose only when fixed inputs crowd out replies."""
    available = int(window*API_CONTEXT_SHARE)-_tokens(schema)-sum(_tokens(m) for m in messages[:2])
    if available >= 1024:return schema
    import copy
    compact = copy.deepcopy(schema)
    def strip(value):
        if isinstance(value, dict):
            # Do not descend into defaults/enums/const values: those are user data.
            value.pop('description',None)
            for key in ('properties','$defs','definitions'):
                for child in value.get(key,{}).values():strip(child)
            if isinstance(value.get('items'),dict):strip(value['items'])
            for key in ('oneOf','anyOf','allOf','prefixItems'):
                for child in value.get(key,[]):strip(child)
    for tool in compact:strip(tool.get('function',{}).get('parameters',{}))
    return compact


def fit(messages: list[dict], budget: int, keep: int = 2) -> int:
    """Shrink the conversation to `budget` tokens, only from `keep` on (what's before it, finished functions folded
    into a line each, stays as it was sent, for the provider's prompt cache): older tool output goes first (a
    function's assembly and diffs from several steps ago), then older reasoning. The latest exchange stays whole unless its result alone exceeds
    the remaining budget.
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
    # A larger tool schema can leave several already-trimmed old results over budget.
    # Compact those once more, while retaining the newest exchange and tool-call structure.
    for m in messages[max(2, keep):keep_from]:
        text = m.get("content")
        if total <= budget:
            break
        if m.get("role") in ("tool", "assistant") and isinstance(text, str) and len(text) > 120:
            before = _tokens(m)
            m["content"] = text[:80] + "\n[Earlier output trimmed; call the tool to recover.]"
            total -= before - _tokens(m)
    # A single large function can exceed a small model's entire window. Preserve
    # custom prompts and call IDs; shorten its newest result only as a last resort.
    for m in messages[max(2, keep):]:
        text = m.get("content")
        if total <= budget:
            break
        if m.get("role") == "tool" and isinstance(text, str) and len(text) > 120:
            before = _tokens(m)
            suffix = "\n[Output trimmed to fit context; fetch a smaller section with the tool.]"
            allowance = budget - (total - before)
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                m["content"] = text[:middle] + suffix
                if _tokens(m) <= allowance:
                    low = middle
                else:
                    high = middle - 1
            m["content"] = text[:low] + suffix
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


def api_tool_schemas(fork: bool = False, names: list[str] | None = None) -> list[dict]:
    """OpenAI-style tool definitions for the built-in agent, from the same tools CLI agents get (`names`: the
    project's, tools.project_tools; else every tool of its kind)."""
    from ..app import tools  # (here: app/tools.py imports this package)

    s = lambda **props: {"type": "object", "properties": props, "required": [k for k, v in props.items() if v.pop("req", False)]}
    st = lambda desc, req=False: {"type": "string", "description": desc, "req": req}
    it = lambda desc: {"type": "integer", "description": desc}
    bo = lambda desc: {"type": "boolean", "description": desc}
    defs = {
        "claim_asset_region": ("Claim/recover one asset region with evidence and prior attempts.",s()),
        "inspect_asset_region": ("Read a held region and up to 4096 original bytes.",s(region=st("region ID",True),claim_token=st("claim token",True),offset=it("ROM offset"),length=it("1–4096 bytes"))),
        "preview_asset_region": ("Compare textures, validate image/audio or test geometry; returns metadata, not base64.",s(region=st("region ID",True),claim_token=st("claim token",True),action=st("textures, preview or model"),options={"type":"object","description":"Source/offset, format, dimensions and palette or audio settings"},model_settings={"type":"object","description":"Vertex/index layout"})),
        "propose_asset_region": ("Save an advisory asset proposal or independent critique for user review.",s(region=st("region ID",True),claim_token=st("claim token",True),proposal={"type":"object","req":True,"description":"For discovery: name,kind,confidence,evidence,counter,uncertainty,approach,citations [{offset,function?}],format_notes; optional start/end, related_regions, preview_settings, model_settings. When claim returns review_target: verdict (agree/disagree/uncertain), evidence and citations [{offset}] independently inspected by this claim."})),
        "defer_asset_region": ("Record a failed approach and continue the queue.",s(region=st("region ID",True),claim_token=st("claim token",True),approach=st("approach tried",True),findings=st("findings and missing evidence",True))),
        "get_boundary_evidence": ("Read original mapped boundary evidence, neighbours, direct edges, symbols and scan limits. No claim needed.", s(function=st("selected function", True))),
        "suggest_boundary": ("Save an advisory recommendation; no layout change. End the investigation after reporting.",
            s(function=st("selected function", True), action=st("delay_slot, join_previous, adjust, padding, data, keep, unclear", True),
              supporting=st("Supporting evidence", True), counter=st("Counter-evidence or explicitly none observed", True),
              uncertainty=st("Remaining uncertainty", True), citations=st("Comma-separated hex instruction addresses from evidence", True),
              end=st("Exclusive hex end for adjust"), confidence=st("strong, tentative, unclear"))),
        "get_claims": ("Recover only your live claims and claim tokens; no build or mutation.", s()),
        "project_info": ("Game, compiler and progress; detail=true includes all facts and suggestions.", s(detail=bo("full details"))),
        "get_candidate": ("Read candidate C and its revision, without assembly or notes.", s(function=st("function", True), base={"type":"string","enum":["current","saved_best","last_compiling","reported_corrected"],"description":"Named recovery base, never a revision hash; selection does not judge source."})),
        "compile_patch": ("Compile one exact-text replacement against a candidate revision using the normal judge.",
                          s(function=st("function", True), revision=st("revision from get_candidate or compile feedback", True),
                            old=st("text occurring exactly once", True), new=st("replacement", True),
                            repair_note=st("Optional decisive repair"), repair_category=st("Repair category"),
                            repair_automation=st("Potential deterministic repair"), occurrence=it("1-based occurrence; 0 requires unique anchor"))),
        "compiler_experiments": ("Bounded diagnostic source/profile trials on one candidate revision. 1–6 compiler profiles (compiler=current uses this function’s current compiler); optional independent old/new/occurrence patches. No compiler choice applied.",
                                 s(function=st("function",True),revision=st("candidate revision",True),trials={"type":"array","minItems":1,"maxItems":6,"req":True,"items":{"type":"object","additionalProperties":False,"properties":{"compiler":{"type":"string","description":"current or an exact compiler ID; use try_local_repairs for repair families"},"flags":{"type":"string"},"old":{"type":"string"},"new":{"type":"string"},"occurrence":{"type":"integer"}},"required":["compiler"]}})),
        "get_search_brief": ("Read current allocation lines, failed experiments and reviewed roots; no compile.",s(function=st("function",True))),
        "review_search_seed": ("Review a diagnostic seed for deep search. Changed/unknown signatures stay diagnostic-only.",s(function=st("function",True),trial_id=st("trial ID",True),review_note=st("Why this seed; caller/interface checks"),enabled=bo("Enable seed; false disables"))),
        "get_compiler_trial": ("Retrieve the saved C/compiler/flags diagnostic pair; no compile/application.",s(function=st("function",True),trial_id=st("trial id",True),include_header=bo("Include historical shared header snapshot; default false to save context"))),
        "try_local_repairs": ("Try applicable local repair families on one snapshot, max six normal compiles/60 seconds. Keeps best continuation; replaces parallel family probes.",
                              s(function=st("function",True),revision=st("candidate revision",True),families={"type":"array","items":{"type":"string"}},max_trials=it("1–6; default 6"))),
        "compile_family": ("Bounded repair family; selector has one mode: next, anchor (old/occurrence), or declaration (declaration). Omit for pattern families.",
                           s(function=st("function", True),revision=st("candidate revision", True),family=st("repair family", True),selector={"type":"object","oneOf":[
                               {"type":"object","properties":{"mode":{"const":"next"}},"required":["mode"],"additionalProperties":False},
                               {"type":"object","properties":{"mode":{"const":"anchor"},"old":{"type":"string"},"occurrence":{"type":"integer"}},"required":["mode","old"],"additionalProperties":False},
                               {"type":"object","properties":{"mode":{"const":"declaration"},"declaration":{"type":"string"}},"required":["mode","declaration"],"additionalProperties":False}]})),
        "family_targets": ("Read current revision, valid declaration names/pairs and tried status; expression optionally lists literal occurrences. No compile.",
                           s(function=st("function", True), family=st("declaration_order | signedness | casts"), expression=st("literal expression to locate"))),
        "compile_variants": ("Try up to six independent old/new patches against one revision; normal judge and limits. Reports the selected best candidate revision; baseline retained when nothing improves.",
                             s(function=st("function", True), revision=st("candidate revision", True),
                               patches={"type": "array", "minItems": 1, "maxItems": 6, "req": True,
                                        "items": {"type": "object", "properties": {"old": {"type": "string"}, "new": {"type": "string"}, "occurrence": {"type": "integer", "minimum": 0}}, "required": ["old", "new"]}})),
        "get_assembly": ("Read target assembly: focus=mismatch or a callee symbol/short anchor for a bounded region; omit focus to page.",
                         s(function=st("function", True), start=it("zero-based line"), limit=it("up to 200 lines"), focus=st("mismatch:N, body, symbol or short assembly anchor"))),
        "claim_function": ("Reserve the next function or batch and return saved candidate C. No diagnostic build or draft during claiming. "
                           "Use get_assembly/get_function(section=brief) for supporting evidence and get_claims to recover leases.",
                           s(function=st("optional name or address"), strategy=st("no_compiling_c | finish | easiest | least_tried | oldest_checked | untried | closest | hardest | random"),
                             count=it("up to 6 small functions at once"))),
        "get_function": ("Show any function by name or address.", s(function=st("name or address", True), section=st("summary (default) | brief | all | history | candidate | experiment | quality | notes | declarations | compiler | layouts"), start=it("Character offset for supporting sections"), limit=it("Page characters, default 6000, maximum 12000; complete editable C is not truncated"))),
        "compile_and_diff": ("Whole-function rewrite: small edits should use compile_patch. Compile, link and diff against the ROM. "
                             "Score 0 = matched. Several functions: names comma-separated, all definitions in source.",
                             s(function=st("function name, or several: a, b, c", True), source=st("C source", True),
                             repair_note=st("Optional one-sentence decisive change, single function only; saved only if it matches"),
                             repair_category=st("types | casts | constants | addresses | signatures | layout | temporaries | expression_order | control_flow | compiler_settings | other"),
                             repair_automation=st("How a deterministic repair could make this change"))),
        "report_candidate_issue": ("Report a saved-C concern; no score or match changes.",
                                   s(function=st("function",True),revision=st("affected revision",True),
                                     issue=st("uninitialized|loop_mutation|wrong_store|wrong_value|other",True),
                                     reason=st("10–600 characters",True),alternative_revision=st("compiled alternative"))),
        "report_repair": ("Annotate your captured verified match for human review, without enabling a rule.",
                          s(function=st("Function you matched", True), change=st("Decisive change; do not guess", True),
                            category=st("Repair category (same as compile_and_diff)"),
                            automation=st("How a deterministic rule could make it"))),
        "last_diff": ("A page of rows from your last compile diff; start is zero-based and limit is at most 200. "
                      "Costs no compile.", s(function=st("function", True), context=it("equal rows around each difference"), start=it("zero-based row"), limit=it("up to 200 rows"))),
        "release_function": ("Give up a function with notes for the next agent, once compile_and_diff says to move "
                             "on (before that it answers once with what to try; call again to release anyway).",
                             s(claim_token=st("Always pass the current claim token from the claim, continuation state or get_candidate on the first release and retries; stale tokens cannot release a newer claim"), function=st("function", True), notes=st("what you tried, what is left", True),
                               not_c=bo("True only with evidence of handwritten assembly or non-function code. Unsupported compiler extensions, asm-register restrictions, return-address builtins or build flags are blockers, not no-C proof; release normally with notes."),
                               move_on=bo("Explicitly release now with precise notes explaining why; bypasses the one-time pushback"),
                               next_experiment={"type":"object","description":"Optional untested exact patch. Anchor/revision are validated; retrieve with get_function(section=experiment).", "properties":{key:{"type":"string"} for key in ('revision','old','new','title')}, "required":["revision","old","new"],"additionalProperties":False},
                               needs=st("Optional direct callee in original assembly; obeys pauses and allows one pivot per unchanged input. Explain inline/header concerns in notes."))),
        "get_context": ("The header used by function; omit only for one held claim or a single-header project.", s(function=st("Function whose per-file header is needed"))),
        "edit_context": ("Replace `old` (exactly once) with `new` in the shared header, or append when old is empty.",
                         s(old=st("text to replace, or empty", True), new=st("replacement", True),
                           message=st("why", True))),
        "rename_symbol": ("Give a function or data address a real name.",
                          s(old=st("current name", True), new=st("new name", True), message=st("why"))),
        "split_function": ("Split a merged function at an address.",
                           s(function=st("function", True), at=st("address like 0x80001234", True), new_name=st("name"))),
        "report_boundary": ("Report that a function really ends earlier: what follows is padding, leftovers or data "
                            "(PC projects: or another function merged with it). ROM: the user approves; PC: agents of "
                            "two models agree and the game still builds byte for byte.",
                            s(function=st("function", True), end=st("where it really ends: an address (PC: or +offset)",
                                                                     True),
                              reason=st("what follows and how you can tell", True))),
        "report_data_region": ("Report an entire suspected data region for user review, not a matching verdict. Claim a representative function; its unit and original image scope the overlay. The user may pause new claims. Use this when the whole entry is data, rather than report_boundary.",
                               s(function=st("claimed representative function", True), start=st("hex address", True),
                                 end=st("exclusive hex end address", True), reason=st("byte evidence for review", True))),
        "resolve_addresses": ("Read-only batch lookup of symbols and hex addresses: function entry, function interior, data, ambiguous or unknown. Returns canonical owner names and offsets. Supply context_function for overlays; never guesses a new entry or changes names/status.",
                              s(queries={"type":"array","items":{"type":"string","minLength":1,"maxLength":200},"minItems":1,"maxItems":20,"req":True}, context_function=st("canonical function in the intended original image"))),
        "read_data": ("Hex dump of the original's data at an address or symbol.", s(what=st("address or symbol", True),
                                                                                    length=it("bytes, up to 512"))),
        "get_data_unit": ("One of the game's data symbols to write as C: address, extent, bytes, users.",
                          s(name=st("data symbol (D_80012340 or its name)", True))),
        "compile_data": ("Judge C defining one data symbol: kept only if it holds the game's bytes there, all of it.",
                         s(name=st("data symbol", True), source=st("C defining it", True))),
        "get_class": ("C++ class layout found in the game's code: fields by offset and virtual functions by slot. "
                      "Give a vtable address or one of its functions; empty lists the classes.",
                      s(what=st("vtable address, or a virtual function / constructor"))),
        "guess_struct": ("The likely struct behind a function's pointer, as C with offsets, from every function the same "
                         "pointer reaches; where else it flows; the type matched code already uses.",
                         s(function=st("function", True), argument=st("arg0-arg3, this (PC), return, or a global; "
                                                                       "+0x14 follows the pointer at 0x14"))),
        "redraft": ("Requires nonempty actual C declarations in context; inspect existing C with get_function(section=candidate). m2c's draft made again with your declarations (a struct, a prototype) added to "
                    "the shared header's, which they replace where they declare the same names. Recorded nowhere.",
                    s(function=st("function", True), context={"type":"string", "minLength":1, "description":"Actual C struct/prototype/extern declarations; not an empty refresh", "req":True})),
        "compiler_map": ("Which compiler built each part of the game, where it differs from today's. survey=true starts "
                         "the judge's survey of other compilers in the background; check_ranges=true looks at the "
                         "applied ranges again.", s(survey=bo("start the survey"), check_ranges=bo("check applied ranges"))),
        "list_functions": ("List functions by status.", s(status=st("open | matched | all"), limit=it("max rows"))),
        "port_readiness": ("What still stands between this game and a native PC port. Read-only.", s()),
        "add_note": ("Leave a note on a function for the next agent on it. Something every agent on this game should "
                     "know goes through share_knowledge.",
                     s(text=st("note", True), function=st("function", True))),
        "search_flags": ("Build the function's C with each flag variant of its compiler, ranked: for C that looks right "
                         "but won't match.", s(function=st("function", True), compiler=st("another compiler"),
                                                source=st("C (default: the best attempt)"))),
        "claim_readable": ("Take the next matched function that isn't readable yet.", s()),
        "polish_function": ("Apply local renames, existing type aliases and a one-line comment without shifting source lines; "
                            "kept only after an exact judge. reuse=true tests a unique verified identical-helper pattern.",
                            s(function=st("function", True), renames=st("JSON object: old local name to new name"),
                              comment=st("one-line description"), types=st("JSON object: old type to existing header type"),
                              reuse=bo("use the suggested verified pattern"))),
        "tidy_file": ("Remove duplicate includes/extern declarations through build verification and rollback; review semantic tidying before tidy_done.",
                      s(path=st("source/header path", True))),
        "make_readable": ("A readable rewrite of a matched function (names, types, a comment); kept only if it still "
                          "matches byte for byte.", s(function=st("function", True), source=st("readable C", True))),
        "share_knowledge": ("Propose evidence-backed reusable discoveries now; do not wait for a second model before proposing. Reusable matching tips for the game/compiler across functions. NOT function-specific notes: use release_function(notes=...) or add_note(function=...). "
                            "Back independently verified tips with supports=N. The user can accept one proposal; automatic acceptance needs two models. "
                            "Compiler identity/profile changes use compiler_map, try_compiler/search_flags or suggest.",
                            s(evidence=st("Concrete function examples and scores; keep text as the reusable rule. Evidence is not put in every agent prompt."), text=st("reusable matching technique, not a function-specific note"), supports=it("a proposal's number to back"))),
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
        "standins_for": ("Games built with link-time code generation: a starting harness for a unit (stand-ins for what "
                         "its functions use elsewhere, a HarnessEntry calling each function as the original's call sites "
                         "do, declarations), to write into harness/ with edit_file.",
                         s(what=st("unit, source file or function", True))),
        "join_functions": ("Forks: two functions are one the game's analysis cut (a piece with no prologue using registers "
                           "the code before set, an epilogue alone, a switch's cases in the next symbol); joined once "
                           "agents of two models agree and the game still builds byte for byte.",
                           s(function=st("function", True), with_function=st("the other part", True),
                             reason=st("what in the code shows it", True))),
        "split_file": ("Divide a source file at a function: it and the rest of the file's code (and data from the given "
                       "offsets) go to a new file, with the file's compiler (DS: then try_compiler can give the new "
                       "file its own). Kept only if the game still builds identically.",
                       s(path=st("file", True), at=st("function name or .text offset", True),
                         new_name=st("new file path (optional)"), data=st("e.g. .rodata:0x88 .data:0x28 (optional)"))),
    }
    if fork:
        defs["compile_and_diff"] = ("Write one complete function definition into its source file, build it and diff "
                                    "with objdiff. 100% / score 0 = matched.",
                                    s(function=st("function name", True), source=st("the function definition", True)))
    names = set(tools.tool_names(fork) if names is None else names)
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}}
            for n, (d, p) in defs.items() if n in names]
