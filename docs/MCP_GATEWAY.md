# MCP gateway

Helpers reach decompdecomp's tools through a small Rust program, `gateway/` (`decomp-mcp-gateway`), started and
supervised by `src/decompnet/app/mcp_gateway.py`. It exists so that a busy or stalled app never shows up as
`MCP error -32001: request timed out`: that error made helpers retry and spend tokens while nothing was wrong
with their work.

## What it promises

| Request | Answered within | When the app doesn't answer in time |
|---|---:|---|
| `tools/call` | 45 s | "*tool* is still running … repeat exactly the same call". The call keeps running in the app. |
| `initialize`, `tools/list` | 15 s | The last good answer for that helper (or a minimal handshake on first contact). |
| `ping` | at once | Answered by the gateway. |
| notifications | at once | 202; the app keeps no MCP session behind the gateway, so there is nothing to deliver. |

- **A repeat never runs twice.** A call with the same helper token, tool and arguments (key order ignored) as one
  still running, or finished but not yet delivered, waits for that run. Once delivered, the same call again is a
  new run. Undelivered results are kept 15 minutes.
- **Waiting is free.** Helpers pay for answers, not time: the gateway holds a call open up to its deadline, and
  retries connections that never reached the app (the app restarting) instead of failing at once.
- **Bounded everywhere.** Every wait has a deadline, no lock is held across an await, a helper may have 16 calls
  running and the gateway 256. The deadlines sit under every client's own limit (Claude Code's MCP SDK: 60 s;
  Codex and Kimi: 180 s, set by the app).

## How it fits

- The app picks the gateway's port once and keeps it in `.decomp/mcp-gateway.port`, so helpers connected by hand
  keep a working address. Helper settings get the gateway's address (`server.build(mcp_url=…)`).
- Behind the gateway the app's `/mcp` runs stateless with JSON replies (no tool uses an MCP session), so the app
  and the gateway can each restart without breaking the other.
- The app holds the gateway's standard input; when the app ends for any reason the gateway sees end-of-file and
  ends too. If the gateway ends while the app runs, the app starts it again on the same port.
- Its log is `.decomp/logs/mcp-gateway.log`; `GET /health` on the gateway's port reports counters.
- Inside the app, tool calls have thread lanes of their own (`tools.QUICK_TOOLS` and the rest), apart from the
  window's, so a burst of compiles can't make `guide` or `get_claims` wait.

## Building and turning it off

```sh
cd gateway && cargo build --release   # a checkout finds gateway/target/release/decomp-mcp-gateway
uv run pytest -q tests/test_mcp_gateway.py && (cd gateway && cargo test)
```

A packaged install looks for `src/decompnet/bin/decomp-mcp-gateway[.exe]`. Without the program, or with
`DECOMP_MCP_GATEWAY=0`, helpers connect to the app's `/mcp` as before; `DECOMP_MCP_GATEWAY=<path>` names another
build. The test suite turns it off (`tests/conftest.py`) except in `tests/test_mcp_gateway.py`.

Not yet: prebuilt binaries for each platform in releases (until then installs fall back to the app's own endpoint),
and answering read-only tools from SQLite in the gateway itself while the app is stalled.
