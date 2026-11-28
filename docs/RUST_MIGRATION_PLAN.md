# Rust Migration Plan

The long-term goal of **decompdecomp** is to replace most of the Python logic with standalone Rust processes.

Today, the main server is a single Python 3.12 process. Because all threads in that process share one Global Interpreter Lock (GIL), CPU-bound work (ROM scanning, disassembly, boundary analysis, heavy SQLite/JSON serialization) stalls the `asyncio` event loop and UI HTTP workers. Moving logic into Rust processes eliminates GIL contention, enables true multi-core parallelism, and reduces memory overhead.

## Guiding Principles

1. **Incremental, process-boundary migration:** Keep the existing `gateway/` Cargo workspace (`decomp-mcp-gateway`) as the home for Rust binaries/subcommands built during `uv sync`, and communicate over stdin/stdout JSON or local HTTP/Unix sockets.
2. **Parity and fallback:** Every migrated component should have parity tests against the Python implementation and (during transition) a clean Python fallback when `DECOMP_BUILD_GATEWAY=0` is set.
3. **Move the hottest GIL/CPU bottlenecks first:** Prioritize pure, self-contained, CPU-heavy or high-frequency paths before touching orchestration or UI state.

## Phased Plan

### Phase 1: Gateway & Read-Only Offloading (Done)
- **MCP Gateway (`gateway/`):** Route MCP tool calls with strict deadlines (`decomp-mcp-gateway`).
- **Read-only UI endpoints (`--window-reads`):** Serve high-frequency read-only routes (`/api/stats`, `/api/knowledge`, `/api/attempt-history`, `/api/projects?brief=1`) through the gateway rather than the main server's event loop.

### Phase 2: Pure CPU-Bound Analysis Workers (Done)
Move pure, stateless byte/instruction analysis out of the main Python process:
- **Boundary & entry fixes (`entry_fixes` / `_pointed_at`) (Done):** Single-pass ROM word scanning and MIPS boundary/epilogue rules in Rust (`decomp-mcp-gateway entry-fixes`), eliminating multi-minute GIL stalls during ROM import and `Check again`.
- **Disassembly, scoring & fingerprinting (`scoring.py`, `twins.py`) (Done):** Offloaded twin fingerprinting (`decomp-mcp-gateway twins-prints`) and instruction-level diffing/scoring (`decomp-mcp-gateway scoring-diff`) to Rust with Python fallbacks and parity tests.
- **Post-`splat` ROM boundary & reference analysis (`rom/automatic_boundaries.py`) (Done):** Offloaded full-ROM pointer scanning and instruction transfer/reference extraction (`decomp-mcp-gateway automatic-boundaries`) to Rust with Python fallback and parity tests.

### Phase 3: High-Frequency State, Claims & Judge
- **SQLite state & lease management:** Move hot database paths (claim/lease expiration, `summary_batch`, `/api/live`, `/api/events`) into a Rust service owning SQLite connections directly (using `rusqlite` / `sqlx`), removing Python JSON serialization and `json_each` overhead.
- **Linking & judging (`rom/match.py`, `forks/project.py`):** Move ELF/COFF parsing, relocation/linking checks, and `objdiff` orchestration into Rust so candidate evaluation scales across cores without spawning Python workers.

### Phase 4: Orchestration & Native Shell
- **Autopilot & permuter coordination:** Move the permuter/deep-permuter search loop and autopilot scheduling into Rust, leaving only external compiler/agent CLI invocations as subprocesses.
- **Desktop host & web server:** Serve the web UI and manage the native window/tray directly from Rust, retiring the Python server process.
