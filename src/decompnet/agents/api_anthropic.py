"""The API helper's native Anthropic route: the Messages API (/v1/messages) for an endpoint at api.anthropic.com.

Anthropic's OpenAI-compatible endpoint has no prompt caching, ignores `reasoning_effort` and never returns the model's
thinking, so a long helper session paid full price for its whole conversation every turn. The helper's loop
(agents/api.py) keeps its conversation in the OpenAI shape every other provider uses (folding, trimming and saved
conversations work on it unchanged), and this module translates at the edge: `request` turns a chat-completions request
into a Messages request, `reply` turns the answer back into a chat-completions reply.

What the native route adds:
- prompt caching: a breakpoint after the tools and system prompt (the same for the whole session) and the automatic
  one at the end of the conversation, so each turn reads the turns before it from the cache;
- the app's effort setting, as `output_config.effort`;
- the model's thinking, summarized for the helper's feed, and passed back verbatim with the calls it made (`NATIVE`
  holds the reply's own blocks), as the API asks for between tool calls;
- for the Claude 5 models, whose thinking is bound to the conversation that produced it: folding and trimming edit
  earlier turns, so the request asks the API to drop the thinking blocks that no longer fit (`drop_block`) instead of
  refusing it; a block refused anyway is stripped and the request sent once more (`strip_thinking`);
- server-side fallbacks for the models that can decline a request (`fallbacks: "default"`).
"""

from __future__ import annotations

import json
import re

HOST = "api.anthropic.com"
VERSION = "2023-06-01"
MAX_TOKENS = 16000  # room for thinking and a reply without streaming (non-streamed requests longer than this time out)
NATIVE = "anthropic_content"  # an assistant message's own content blocks, as the API sent them
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_ALIASES = {"minimal": "low"}
BINDING_BETA = "thinking-binding-controls-2026-08-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models without adaptive thinking (Claude 3, and 4.0-4.5 by name or dated snapshot): they get no thinking settings.
NO_ADAPTIVE = re.compile(r"^claude-(?:3|instant)|-4(?:-[0-5])?(?:-\d{8})?$")
# Claude 5 models, whose thinking blocks carry the conversation that produced them (preserved thinking).
BOUND = re.compile(r"^claude-(?:fable|mythos|(?:opus|sonnet|haiku)-5)")
# Models whose safety classifiers may decline a request, which a server-side fallback then finishes.
FALLBACKS = re.compile(r"^claude-(?:fable-5-[12]|opus-5(?:-5)?|sonnet-5-5)$")
INVALID_SIGNATURE = re.compile(r"invalid\W+signature\W+in\W+.?thinking", re.I)


def is_native(options: dict) -> bool:
    """An endpoint on Anthropic's own API: its preset, or an address at api.anthropic.com (a Custom endpoint someone
    pointed at https://api.anthropic.com/v1/messages, which the OpenAI route turned into a 404)."""
    base = str(options.get("base_url") or "")
    return options.get("provider") == "anthropic" or re.sub(r"^\w+://", "", base).split("/", 1)[0].lower() == HOST


def base_url(base: str) -> str:
    """The API's root (…/v1), whether the address was typed with the endpoint's path or without."""
    return re.sub(r"/(?:messages|chat/completions)/*$", "", base.rstrip("/"))


def headers(api_key: str, model: str) -> dict:
    betas = [beta for beta, pattern in ((BINDING_BETA, BOUND), (FALLBACK_BETA, FALLBACKS)) if pattern.search(model)]
    out = {"Content-Type": "application/json", "anthropic-version": VERSION}
    if api_key:
        out["x-api-key"] = api_key
    if betas:
        out["anthropic-beta"] = ",".join(betas)
    return out


def models_request(entry: dict) -> tuple[str, dict]:
    """Where and how to list an Anthropic endpoint's models for the launcher's Model choice: /v1/models takes the
    API's own headers, not OpenAI's Bearer token (which it refuses, so the launcher offered only the default), and the
    address without a typed /messages. `limit`: its default page is 20 models."""
    return base_url(str(entry.get("base_url") or "")) + "/models?limit=1000", headers(entry.get("api_key") or "", "")


def request(chat: dict) -> dict:
    """A chat-completions request (model, messages, tools, tool_choice, reasoning_effort, max_tokens) as a Messages
    request."""
    model = str(chat.get("model") or "")
    tools = [_tool(t) for t in chat.get("tools") or [] if isinstance(t, dict) and t.get("function")]
    system, messages = _messages(chat.get("messages") or [], with_tools=bool(tools))
    body = {"model": model, "max_tokens": max(int(chat.get("max_tokens") or 0), MAX_TOKENS), "messages": messages,
            "cache_control": {"type": "ephemeral"}}  # the automatic breakpoint, at the end of the conversation
    if system:
        # the second breakpoint: tools and system prompt stay the same all session, so they cache even when an
        # earlier turn was trimmed
        body["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if tools:
        body["tools"] = tools
        body["tool_choice"] = {"type": "auto"}
    if not NO_ADAPTIVE.search(model):
        thinking = {"type": "adaptive", "display": "summarized"}
        if BOUND.search(model):
            thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
        body["thinking"] = thinking
    effort = EFFORT_ALIASES.get(chat.get("reasoning_effort"), chat.get("reasoning_effort"))
    if effort in EFFORTS:
        body["output_config"] = {"effort": effort}
    if FALLBACKS.search(model):
        body["fallbacks"] = "default"
    return body


def strip_thinking(body: dict) -> dict:
    """The request without the thinking blocks it replays: the one-time recovery from a block the API refused
    (bound to a different conversation)."""
    out = dict(body)
    out["messages"] = []
    for m in body.get("messages") or []:
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            kept = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
            if not kept:
                continue
            m = m | {"content": kept}
        out["messages"].append(m)
    return out


def refused_thinking(text: str) -> bool:
    return bool(INVALID_SIGNATURE.search(text or ""))


def reply(body: dict) -> dict:
    """A Messages answer as a chat-completions reply: its text, its tool calls, its thinking (for the feed) and its own
    blocks (`NATIVE`, sent back as they came), and its usage as the API reports it (api.api_usage reads both)."""
    blocks = [b for b in body.get("content") or [] if isinstance(b, dict)]
    text = "\n".join(b.get("text") or "" for b in blocks if b.get("type") == "text").strip()
    calls = [{"id": b.get("id"), "type": "function",
              "function": {"name": b.get("name"), "arguments": json.dumps(b.get("input") or {})}}
             for b in blocks if b.get("type") == "tool_use"]
    thinking = "\n".join(b.get("thinking") or "" for b in blocks if b.get("type") == "thinking").strip()
    if body.get("stop_reason") == "refusal":
        details = body.get("stop_details") or {}
        why = details.get("explanation") or details.get("category") or "no reason given"
        text = (text + "\n" if text else "") + f"(The model declined this request: {why}.)"
    msg = {"role": "assistant", "content": text or None, NATIVE: blocks}
    if calls:
        msg["tool_calls"] = calls
    if thinking:
        msg["reasoning_content"] = thinking
    return {"id": body.get("id"), "model": body.get("model"), "usage": body.get("usage") or {},
            "choices": [{"index": 0, "message": msg, "finish_reason": body.get("stop_reason")}]}


def usage(u: dict) -> dict:
    """The Messages API's usage as `usage` items count it: input excludes what was read from or written to the cache."""
    def n(key):
        v = u.get(key)
        return v if isinstance(v, int) else 0
    return {"input": n("input_tokens"), "cache_read": n("cache_read_input_tokens"),
            "cache_write": n("cache_creation_input_tokens"), "output": n("output_tokens"), "turns": 1}


def _tool(t: dict) -> dict:
    fn = t["function"]
    schema = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
    out = {"name": fn.get("name"), "input_schema": {"type": "object", "properties": {}} | schema}
    if fn.get("description"):
        out["description"] = fn["description"]
    return out


def _arguments(call: dict) -> dict:
    try:
        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def _same(native: list, text: str, calls: list) -> bool:
    """The assistant message still says what the API sent (fit() trims old messages' text): its blocks can go back
    verbatim, thinking and all."""
    said = "\n".join(b.get("text") or "" for b in native if b.get("type") == "text").strip()
    uses = [(b.get("id"), b.get("name"), b.get("input") or {}) for b in native if b.get("type") == "tool_use"]
    return said == (text or "").strip() and uses == [(c.get("id"), (c.get("function") or {}).get("name"), _arguments(c))
                                                       for c in calls]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text") or "" for p in content if isinstance(p, dict))
    return ""


def _messages(chat: list[dict], with_tools: bool) -> tuple[str, list[dict]]:
    """The system prompt and the turns. Consecutive tool results (and what the user adds after them) are one user
    turn; a tool call with no result (an interrupted session, restored) gets one saying so, and a result whose call is
    gone is dropped, as the API requires. Without tools in the request (a compaction), calls and results are text."""
    system, out = [], []

    def add(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in chat:
        role = m.get("role")
        if role in ("system", "developer"):
            if not out:
                system.append(_text(m.get("content")))
            else:
                add("user", [{"type": "text", "text": _text(m.get("content"))}])
        elif role == "assistant":
            text, calls = _text(m.get("content")), m.get("tool_calls") or []
            native = m.get(NATIVE)
            if with_tools and isinstance(native, list) and _same(native, text, calls):
                add("assistant", [dict(b) for b in native])
                continue
            blocks = [{"type": "text", "text": text}] if text.strip() else []
            for c in calls:
                name = (c.get("function") or {}).get("name") or ""
                if with_tools:
                    blocks.append({"type": "tool_use", "id": c.get("id"), "name": name, "input": _arguments(c)})
                else:
                    blocks.append({"type": "text", "text": f"[called {name}({json.dumps(_arguments(c))})]"})
            add("assistant", blocks)
        elif role == "tool":
            result = _text(m.get("content")) or "(no output)"
            if with_tools:
                block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": result}
                if result.startswith("error:"):
                    block["is_error"] = True
                add("user", [block])
            else:
                add("user", [{"type": "text", "text": f"[result: {result}]"}])
        else:
            text = _text(m.get("content"))
            if text.strip():
                add("user", [{"type": "text", "text": text}])
    if with_tools:
        _pair(out)
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return "\n".join(s for s in system if s), out


def _pair(turns: list[dict]) -> None:
    """Every tool call answered in the next turn, results first; no result without its call."""
    i = 0
    while i < len(turns):
        t = turns[i]
        if t["role"] == "assistant":
            ids = [b.get("id") for b in t["content"] if b.get("type") == "tool_use"]
            nxt = turns[i + 1] if i + 1 < len(turns) and turns[i + 1]["role"] == "user" else None
            if ids and nxt is None:
                nxt = {"role": "user", "content": []}
                turns.insert(i + 1, nxt)
            if nxt is not None:
                results = [b for b in nxt["content"] if b.get("type") == "tool_result" and b.get("tool_use_id") in ids]
                rest = [b for b in nxt["content"] if b.get("type") != "tool_result"]
                have = {b.get("tool_use_id") for b in results}
                missing = [{"type": "tool_result", "tool_use_id": x, "is_error": True,
                            "content": "(no result: the session was interrupted before this call finished)"}
                           for x in ids if x not in have]
                nxt["content"] = results + missing + rest
                if not nxt["content"]:
                    turns.pop(i + 1)
        elif i == 0 or turns[i - 1]["role"] != "assistant":
            t["content"] = [b for b in t["content"] if b.get("type") != "tool_result"]
        i += 1
    turns[:] = [t for t in turns if t["content"]]
