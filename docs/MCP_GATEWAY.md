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

- **A repeat never runs twice.** A call with the same helper token, tool and arguments (key order ignored; the rest
  of `params`, such as the new `_meta.progressToken` clients send with every request, doesn't count) as one
  still running, or finished but not yet delivered, waits for that run. Once delivered, the same call again is a
  new run. Undelivered results are kept 15 minutes.
- **Waiting is free.** Helpers pay for answers, not time: the gateway holds a call open up to its deadline, and
  retries connections that never reached the app (the app restarting) instead of failing at once.
- **Bounded everywhere.** Every wait has a deadline, no lock is held across an await, a helper may have 16 calls
  running and the gateway 256. The deadlines sit under every client's own limit (Claude Code's MCP SDK: 60 s;
  Codex and Kimi: 180 s, set by the app).

## Tools the gateway answers itself

The first step towards serving the tools without the Python app: `gateway/src/native.rs` answers `get_claims`,
`list_functions` and the compact `project_info` straight from the SQLite databases, read-only, with exactly the
reply the app's handlers give (the same SQL, Python's number formatting and `json.dumps`, the same claim tokens).
`tests/test_gateway_native.py` compares the two byte for byte. On a copy of a real project they agreed on all 23
calls tried, and the gateway answered in about 1 ms.

- The app sends what only it knows over the gateway's standard input every second, and at once when a handshake
  or investigation starts (`app/tool_reader.py` routes):
  - which project each helper works;
  - the helpers with app-only state;
  - the projects it is rechecking or sharing;
  - each active project's database, and the saved parts of its project_info.
- Each call it answers goes back to the app on standard output, so the app keeps the transcript, session budget,
  tool usage and activity (`apply_report`). It doesn't update the helper's advisory last-seen time.
- Whatever it isn't sure of goes on to the reader and then the app:
  - a helper or project the app hasn't described, or routes older than 5 s;
  - fork projects;
  - `project_info(detail=true)`;
  - a missing row, or any database error.
- Claims, releases, notes and everything that compiles stay with the app. They move once projects have their slim
  hosted format, with the gateway as the only thing that claims.

## Read-only tools in their own process

`src/decompnet/app/tool_reader.py` runs a second, small Python process (about 90 MB) that opens the same databases
and answers a few read-only tools with the app's own handlers (`READER_TOOLS`: get_claims, list_functions,
project_info, get_function's database sections, ...). It has its own interpreter lock, so these calls don't wait
for the app's saves, sweeps and other helpers' compiles, and they answer while the app is stalled. On a copy of a
real project, with the app's process kept busy, get_function / get_claims / list_functions took 17 ms (median)
from the reader and 357 ms in the app, and its answers to 155 real calls were identical to the app's.

- The gateway sends those tools there first (`--reader`, `--read-tools`) and to the app when the reader declines,
  isn't running, fails or takes over 20 s. A read changes nothing, so asking the app after is safe.
- It answers only as the app would, and declines otherwise:
  - tools whose output comes from what the app keeps in memory (last diffs, search evidence, the helper's
    selected candidate, compile warnings);
  - helpers with app-only state: a pending connection handshake, an asset or boundary investigation, a used-up
    session budget;
  - projects the app is rechecking or sharing in a room;
  - any unexpected error.
- The app sends it that state every second, and at once when a handshake or investigation starts.
- It never synchronizes saved progress folders. It reads what the app has put in the database, and drops its own
  symbol caches when the database's revisions say the app renamed, split or imported something.
- Its databases open with `store.ATTACH_ONLY`: no schema upgrade, checkpoint or ANALYZE, and no writer gate. It
  leaves the app's leases, project moves and repair analyses alone.
- For each call it serves it reports back, and the app keeps the transcript, session budget, tool usage and
  project activity as for its own calls.
- Its log is `.decomp/logs/tool-reader.log`. It starts in the background with the app and again if it ends.
  `DECOMP_TOOL_READER=0` turns it off; it never runs without the gateway.

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

## Building, installing and turning it off

```sh
cd gateway && cargo build --release   # a checkout finds gateway/target/release/decomp-mcp-gateway
uv run pytest -q tests/test_mcp_gateway.py tests/test_mcp_gateway_install.py tests/test_tool_reader.py tests/test_gateway_native.py && (cd gateway && cargo test)
```

- Every package carries it: the macOS app, the Linux .deb/.rpm/AppImage and the Windows setup and portable ZIP run
  `packaging/build_gateway.py` before building the wheel, which puts it in `decompnet/bin/`, and their smoke tests
  fail without it (`DECOMP_BUILD_GATEWAY=0` packages without it). Linux builds link it statically with musl,
  Windows with a static C runtime.
- Every release also carries `decomp-mcp-gateway-<platform>` and its `.sha256` (macOS arm64 and x86_64, Linux
  x86_64, Windows x86_64). An install from source (`site/public/install.sh`, `uv tool install`) has no Rust build:
  `decomp setup`, which the installer and every update run, downloads the one for this computer and checks it
  against its checksum. It tries this version's release, then the latest.
- The app uses the newest of the package's copy and a checkout's cargo build, else the downloaded one.
- Without any of them, or with `DECOMP_MCP_GATEWAY=0`, helpers connect to the app's `/mcp` as before;
  `DECOMP_MCP_GATEWAY=<path>` names another build. The test suite turns it off (`tests/conftest.py`) except in
  `tests/test_mcp_gateway.py`. The Linux CI job builds it, so those tests run there.
