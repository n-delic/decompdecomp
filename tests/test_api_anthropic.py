"""The API helper's native Anthropic route (agents/api_anthropic.py): Anthropic's OpenAI-compatible endpoint has no
prompt caching and ignores the effort setting, so an endpoint at api.anthropic.com speaks the Messages API, translated
at the edge from and to the chat-completions conversation the helper's loop keeps."""

import json
import threading

import httpx

from decompnet.agents import api
from decompnet.agents import api_anthropic as native

TOOLS = [{"type": "function", "function": {"name": "claim_function", "description": "Claim one.",
                                           "parameters": {"type": "object", "properties": {"function": {"type": "string"}}}}}]


def chat(messages, model="claude-opus-5-5", **kw):
    return {"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto"} | kw


def test_an_anthropic_address_is_native_whatever_its_path():
    assert native.is_native({"provider": "anthropic"})
    assert native.is_native({"provider": "custom", "base_url": "https://api.anthropic.com/v1/messages"})
    assert not native.is_native({"provider": "openai", "base_url": "https://api.openai.com/v1"})
    assert native.base_url("https://api.anthropic.com/v1/messages/") == "https://api.anthropic.com/v1"
    assert native.base_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1"


def test_a_request_caches_its_prefix_and_takes_the_effort_setting():
    body = native.request(chat([{"role": "system", "content": "rules"}, {"role": "user", "content": "go"}],
                               reasoning_effort="minimal"))
    assert body["system"] == [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}]
    assert body["cache_control"] == {"type": "ephemeral"}  # the automatic breakpoint at the conversation's end
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "go"}]}]
    assert body["tools"] == [{"name": "claim_function", "description": "Claim one.", "input_schema": TOOLS[0]["function"]["parameters"]}]
    assert body["tool_choice"] == {"type": "auto"} and body["output_config"] == {"effort": "low"}
    assert body["thinking"] == {"type": "adaptive", "display": "summarized",
                                "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert body["fallbacks"] == "default" and body["max_tokens"] >= native.MAX_TOKENS
    assert "temperature" not in body and "reasoning_effort" not in body
    h = native.headers("sk-ant-x", "claude-opus-5-5")
    assert h["x-api-key"] == "sk-ant-x" and "authorization" not in {k.lower() for k in h}
    assert set(h["anthropic-beta"].split(",")) == {native.BINDING_BETA, native.FALLBACK_BETA}


def test_older_models_get_no_thinking_settings_they_would_refuse():
    for model in ("claude-haiku-4-5-20251001", "claude-sonnet-4-5", "claude-3-7-sonnet-20250219"):
        body = native.request(chat([{"role": "user", "content": "go"}], model=model))
        assert "thinking" not in body and "fallbacks" not in body, model
        assert "anthropic-beta" not in native.headers("k", model)
    body = native.request(chat([{"role": "user", "content": "go"}], model="claude-opus-4-6"))
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}  # adaptive, not bound to the conversation


def answer(*blocks, stop="tool_use"):
    return {"id": "msg_1", "model": "claude-opus-5-5", "stop_reason": stop, "content": list(blocks),
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50, "output_tokens": 7}}


THINK = {"type": "thinking", "thinking": "Easiest first.", "signature": "sig=="}
CALL = {"type": "tool_use", "id": "toolu_1", "name": "claim_function", "input": {"function": "f"}}


def test_thinking_goes_back_verbatim_until_its_turn_is_trimmed():
    out = native.reply(answer(THINK, {"type": "text", "text": "Claiming."}, CALL))
    msg = out["choices"][0]["message"]
    assert msg["content"] == "Claiming." and msg["reasoning_content"] == "Easiest first."
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"function": "f"}
    assert api.api_usage(out["usage"]) == {"input": 10, "cache_read": 900, "cache_write": 50, "output": 7, "turns": 1}
    convo = [{"role": "system", "content": "rules"}, {"role": "user", "content": "go"}, msg,
             {"role": "tool", "tool_call_id": "toolu_1", "content": "claimed f"}]
    sent = native.request(chat(convo))["messages"]
    assert sent[1] == {"role": "assistant", "content": [THINK, {"type": "text", "text": "Claiming."}, CALL]}
    assert sent[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "claimed f"}]}
    msg["content"] = "Clai[... trimmed]"  # fit() shortened an old turn: its thinking no longer fits it
    sent = native.request(chat(convo))["messages"]
    assert [b["type"] for b in sent[1]["content"]] == ["text", "tool_use"]


def test_tool_results_pair_with_their_calls():
    calls = [{"id": i, "type": "function", "function": {"name": "claim_function", "arguments": "{}"}} for i in ("a", "b")]
    convo = [{"role": "user", "content": "go"}, {"role": "assistant", "content": None, "tool_calls": calls},
             {"role": "tool", "tool_call_id": "a", "content": "error: no such function"},
             {"role": "user", "content": "Resume after interruption."},
             {"role": "tool", "tool_call_id": "zzz", "content": "orphan"}]
    sent = native.request(chat(convo))["messages"]
    assert len(sent) == 3 and sent[2]["role"] == "user"
    blocks = sent[2]["content"]
    assert [(b["type"], b.get("tool_use_id")) for b in blocks] == [("tool_result", "a"), ("tool_result", "b"), ("text", None)]
    assert blocks[0]["is_error"] and blocks[1]["is_error"] and "interrupted" in blocks[1]["content"]


def test_a_compaction_without_tools_reads_calls_as_text():
    calls = [{"id": "a", "type": "function", "function": {"name": "claim_function", "arguments": "{}"}}]
    convo = [{"role": "system", "content": "rules"}, {"role": "user", "content": "go"},
             {"role": "assistant", "content": None, "tool_calls": calls}, {"role": "tool", "tool_call_id": "a", "content": "ok"},
             {"role": "user", "content": "Compact the focused work."}]
    body = native.request({"model": "claude-opus-5-5", "messages": convo, "max_tokens": 1600})
    assert "tools" not in body
    kinds = {b["type"] for m in body["messages"] for b in m["content"]}
    assert kinds == {"text"}


def test_a_refusal_says_so():
    msg = native.reply(answer(stop="refusal") | {"stop_details": {"category": "cyber", "explanation": None}})["choices"][0]["message"]
    assert "declined" in msg["content"] and "cyber" in msg["content"] and "tool_calls" not in msg


def test_the_messages_endpoint_is_called_and_a_refused_thinking_block_is_dropped():
    seen = []

    def handler(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        seen.append((str(r.url), dict(r.headers), body))
        if any(b.get("type") == "thinking" for m in body["messages"] for b in m["content"]):
            return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message":
                "messages.1.content.0: Invalid `signature` in `thinking` block. The block is bound to a different conversation."}})
        return httpx.Response(200, json=answer({"type": "text", "text": "Done."}, stop="end_turn"))

    old = native.reply(answer(THINK, CALL))["choices"][0]["message"]
    convo = [{"role": "user", "content": "go"}, old, {"role": "tool", "tool_call_id": "toolu_1", "content": "ok"}]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r = api._complete(client, "https://api.anthropic.com/v1", native.headers("sk-ant-x", "claude-opus-5-5"),
                          chat(convo), threading.Event(), native=True)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "Done."
    assert [u for u, _, _ in seen] == ["https://api.anthropic.com/v1/messages"] * 2
    assert seen[0][1]["x-api-key"] == "sk-ant-x" and seen[0][1]["anthropic-version"] == native.VERSION
    assert [b["type"] for b in seen[1][2]["messages"][1]["content"]] == ["tool_use"]


def test_the_estimate_counts_a_reply_once():
    msg = native.reply(answer({"type": "text", "text": "x" * 3000}, stop="end_turn"))["choices"][0]["message"]
    assert api._tokens(msg) < api._tokens({"role": "assistant", "content": "x" * 3000}) + 50


def test_the_helper_loop_runs_through_the_messages_api(tmp_path, monkeypatch):
    """The whole loop with no compiler: each step goes to /v1/messages, the model's thinking goes back with the calls it
    made, the effort setting is sent, and cache reads are counted."""
    from decompnet.app import agents, tools
    from tests.test_agents import _game
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, "http://127.0.0.1:9/mcp")
    monkeypatch.setattr(manager, "prepare_session", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "prompt", lambda s: "match a function")
    monkeypatch.setattr(manager, "app_instructions", lambda s: "rules")
    s = agents.Session("api-a", "api", "Anthropic #1", "game", "api-a", {
        "provider": "custom", "base_url": "https://api.anthropic.com/v1/messages", "model": "claude-opus-5-5",
        "api_key": "sk-ant-test", "effort": "xhigh", "max_steps": 3})
    agent, _ = net.local_agent(s.agent_name)
    seen = []

    def provider(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        seen.append((r.url.path, r.headers.get("x-api-key"), body))
        n = len(seen)
        blocks = [{"type": "thinking", "thinking": f"step {n}", "signature": f"sig{n}"},
                  {"type": "tool_use", "id": f"toolu_{n}", "name": "project_info", "input": {}}]
        return httpx.Response(200, json={"id": f"msg_{n}", "model": "claude-opus-5-5", "stop_reason": "tool_use",
                                         "content": blocks, "usage": {"input_tokens": 3, "cache_read_input_tokens": 1000,
                                                                      "cache_creation_input_tokens": 20, "output_tokens": 9}})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(provider), **kw))
    monkeypatch.setattr(tools, "call", lambda *args: "measured facts")
    assert api.API.run(manager, s, agent)
    assert [(path, key) for path, key, _ in seen] == [("/v1/messages", "sk-ant-test")] * 3
    last = seen[-1][2]
    assert last["output_config"] == {"effort": "xhigh"} and last["system"][0]["text"] == "rules"
    turns = last["messages"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant", "user"]
    assert turns[1]["content"][0] == {"type": "thinking", "thinking": "step 1", "signature": "sig1"}
    assert turns[4]["content"] == [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "measured facts"}]
    assert s.usage["cache_read"] == 3000 and s.usage["cache_write"] == 60 and s.usage["input"] == 9
