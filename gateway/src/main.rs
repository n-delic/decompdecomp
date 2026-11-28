//! decomp-mcp-gateway: the agents' MCP endpoint, in its own small process that always answers.
//!
//! The decompdecomp app serves its tools from a busy Python process. When that process stalls (a long database
//! read, the interpreter lock held by background work) every tool call used to wait until the agent's client gave
//! up with "MCP error -32001: request timed out", and the agent spent tokens retrying. This process sits between the
//! agents and the app and keeps three promises:
//!
//! 1. Every request gets an answer within a deadline. `initialize` and `tools/list` fall back to the last good
//!    answer for that agent, else for its project, else the latest one (kept in `--state` across restarts, so a
//!    helper starting while the app is busy or restarting gets its tools at once); a tool call that is still
//!    running answers "still running, repeat the same call".
//! 2. A repeated call never runs twice. A call with the same agent, tool and arguments as one still running (or
//!    finished but not yet delivered) waits for that one instead of starting another, so "repeat the call" is safe.
//! 3. Waiting is free. The agent pays for responses, not for time: while the app is slow or restarting, the gateway
//!    holds the call open (up to the deadline) and retries connections that never reached the app.
//!
//! The app runs its MCP server stateless behind this gateway, so the gateway keeps no MCP sessions either. It never
//! holds a lock across an await, bounds every wait, and exits when the app closes its standard input.

mod hosted;
mod native;
mod rom_judge;
mod desktop_judge;
mod entry_fixes;
mod elf_extract;
mod scoring;
mod twins_prints;
mod automatic_boundaries;
mod mips_link;
mod mips_elf;
mod mips_ecoff;
mod mips_dispatch;
mod mips_diff;
#[path = "../../native/matching_blocks.rs"]
mod matching_blocks;
mod mips_rom;
mod mips_uso;
mod mips_batch;
mod rom_project;
mod verifier;
mod window;
mod window_db;

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::io::AsyncBufReadExt;
use tokio::sync::{watch, OwnedSemaphorePermit, Semaphore};

const PROTOCOL_FALLBACK: &str = "2025-06-18";
/// The reader's answer when it can't answer a call exactly as the app would (src/decompnet/app/tool_reader.py).
const DECLINED: &str = "decompdecomp-reader: declined";

#[derive(Clone, Debug)]
struct Config {
    listen: String,
    upstream: String,
    /// How long a tool call may take before the agent hears "still running" (the call keeps running).
    call_deadline: Duration,
    /// How long `initialize`, `tools/list` and other requests wait before the last good answer is used.
    meta_deadline: Duration,
    /// The longest a tool call may run in the app at all.
    upstream_limit: Duration,
    /// How long a finished, undelivered result is kept for the agent's repeat.
    keep_results: Duration,
    max_body: usize,
    per_agent: usize,
    total: usize,
    /// The app's read-only tool process: `read_tools` go there first, and to the app when it declines.
    reader: Option<String>,
    read_tools: HashSet<String>,
    /// How long the reader may take before the call goes to the app instead.
    reader_limit: Duration,
    /// A file that keeps each project's last `initialize` / `tools/list` answers across restarts.
    state: Option<String>,
    /// The app window's address (window.rs): `--window-listen`, the app's base address, the reader's, the GETs the
    /// reader may answer, and how long it may take.
    window_listen: Option<String>,
    window_upstream: Option<String>,
    window_reader: Option<String>,
    window_reads: Vec<window::Read>,
    window_reader_limit: Duration,
    window_db: Option<String>,
    /// Tools the agent may leave behind (release_function): after `ack_deadline` the agent hears "accepted" and
    /// moves on while the call keeps running in the app. Kept in `journal` until the app answers, so a restart
    /// loses none; the agent's later calls to the app wait for it, so they see it done; the app's answer comes
    /// with the agent's next reply.
    ack_tools: HashSet<String>,
    ack_deadline: Duration,
    journal: Option<String>,
}

impl Config {
    fn from_args() -> Result<Config, String> {
        let mut config = Config {
            listen: "127.0.0.1:0".into(),
            upstream: String::new(),
            call_deadline: Duration::from_secs(45),
            meta_deadline: Duration::from_secs(15),
            upstream_limit: Duration::from_secs(3600),
            keep_results: Duration::from_secs(900),
            max_body: 4 * 1024 * 1024,
            per_agent: 16,
            total: 256,
            reader: None,
            read_tools: HashSet::new(),
            reader_limit: Duration::from_secs(20),
            state: None,
            window_listen: None,
            window_upstream: None,
            window_reader: None,
            window_reads: Vec::new(),
            window_reader_limit: Duration::from_secs(10),
            window_db: None,
            ack_tools: HashSet::new(),
            ack_deadline: Duration::from_secs(3),
            journal: None,
        };
        let mut args = std::env::args().skip(1);
        while let Some(flag) = args.next() {
            let mut value = || args.next().ok_or_else(|| format!("{flag} needs a value"));
            let seconds = |text: String| -> Result<Duration, String> {
                text.parse::<f64>()
                    .ok()
                    .filter(|s| *s > 0.0 && s.is_finite())
                    .map(Duration::from_secs_f64)
                    .ok_or_else(|| format!("not a number of seconds: {text}"))
            };
            let count = |text: String| -> Result<usize, String> {
                text.parse::<usize>().ok().filter(|n| *n > 0).ok_or_else(|| format!("not a count: {text}"))
            };
            match flag.as_str() {
                "--listen" => config.listen = value()?,
                "--upstream" => config.upstream = value()?,
                "--call-deadline" => config.call_deadline = seconds(value()?)?,
                "--meta-deadline" => config.meta_deadline = seconds(value()?)?,
                "--upstream-limit" => config.upstream_limit = seconds(value()?)?,
                "--keep-results" => config.keep_results = seconds(value()?)?,
                "--max-body" => config.max_body = count(value()?)?,
                "--per-agent" => config.per_agent = count(value()?)?,
                "--total" => config.total = count(value()?)?,
                "--reader" => config.reader = Some(value()?),
                "--read-tools" => {
                    config.read_tools = value()?.split(',').map(str::trim).filter(|s| !s.is_empty()).map(String::from).collect()
                }
                "--reader-limit" => config.reader_limit = seconds(value()?)?,
                "--state" => config.state = Some(value()?),
                "--window-listen" => config.window_listen = Some(value()?),
                "--window-upstream" => config.window_upstream = Some(value()?),
                "--window-reader" => config.window_reader = Some(value()?),
                "--window-reads" => {
                    config.window_reads = value()?
                        .split(',')
                        .map(str::trim)
                        .filter(|s| !s.is_empty())
                        .map(|s| window::Read::parse(s).ok_or_else(|| format!("not a path: {s}")))
                        .collect::<Result<_, _>>()?
                }
                "--window-reader-limit" => config.window_reader_limit = seconds(value()?)?,
                "--window-db" => config.window_db = Some(value()?),
                "--ack-tools" => {
                    config.ack_tools = value()?.split(',').map(str::trim).filter(|s| !s.is_empty()).map(String::from).collect()
                }
                "--ack-deadline" => config.ack_deadline = seconds(value()?)?,
                "--journal" => config.journal = Some(value()?),
                _ => return Err(format!("unknown option {flag}")),
            }
        }
        if config.upstream.is_empty() {
            return Err("--upstream is required (the app's MCP address)".into());
        }
        if config.window_listen.is_some() && config.window_upstream.is_none() {
            return Err("--window-listen needs --window-upstream (the app's address)".into());
        }
        Ok(config)
    }
}

/// One tool call running in the app. Agents that repeat it wait on `done`.
struct Call {
    started: Instant,
    done: watch::Receiver<Option<Value>>,
    finished_at: Mutex<Option<Instant>>,
    /// The agent was told "accepted" and went on: the reply goes to its next call instead (`notices`).
    acked: Mutex<bool>,
}

/// Calls the agent left behind (release_function), kept on disk until the app answers them.
struct Journal {
    path: String,
    lock: Mutex<()>,
}

impl Journal {
    fn entries(&self) -> Vec<Value> {
        let _held = self.lock.lock().unwrap();
        std::fs::read_to_string(&self.path)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .collect()
    }

    fn add(&self, entry: &Value) {
        let _held = self.lock.lock().unwrap();
        let file = std::fs::OpenOptions::new().create(true).append(true).open(&self.path);
        match file {
            Ok(mut file) => {
                let _ = writeln!(file, "{entry}");
                let _ = file.sync_data();
            }
            Err(error) => log(format!("can't write the journal {}: {error}", self.path)),
        }
    }

    fn remove(&self, id: &str) {
        let _held = self.lock.lock().unwrap();
        let kept: Vec<String> = std::fs::read_to_string(&self.path)
            .unwrap_or_default()
            .lines()
            .filter(|l| serde_json::from_str::<Value>(l).ok().and_then(|v| v.get("id").cloned()) != Some(json!(id)))
            .map(String::from)
            .collect();
        let temporary = format!("{}.tmp", self.path);
        let text = kept.iter().map(|l| format!("{l}\n")).collect::<String>();
        if std::fs::write(&temporary, text).is_ok() {
            let _ = std::fs::rename(&temporary, &self.path);
        }
    }
}

/// The headers a call goes to the app with, kept in the journal so a replay after a restart sends the same.
const FORWARDED: [&str; 4] = ["authorization", "mcp-protocol-version", "user-agent", "x-decomp-connection-check"];

fn saved_headers(headers: &HeaderMap) -> Value {
    let mut map = serde_json::Map::new();
    for name in FORWARDED {
        if let Some(value) = headers.get(name).and_then(|v| v.to_str().ok()) {
            map.insert(name.into(), Value::String(value.into()));
        }
    }
    Value::Object(map)
}

fn restored_headers(saved: &Value) -> HeaderMap {
    let mut headers = HeaderMap::new();
    for name in FORWARDED {
        if let Some(value) = saved.get(name).and_then(Value::as_str).and_then(|v| HeaderValue::from_str(v).ok()) {
            headers.insert(name, value);
        }
    }
    headers
}

/// The first text of a tool reply.
fn reply_text(reply: &Value) -> String {
    reply.pointer("/result/content/0/text").and_then(Value::as_str).map(String::from)
        .or_else(|| reply.pointer("/error/message").and_then(Value::as_str).map(String::from))
        .unwrap_or_default()
}

/// `notes` put before the reply's first text (and its structured copy).
fn with_notes(mut reply: Value, notes: &[String]) -> Value {
    if notes.is_empty() {
        return reply;
    }
    let head = notes.join("\n\n") + "\n\n---\n\n";
    for pointer in ["/result/content/0/text", "/result/structuredContent/result"] {
        if let Some(Value::String(text)) = reply.pointer_mut(pointer) {
            *text = format!("{head}{text}");
        }
    }
    reply
}

struct Gateway {
    config: Config,
    client: reqwest::Client,
    calls: Mutex<HashMap<String, Arc<Call>>>,
    /// The last good `initialize` / `tools/list` result per (agent, method).
    answers: Mutex<HashMap<(String, String), Value>>,
    /// The same per (project, method), and the latest per method, for agents with none of their own yet.
    by_project: Mutex<Remembered>,
    agents: Mutex<HashMap<String, Arc<Semaphore>>>,
    total: Arc<Semaphore>,
    served: AtomicU64,
    late: AtomicU64,
    reachable: AtomicBool,
    /// Calls the reader answered, and calls it passed to the app.
    read: AtomicU64,
    declined: AtomicU64,
    /// Tools answered here, from the databases (native.rs), and how many.
    native: Arc<native::Native>,
    answered: AtomicU64,
    reports: AtomicU64,
    /// The app window's proxy, when it listens (window.rs).
    window: std::sync::OnceLock<Arc<window::Window>>,
    /// Per helper: its calls left behind still running in the app (`ack_tools`), which its later calls to the app
    /// wait for, and the app's answers to them, given with its next reply.
    behind: Mutex<HashMap<String, Vec<(u64, watch::Receiver<Option<Value>>)>>>,
    notices: Mutex<HashMap<String, Vec<String>>>,
    journal: Option<Journal>,
    ids: AtomicU64,
    acknowledged: AtomicU64,
    claims_given: AtomicU64,
    /// Per tool, how long helpers waited for each reply (`/health` "tools").
    latency: Mutex<HashMap<String, Latency>>,
}

/// What helpers waited for one tool's replies: every reply counts, "still running" ones too.
#[derive(Default)]
struct Latency {
    calls: u64,
    total_ms: f64,
    max_ms: f64,
    over_10s: u64,
    still_running: u64,
}

/// Answers kept for new connections: per (project, method), and the latest per method. Saved to `--state`.
#[derive(Default)]
struct Remembered {
    projects: HashMap<String, Value>, // "slug\tmethod" -> result
    latest: HashMap<String, Value>,   // method -> result
}

impl Remembered {
    fn load(path: &Option<String>) -> Remembered {
        let mut kept = Remembered::default();
        let Some(path) = path else { return kept };
        let Ok(text) = std::fs::read_to_string(path) else { return kept };
        let Ok(value) = serde_json::from_str::<Value>(&text) else { return kept };
        for (field, map) in [("projects", &mut kept.projects), ("latest", &mut kept.latest)] {
            if let Some(entries) = value.get(field).and_then(|v| v.as_object()) {
                map.extend(entries.iter().map(|(k, v)| (k.clone(), v.clone())));
            }
        }
        kept
    }

    /// Keep `result` for the project and as the latest; true when anything changed (and should be saved).
    fn keep(&mut self, project: Option<&str>, method: &str, result: &Value) -> bool {
        let mut changed = self.latest.get(method) != Some(result);
        self.latest.insert(method.to_string(), result.clone());
        if let Some(project) = project {
            let key = format!("{project}\t{method}");
            changed |= self.projects.get(&key) != Some(result);
            self.projects.insert(key, result.clone());
        }
        changed
    }

    fn save(&self, path: &Option<String>) {
        let Some(path) = path else { return };
        let value = json!({"projects": self.projects, "latest": self.latest});
        let temporary = format!("{path}.tmp");
        if std::fs::write(&temporary, value.to_string()).is_ok() {
            let _ = std::fs::rename(&temporary, path);
        }
    }
}

fn log(message: impl AsRef<str>) {
    eprintln!("decomp-mcp-gateway: {}", message.as_ref());
}

fn bearer(headers: &HeaderMap) -> String {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string()
}

/// Object keys sorted, so equal arguments give equal keys whatever order the agent wrote them in.
fn canonical(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let inner: Vec<String> = keys
                .into_iter()
                .map(|k| format!("{}:{}", Value::String(k.clone()), canonical(&map[k])))
                .collect();
            format!("{{{}}}", inner.join(","))
        }
        Value::Array(items) => format!("[{}]", items.iter().map(canonical).collect::<Vec<_>>().join(",")),
        other => other.to_string(),
    }
}

/// A call is its helper, tool and arguments. Everything else in `params` is left out: clients put a new
/// `_meta.progressToken` on every request, so with it a repeat never found the call it repeats.
fn call_key(auth: &str, params: &Value) -> String {
    let identity = json!({
        "name": params.get("name").cloned().unwrap_or(Value::Null),
        "arguments": params.get("arguments").cloned().unwrap_or_else(|| json!({})),
    });
    let mut hash = Sha256::new();
    hash.update(auth.as_bytes());
    hash.update([0]);
    hash.update(canonical(&identity).as_bytes());
    hash.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn tool_text(id: &Value, text: String) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "result": {"content": [{"type": "text", "text": text}], "isError": true}})
}

fn rpc_error(id: &Value, code: i64, message: impl Into<String>) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message.into()}})
}

fn with_id(mut message: Value, id: &Value) -> Value {
    if let Value::Object(map) = &mut message {
        map.insert("id".into(), id.clone());
    }
    message
}

/// The JSON-RPC reply for `id` in an app response: plain JSON, or the first matching event of an SSE stream.
fn reply_in(body: &[u8], content_type: &str, id: &Value) -> Option<Value> {
    if !content_type.starts_with("text/event-stream") {
        return serde_json::from_slice::<Value>(body).ok().and_then(|v| match v {
            Value::Array(items) => items.into_iter().find(|m| m.get("id") == Some(id)),
            single => Some(single),
        });
    }
    let text = String::from_utf8_lossy(body);
    let mut data = String::new();
    for line in text.lines().chain(std::iter::once("")) {
        if let Some(rest) = line.strip_prefix("data:") {
            data.push_str(rest.strip_prefix(' ').unwrap_or(rest));
            data.push('\n');
        } else if line.is_empty() && !data.is_empty() {
            if let Ok(message) = serde_json::from_str::<Value>(&data) {
                if message.get("id") == Some(id) && (message.get("result").is_some() || message.get("error").is_some()) {
                    return Some(message);
                }
            }
            data.clear();
        }
    }
    None
}

enum Upstream {
    Reply(Value),
    /// The request never reached the app (connection refused or reset before sending): safe to send again.
    Unreachable(String),
    /// The app took it but failed or answered nonsense: not safe to send again.
    Failed(String),
}

/// The reader's reply says it declined: a tool error whose text is DECLINED.
fn declined(reply: &Value) -> bool {
    let result = reply.get("result");
    result.and_then(|r| r.get("isError")).and_then(Value::as_bool).unwrap_or(false)
        && result.and_then(|r| r.get("content")).and_then(Value::as_array).is_some_and(|items| {
            items.iter().any(|i| i.get("text").and_then(Value::as_str).is_some_and(|t| t.contains(DECLINED)))
        })
}

impl Gateway {
    async fn send(&self, message: &Value, headers: &HeaderMap, limit: Duration) -> Upstream {
        let upstream = self.config.upstream.clone();
        self.send_to(&upstream, message, headers, limit).await
    }

    /// The gateway's own answer (native.rs), or None. The app hears of it on standard output, to keep the
    /// helper's transcript, session budget and tool usage as for its own calls (app/tool_reader.py apply_report).
    async fn from_here(&self, message: &Value, headers: &HeaderMap, tool: &str) -> Option<Value> {
        if !native::TOOLS.contains(&tool) {
            return None;
        }
        let id = message.get("id").cloned().unwrap_or(Value::Null);
        let arguments = message.pointer("/params/arguments").cloned().unwrap_or_else(|| json!({}));
        let (auth, tool_name, native) = (bearer(headers), tool.to_string(), self.native.clone());
        let started = Instant::now();
        let work = tokio::task::spawn_blocking(move || native.answer(&auth, &tool_name, &arguments).map(|s| (s, arguments)));
        let (served, arguments) = tokio::time::timeout(Duration::from_secs(10), work).await.ok()?.ok()??;
        self.answered.fetch_add(1, Ordering::Relaxed);
        self.report(&served, tool, &arguments, started);
        Some(json!({"jsonrpc": "2.0", "id": id, "result": {
            "content": [{"type": "text", "text": served.text}],
            "structuredContent": {"result": served.text},
            "isError": false}}))
    }

    /// The reader's answer to a read-only call, or None: the app answers it (not a reader tool, declined,
    /// unreachable, failed or too slow; a read changes nothing, so asking the app after is safe).
    async fn from_reader(&self, message: &Value, headers: &HeaderMap, tool: &str) -> Option<Value> {
        let reader = self.config.reader.as_ref()?;
        if !self.config.read_tools.contains(tool) {
            return None;
        }
        match self.send_to(reader, message, headers, self.config.reader_limit).await {
            Upstream::Reply(reply) if reply.get("error").is_none() && !declined(&reply) => {
                self.read.fetch_add(1, Ordering::Relaxed);
                Some(reply)
            }
            _ => {
                self.declined.fetch_add(1, Ordering::Relaxed);
                None
            }
        }
    }

    async fn send_to(&self, url: &str, message: &Value, headers: &HeaderMap, limit: Duration) -> Upstream {
        let id = message.get("id").cloned().unwrap_or(Value::Null);
        let to_app = url == self.config.upstream;
        let mut request = self
            .client
            .post(url)
            .timeout(limit)
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::ACCEPT, "application/json, text/event-stream")
            .body(message.to_string());
        for name in FORWARDED {
            if let Some(value) = headers.get(name) {
                request = request.header(name, value.clone());
            }
        }
        let response = match request.send().await {
            Ok(response) => response,
            Err(error) if error.is_connect() => return Upstream::Unreachable(error.to_string()),
            Err(error) => return Upstream::Failed(error.to_string()),
        };
        if to_app {
            self.reachable.store(true, Ordering::Relaxed);
        }
        let status = response.status();
        let content_type = response
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let body = match response.bytes().await {
            Ok(body) => body,
            Err(error) => return Upstream::Failed(error.to_string()),
        };
        match reply_in(&body, &content_type, &id) {
            Some(reply) => Upstream::Reply(reply),
            None => Upstream::Failed(format!(
                "the app answered HTTP {status} without a reply: {}",
                String::from_utf8_lossy(&body[..body.len().min(300)])
            )),
        }
    }

    /// Send until the app answers or `limit` passes, retrying only requests that never reached it.
    async fn send_patiently(&self, message: &Value, headers: &HeaderMap, limit: Duration) -> Upstream {
        let end = Instant::now() + limit;
        let mut pause = Duration::from_millis(200);
        loop {
            let left = end.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Upstream::Unreachable("the app did not accept connections in time".into());
            }
            match self.send(message, headers, left).await {
                Upstream::Unreachable(why) => {
                    self.reachable.store(false, Ordering::Relaxed);
                    let left = end.saturating_duration_since(Instant::now());
                    if left <= pause {
                        return Upstream::Unreachable(why);
                    }
                    tokio::time::sleep(pause).await;
                    pause = (pause * 2).min(Duration::from_secs(2));
                }
                other => return other,
            }
        }
    }

    fn permits(&self, auth: &str) -> Option<(OwnedSemaphorePermit, OwnedSemaphorePermit)> {
        let agent = {
            let mut agents = self.agents.lock().unwrap();
            agents
                .entry(auth.to_string())
                .or_insert_with(|| Arc::new(Semaphore::new(self.config.per_agent)))
                .clone()
        };
        let mine = agent.try_acquire_owned().ok()?;
        let all = self.total.clone().try_acquire_owned().ok()?;
        Some((mine, all))
    }

    async fn tool_call(self: &Arc<Self>, message: Value, headers: HeaderMap) -> Value {
        let tool = message.pointer("/params/name").and_then(Value::as_str).unwrap_or("?").to_string();
        let arrived = Instant::now();
        let reply = self.tool_call_timed(message, headers).await;
        let waited = arrived.elapsed().as_secs_f64() * 1000.0;
        let running = reply_text(&reply).contains(" is still running (");
        let mut latency = self.latency.lock().unwrap();
        if latency.len() < 512 || latency.contains_key(&tool) {
            let kept = latency.entry(tool).or_default();
            kept.calls += 1;
            kept.total_ms += waited;
            kept.max_ms = kept.max_ms.max(waited);
            kept.over_10s += u64::from(waited >= 10_000.0);
            kept.still_running += u64::from(running);
        }
        reply
    }

    async fn tool_call_timed(self: &Arc<Self>, message: Value, headers: HeaderMap) -> Value {
        let id = message.get("id").cloned().unwrap_or(Value::Null);
        let auth = bearer(&headers);
        let params = message.get("params").cloned().unwrap_or(Value::Null);
        let tool = params.get("name").and_then(|n| n.as_str()).unwrap_or("?").to_string();
        let key = call_key(&auth, &params);
        let call = {
            let existing = self.calls.lock().unwrap().get(&key).cloned();
            match existing {
                Some(call) => call,
                None => {
                    let Some(permits) = self.permits(&auth) else {
                        return tool_text(&id, format!(
                            "decompdecomp is already running {} of this helper's tool calls. Wait for those to \
                             finish (repeat any that said \"still running\"), then call {tool} again.",
                            self.config.per_agent));
                    };
                    let (sender, receiver) = watch::channel(None);
                    let call = Arc::new(Call {
                        started: Instant::now(), done: receiver.clone(), finished_at: Mutex::new(None), acked: Mutex::new(false),
                    });
                    let mut calls = self.calls.lock().unwrap();
                    // Another request for the same call may have started it while this one took permits.
                    if let Some(raced) = calls.get(&key) {
                        raced.clone()
                    } else {
                        calls.insert(key.clone(), call.clone());
                        drop(calls);
                        // What this helper left behind before this call; a call left behind itself joins them.
                        let earlier = self.behind_of(&auth);
                        let left = self.config.ack_tools.contains(&tool).then(|| {
                            let id = self.ids.fetch_add(1, Ordering::Relaxed);
                            self.behind.lock().unwrap().entry(auth.clone()).or_default().push((id, receiver.clone()));
                            let entry = format!("{:x}-{id}", std::process::id());
                            if let Some(journal) = &self.journal {
                                journal.add(&json!({"id": entry, "message": message, "headers": saved_headers(&headers)}));
                            }
                            (id, entry)
                        });
                        let gateway = self.clone();
                        let started = call.clone();
                        let (message, headers, tool, auth) = (message.clone(), headers.clone(), tool.clone(), auth.clone());
                        let key_of_task = key.clone();
                        tokio::spawn(async move {
                            let _permits = permits;
                            let limit = gateway.config.upstream_limit;
                            let mut read = if tool == "claim_function" {
                                gateway.claim_from_here(&message, &headers).await
                            } else {
                                gateway.from_here(&message, &headers, &tool).await
                            };
                            if read.is_none() {
                                read = gateway.from_reader(&message, &headers, &tool).await;
                            }
                            let reply = match read {
                                Some(reply) => Upstream::Reply(reply),
                                None => {
                                    gateway.wait_for(earlier, limit).await;
                                    gateway.send_patiently(&message, &headers, limit).await
                                }
                            };
                            let reply = match reply {
                                Upstream::Reply(reply) => reply,
                                Upstream::Unreachable(why) => tool_text(&Value::Null, format!(
                                    "decompdecomp isn't running or is restarting ({why}). Nothing was changed. \
                                     Wait a minute, then call {tool} again.")),
                                Upstream::Failed(why) => tool_text(&Value::Null, format!(
                                    "decompdecomp failed while running {tool}: {why}. Check the function's state \
                                     (get_function) before repeating it.")),
                            };
                            *started.finished_at.lock().unwrap() = Some(Instant::now());
                            {
                                let acked = started.acked.lock().unwrap();
                                if *acked {
                                    gateway.notice(&auth, format!(
                                        "decompdecomp has now finished your earlier {tool} call ({}): {}",
                                        brief_arguments(&message), reply_text(&reply)));
                                }
                                let _ = sender.send(Some(reply));
                            }
                            if let Some((id, entry)) = left {
                                gateway.left_done(&auth, id, &entry);
                                if *started.acked.lock().unwrap() {
                                    // Nobody will repeat it: its answer went to the notices.
                                    let mut calls = gateway.calls.lock().unwrap();
                                    if calls.get(&key_of_task).is_some_and(|c| Arc::ptr_eq(c, &started)) {
                                        calls.remove(&key_of_task);
                                    }
                                }
                            }
                        });
                        call
                    }
                }
            }
        };
        let mut done = call.done.clone();
        let leave = self.config.ack_tools.contains(&tool);
        let deadline = if leave { self.config.ack_deadline.min(self.config.call_deadline) } else { self.config.call_deadline };
        let waited = tokio::time::timeout(deadline, done.wait_for(|r| r.is_some())).await;
        let notes = self.notices_of(&auth);
        if let Ok(Ok(reply)) = waited {
            let reply = reply.clone().unwrap_or(Value::Null);
            // Delivered: the next identical call is a new call.
            let mut calls = self.calls.lock().unwrap();
            if calls.get(&key).is_some_and(|c| Arc::ptr_eq(c, &call)) {
                calls.remove(&key);
            }
            self.served.fetch_add(1, Ordering::Relaxed);
            return with_notes(with_id(reply, &id), &notes);
        }
        if leave {
            let mut acked = call.acked.lock().unwrap();
            if let Some(reply) = call.done.borrow().clone() {
                drop(acked);
                let mut calls = self.calls.lock().unwrap();
                if calls.get(&key).is_some_and(|c| Arc::ptr_eq(c, &call)) {
                    calls.remove(&key);
                }
                self.served.fetch_add(1, Ordering::Relaxed);
                return with_notes(with_id(reply, &id), &notes);
            }
            *acked = true;
            drop(acked);
            self.acknowledged.fetch_add(1, Ordering::Relaxed);
            let text = format!(
                "{tool} accepted: decompdecomp is busy, so it records it in the background ({}). Go on with your \
                 next step (claim another function); your next calls wait for it to be recorded first. If \
                 decompdecomp answers anything you need to know, it comes at the top of your next tool reply. \
                 Don't repeat this call.", brief_arguments(&message));
            let reply = json!({"jsonrpc": "2.0", "id": id, "result": {
                "content": [{"type": "text", "text": text}], "structuredContent": {"result": text}, "isError": false}});
            return with_notes(reply, &notes);
        }
        if !notes.is_empty() {
            // Not delivered now: keep them for the reply that is.
            self.notices.lock().unwrap().entry(auth.clone()).or_default().splice(0..0, notes);
        }
        self.late.fetch_add(1, Ordering::Relaxed);
        let running = call.started.elapsed().as_secs();
        let state = if self.reachable.load(Ordering::Relaxed) {
            "decompdecomp is still working on it"
        } else {
            "decompdecomp is busy or restarting and hasn't answered yet"
        };
        tool_text(&id, format!(
            "{tool} is still running ({running} s so far): {state}. Nothing was lost and it will not run twice: \
             repeat exactly the same {tool} call (same arguments) to wait for its result. Meanwhile don't redo \
             its work another way."))
    }

    /// This helper's calls left behind and still running.
    fn behind_of(&self, auth: &str) -> Vec<watch::Receiver<Option<Value>>> {
        self.behind.lock().unwrap().get(auth).map(|v| v.iter().map(|(_, r)| r.clone()).collect()).unwrap_or_default()
    }

    /// Wait (up to `limit`) until those calls have their answers, so the app sees them before what comes after.
    async fn wait_for(&self, earlier: Vec<watch::Receiver<Option<Value>>>, limit: Duration) {
        let end = Instant::now() + limit;
        for mut done in earlier {
            let left = end.saturating_duration_since(Instant::now());
            let _ = tokio::time::timeout(left, done.wait_for(|r| r.is_some())).await;
        }
    }

    fn left_done(&self, auth: &str, id: u64, entry: &str) {
        let mut behind = self.behind.lock().unwrap();
        if let Some(list) = behind.get_mut(auth) {
            list.retain(|(i, _)| *i != id);
            if list.is_empty() {
                behind.remove(auth);
            }
        }
        drop(behind);
        if let Some(journal) = &self.journal {
            journal.remove(entry);
        }
    }

    fn notice(&self, auth: &str, text: String) {
        self.notices.lock().unwrap().entry(auth.to_string()).or_default().push(text);
    }

    fn notices_of(&self, auth: &str) -> Vec<String> {
        self.notices.lock().unwrap().remove(auth).unwrap_or_default()
    }

    /// Calls left behind when the gateway last ended, sent to the app again (it never answered them).
    fn replay(self: &Arc<Self>) {
        let Some(journal) = &self.journal else { return };
        for entry in journal.entries() {
            let (Some(message), Some(entry_id)) = (entry.get("message").cloned(), entry.get("id").and_then(Value::as_str)) else {
                continue;
            };
            let headers = restored_headers(entry.get("headers").unwrap_or(&Value::Null));
            let quiet = entry.get("quiet").and_then(Value::as_str).map(String::from);
            self.behind_app(message, headers, quiet, Some(entry_id.to_string()));
        }
    }

    /// Send `message` to the app in the background for its helper, who has already been answered: kept in the
    /// journal until the app answers, the helper's later calls to the app wait for it, and the app's answer comes at
    /// the top of the helper's next reply, unless it is the claim the gateway handed over (`quiet`: a reply whose
    /// first line ends with "/<quiet>", the claim recorded as given).
    fn behind_app(self: &Arc<Self>, message: Value, headers: HeaderMap, quiet: Option<String>, journaled: Option<String>) {
        let auth = bearer(&headers);
        let (sender, receiver) = watch::channel(None);
        let id = self.ids.fetch_add(1, Ordering::Relaxed);
        let earlier = self.behind_of(&auth);
        self.behind.lock().unwrap().entry(auth.clone()).or_default().push((id, receiver));
        let entry_id = journaled.unwrap_or_else(|| {
            let entry_id = format!("{:x}-{id}", std::process::id());
            if let Some(journal) = &self.journal {
                journal.add(&json!({"id": entry_id, "message": message, "headers": saved_headers(&headers), "quiet": quiet}));
            }
            entry_id
        });
        let gateway = self.clone();
        tokio::spawn(async move {
            let limit = gateway.config.upstream_limit;
            gateway.wait_for(earlier, limit).await;
            let tool = message.pointer("/params/name").and_then(Value::as_str).unwrap_or("?").to_string();
            let reply = match gateway.send_patiently(&message, &headers, limit).await {
                Upstream::Reply(reply) => reply,
                Upstream::Unreachable(why) | Upstream::Failed(why) => tool_text(&Value::Null, why),
            };
            let text = reply_text(&reply);
            let error = reply.get("error").is_some()
                || reply.pointer("/result/isError").and_then(Value::as_bool).unwrap_or(false);
            let given = quiet.as_ref().is_some_and(|name| {
                !error && text.lines().next().is_some_and(|first| first.trim_end().ends_with(&format!("/{name}")))
            });
            if !given {
                gateway.notice(&auth, format!("decompdecomp has now finished your earlier {tool} call ({}): {text}",
                                              brief_arguments(&message)));
            }
            let _ = sender.send(Some(reply));
            gateway.left_done(&auth, id, &entry_id);
        });
    }

    /// claim_function answered with the helper's prepared claim (native.rs claim), or None. The app records the
    /// claim in the background (claim_function naming the function), with every check it makes.
    async fn claim_from_here(self: &Arc<Self>, message: &Value, headers: &HeaderMap) -> Option<Value> {
        let id = message.get("id").cloned().unwrap_or(Value::Null);
        let arguments = message.pointer("/params/arguments").cloned().unwrap_or_else(|| json!({}));
        let (auth, native) = (bearer(headers), self.native.clone());
        if !self.behind_of(&auth).is_empty() {
            return None; // (a release still on its way: the app answers after it)
        }
        let started = Instant::now();
        let asked = arguments.clone();
        let work = tokio::task::spawn_blocking(move || native.claim(&auth, &asked));
        let (served, function) = tokio::time::timeout(Duration::from_secs(10), work).await.ok()?.ok()??;
        let mut record = message.clone();
        if let Some(params) = record.get_mut("params") {
            params["arguments"] = json!({"function": function});
            params.as_object_mut().map(|p| p.remove("_meta"));
        }
        if let Some(object) = record.as_object_mut() {
            object.insert("id".into(), json!(format!("gw-claim-{}", self.ids.fetch_add(1, Ordering::Relaxed))));
        }
        self.behind_app(record, headers.clone(), Some(function), None);
        self.answered.fetch_add(1, Ordering::Relaxed);
        self.claims_given.fetch_add(1, Ordering::Relaxed);
        self.report(&served, "claim_function", &arguments, started);
        Some(json!({"jsonrpc": "2.0", "id": id, "result": {
            "content": [{"type": "text", "text": served.text}],
            "structuredContent": {"result": served.text},
            "isError": false}}))
    }

    /// Tell the app about a call the gateway answered (app/tool_reader.py apply_report).
    fn report(&self, served: &native::Served, tool: &str, arguments: &Value, started: Instant) {
        let call_id = format!("gw{:x}{:x}", std::process::id(), self.reports.fetch_add(1, Ordering::Relaxed));
        let report = json!({"call": {
            "agent_id": served.agent_id, "agent_name": served.agent_name, "slug": served.slug, "name": tool,
            "arguments": arguments, "call_id": call_id, "content": served.text,
            "timings": {"dispatch_ms": (started.elapsed().as_secs_f64() * 1000.0 * 100.0).round() / 100.0, "gateway": true},
        }});
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{report}");
        let _ = out.flush();
    }

    async fn remembered(self: &Arc<Self>, message: Value, headers: HeaderMap, method: &str) -> Value {
        let id = message.get("id").cloned().unwrap_or(Value::Null);
        let slot = (bearer(&headers), method.to_string());
        let deadline = self.config.meta_deadline;
        let fresh = tokio::time::timeout(deadline, self.send_patiently(&message, &headers, deadline)).await;
        let project = {
            let native = self.native.clone();
            let auth = slot.0.clone();
            tokio::task::spawn_blocking(move || native.project_of_bearer(&auth)).await.ok().flatten()
        };
        if let Ok(Upstream::Reply(reply)) = fresh {
            if let Some(result) = reply.get("result") {
                self.answers.lock().unwrap().insert(slot, result.clone());
                let mut kept = self.by_project.lock().unwrap();
                if kept.keep(project.as_deref(), method, result) {
                    kept.save(&self.config.state);
                }
            }
            return with_id(reply, &id);
        }
        if let Some(result) = self.answers.lock().unwrap().get(&slot).cloned() {
            return json!({"jsonrpc": "2.0", "id": id, "result": result});
        }
        // A new connection (a helper just started, or the app restarted under it): its project's last answer, else
        // the latest any agent got. The app still judges every call, so a tool listed here it wouldn't offer this
        // agent is refused when called; a refusal now would leave the agent with no tools at all.
        {
            let kept = self.by_project.lock().unwrap();
            let found = project
                .as_deref()
                .and_then(|p| kept.projects.get(&format!("{p}\t{method}")))
                .or_else(|| kept.latest.get(method))
                .cloned();
            if let Some(result) = found {
                return json!({"jsonrpc": "2.0", "id": id, "result": result});
            }
        }
        if method == "initialize" {
            // Nothing to fall back on: answer the handshake ourselves; the tool list comes when the app answers.
            let version = message
                .pointer("/params/protocolVersion")
                .and_then(|v| v.as_str())
                .unwrap_or(PROTOCOL_FALLBACK);
            return json!({"jsonrpc": "2.0", "id": id, "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": false}},
                "serverInfo": {"name": "decomp", "title": "decompdecomp", "version": env!("CARGO_PKG_VERSION")},
                "instructions": "decompdecomp is starting. List the tools again in a minute."}});
        }
        rpc_error(&id, -32002, "decompdecomp is busy or starting; try again in a minute")
    }

    async fn one(self: &Arc<Self>, message: Value, headers: HeaderMap) -> Option<Value> {
        let id = message.get("id").cloned();
        let method = message.get("method").and_then(|m| m.as_str()).unwrap_or("").to_string();
        let Some(id) = id else {
            // A notification: the app keeps no session behind the gateway, so there's nothing to deliver.
            return None;
        };
        if message.get("method").is_none() {
            return None; // a client's reply to a server request; this server makes none
        }
        Some(match method.as_str() {
            "ping" => json!({"jsonrpc": "2.0", "id": id, "result": {}}),
            "tools/call" => self.tool_call(message, headers).await,
            "initialize" | "tools/list" => self.remembered(message, headers, &method).await,
            _ => {
                let deadline = self.config.meta_deadline;
                match tokio::time::timeout(deadline, self.send_patiently(&message, &headers, deadline)).await {
                    Ok(Upstream::Reply(reply)) => with_id(reply, &id),
                    _ => rpc_error(&id, -32002, "decompdecomp is busy or starting; try again in a minute"),
                }
            }
        })
    }
}

/// The call's function, to name it in a later notice.
fn brief_arguments(message: &Value) -> String {
    match message.pointer("/params/arguments/function").and_then(Value::as_str) {
        Some(function) => format!("function={function}"),
        None => "no function named".into(),
    }
}

fn json_response(status: StatusCode, value: Value) -> Response {
    let mut response = (status, value.to_string()).into_response();
    response.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/json"));
    response
}

async fn mcp_post(State(gateway): State<Arc<Gateway>>, headers: HeaderMap, body: Bytes) -> Response {
    if bearer(&headers).is_empty() {
        return json_response(StatusCode::UNAUTHORIZED, rpc_error(&Value::Null, -32001, "missing Authorization"));
    }
    let parsed: Value = match serde_json::from_slice(&body) {
        Ok(value) => value,
        Err(error) => return json_response(StatusCode::BAD_REQUEST, rpc_error(&Value::Null, -32700, error.to_string())),
    };
    match parsed {
        Value::Array(messages) => {
            let tasks: Vec<_> = messages
                .into_iter()
                .map(|m| {
                    let (gateway, headers) = (gateway.clone(), headers.clone());
                    tokio::spawn(async move { gateway.one(m, headers).await })
                })
                .collect();
            let mut replies = Vec::new();
            for task in tasks {
                if let Ok(Some(reply)) = task.await {
                    replies.push(reply);
                }
            }
            if replies.is_empty() {
                StatusCode::ACCEPTED.into_response()
            } else {
                json_response(StatusCode::OK, Value::Array(replies))
            }
        }
        message @ Value::Object(_) => match gateway.one(message, headers).await {
            Some(reply) => json_response(StatusCode::OK, reply),
            None => StatusCode::ACCEPTED.into_response(),
        },
        _ => json_response(StatusCode::BAD_REQUEST, rpc_error(&Value::Null, -32600, "not a JSON-RPC message")),
    }
}

async fn mcp_other() -> Response {
    // Stateless: no server-to-client stream and no session to end.
    let mut response = StatusCode::METHOD_NOT_ALLOWED.into_response();
    response.headers_mut().insert(header::ALLOW, HeaderValue::from_static("POST"));
    response
}

async fn health(State(gateway): State<Arc<Gateway>>) -> Response {
    let running = gateway.calls.lock().unwrap().len();
    json_response(StatusCode::OK, json!({
        "ok": true,
        "app_reachable": gateway.reachable.load(Ordering::Relaxed),
        "calls_tracked": running,
        "served": gateway.served.load(Ordering::Relaxed),
        "still_running_replies": gateway.late.load(Ordering::Relaxed),
        "reader": gateway.config.reader.is_some(),
        "read_by_reader": gateway.read.load(Ordering::Relaxed),
        "passed_to_app": gateway.declined.load(Ordering::Relaxed),
        "answered_by_gateway": gateway.answered.load(Ordering::Relaxed),
        "accepted_in_background": gateway.acknowledged.load(Ordering::Relaxed),
        "prepared_claims_given": gateway.claims_given.load(Ordering::Relaxed),
        "tools": gateway.latency.lock().unwrap().iter().map(|(tool, l)| (tool.clone(), json!({
            "calls": l.calls, "mean_ms": (l.total_ms / l.calls.max(1) as f64).round(), "max_ms": l.max_ms.round(),
            "over_10s": l.over_10s, "still_running": l.still_running,
        }))).collect::<serde_json::Map<String, Value>>(),
        "left_behind": gateway.behind.lock().unwrap().values().map(Vec::len).sum::<usize>(),
        "window": gateway.window.get().map(|w| w.health()),
    }))
}

/// Results nobody came back for are dropped after `keep_results`.
async fn sweep(gateway: Arc<Gateway>) {
    loop {
        tokio::time::sleep(Duration::from_secs(30)).await;
        let keep = gateway.config.keep_results;
        gateway.calls.lock().unwrap().retain(|_, call| {
            call.finished_at.lock().unwrap().map_or(true, |at| at.elapsed() < keep)
        });
    }
}

/// The app holds our standard input open and writes its routes there (native.rs), one JSON line each; when it
/// closes (the app quit or crashed) the gateway ends too.
async fn until_parent_leaves(native: Arc<native::Native>) {
    let mut lines = tokio::io::BufReader::new(tokio::io::stdin()).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if let Ok(message) = serde_json::from_str::<Value>(&line) {
            if let Some(routes) = message.get("routes").and_then(native::Routes::parse) {
                native.set_routes(routes);
            }
            if let Some(guides) = message.get("guides").and_then(native::Guides::parse) {
                native.set_guides(guides);
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.as_slice() == ["desktop-judge"] {
        std::process::exit(desktop_judge::serve());
    }
    if args.as_slice() == ["entry-fixes"] {
        std::process::exit(entry_fixes::serve());
    }
    if args.as_slice() == ["twins-prints"] {
        std::process::exit(twins_prints::serve());
    }
    if args.as_slice() == ["scoring-diff"] {
        std::process::exit(scoring::serve());
    }
    if args.as_slice() == ["elf-extract"] {
        std::process::exit(elf_extract::serve());
    }
    if args.as_slice() == ["automatic-boundaries"] {
        std::process::exit(automatic_boundaries::serve());
    }
    gateway_main(args);
}

#[tokio::main]
async fn gateway_main(mut args: Vec<String>) {
    if args.as_slice() == ["--licenses"] {
        print!("{}", include_str!("../THIRD_PARTY_LICENSES.txt"));
        return;
    }
    if args.first().map(String::as_str) == Some("hosted") {
        // Standalone: shared projects served without the desktop app (hosted.rs).
        args.remove(0);
        std::process::exit(hosted::main(args).await);
    }
    if args.first().map(String::as_str) == Some("verifier") {
        // A compiler server judging a hosted server's submissions (verifier.rs).
        args.remove(0);
        std::process::exit(verifier::main(args).await);
    }
    let config = match Config::from_args() {
        Ok(config) => config,
        Err(problem) => {
            log(problem);
            std::process::exit(2);
        }
    };
    let client = reqwest::Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(2))
        .pool_idle_timeout(Duration::from_secs(30))
        .build()
        .expect("HTTP client");
    let gateway = Arc::new(Gateway {
        total: Arc::new(Semaphore::new(config.total)),
        config: config.clone(),
        client,
        calls: Mutex::new(HashMap::new()),
        answers: Mutex::new(HashMap::new()),
        by_project: Mutex::new(Remembered::load(&config.state)),
        agents: Mutex::new(HashMap::new()),
        served: AtomicU64::new(0),
        late: AtomicU64::new(0),
        reachable: AtomicBool::new(true),
        read: AtomicU64::new(0),
        declined: AtomicU64::new(0),
        native: Arc::new(native::Native::new()),
        answered: AtomicU64::new(0),
        reports: AtomicU64::new(0),
        window: std::sync::OnceLock::new(),
        behind: Mutex::new(HashMap::new()),
        notices: Mutex::new(HashMap::new()),
        journal: config.journal.clone().map(|path| Journal { path, lock: Mutex::new(()) }),
        ids: AtomicU64::new(0),
        acknowledged: AtomicU64::new(0),
        claims_given: AtomicU64::new(0),
        latency: Mutex::new(HashMap::new()),
    });
    gateway.replay();
    let app = Router::new()
        .route("/mcp", post(mcp_post).get(mcp_other).delete(mcp_other))
        .route("/mcp/", post(mcp_post).get(mcp_other).delete(mcp_other))
        .route("/health", get(health))
        .layer(DefaultBodyLimit::max(config.max_body))
        .with_state(gateway.clone());
    let listener = match tokio::net::TcpListener::bind(&config.listen).await {
        Ok(listener) => listener,
        Err(error) => {
            log(format!("can't listen on {}: {error}", config.listen));
            std::process::exit(3);
        }
    };
    let address = listener.local_addr().expect("bound address");
    // The window's listener, bound before the app hears the ports: the app's window loads its pages there.
    let mut window_port = None;
    if let (Some(listen), Some(upstream)) = (config.window_listen.clone(), config.window_upstream.clone()) {
        let bound = match tokio::net::TcpListener::bind(&listen).await {
            Ok(bound) => bound,
            Err(error) => {
                log(format!("can't listen on {listen} for the window: {error}"));
                std::process::exit(3);
            }
        };
        let port = bound.local_addr().expect("bound address").port();
        let window_secret = std::env::var("DECOMP_GATEWAY_WINDOW_SECRET").ok();
        let db = window_db::WindowDb::from_config(
            config.window_db.as_deref(),
            config.state.as_deref(),
            window_secret,
        );
        let config = window::Config {
            upstream: upstream.trim_end_matches('/').to_string(),
            reader: config.window_reader.clone().map(|r| r.trim_end_matches('/').to_string()),
            reads: config.window_reads.clone(),
            reader_limit: config.window_reader_limit,
            head_limit: config.upstream_limit,
            db,
        };
        let proxy = match window::Window::new(config, port) {
            Ok(proxy) => Arc::new(proxy),
            Err(problem) => {
                log(problem);
                std::process::exit(2);
            }
        };
        let _ = gateway.window.set(proxy.clone());
        let routes = Router::new().fallback(window::handle).with_state(proxy);
        tokio::spawn(async move {
            if let Err(error) = axum::serve(bound, routes).await {
                log(format!("the window's listener stopped: {error}"));
            }
        });
        window_port = Some(port);
    }
    // The app reads this line to learn the ports.
    match window_port {
        Some(port) => println!("LISTENING {} WINDOW {port}", address.port()),
        None => println!("LISTENING {}", address.port()),
    }
    let native = gateway.native.clone();
    tokio::spawn(sweep(gateway));
    let server = axum::serve(listener, app).with_graceful_shutdown(async {
        tokio::select! {
            _ = until_parent_leaves(native) => {},
            _ = tokio::signal::ctrl_c() => {},
        }
    });
    if let Err(error) = server.await {
        log(format!("server stopped: {error}"));
    }
    // Graceful shutdown waits for open requests; a held tool call must not keep the process alive.
    std::process::exit(0);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_ignores_key_order() {
        let a = json!({"name": "compile", "arguments": {"b": 1, "a": [1, {"y": 2, "x": 3}]}});
        let b = json!({"arguments": {"a": [1, {"x": 3, "y": 2}], "b": 1}, "name": "compile"});
        assert_eq!(call_key("t", &a), call_key("t", &b));
        assert_ne!(call_key("t", &a), call_key("u", &a));
    }

    #[test]
    fn a_repeat_with_new_request_metadata_is_the_same_call() {
        let first = json!({"name": "rename_symbol", "arguments": {"old": "f", "new": "g"}, "_meta": {"progressToken": 7}});
        let repeat = json!({"name": "rename_symbol", "arguments": {"new": "g", "old": "f"}, "_meta": {"progressToken": 8}});
        let bare = json!({"name": "rename_symbol", "arguments": {"old": "f", "new": "g"}});
        assert_eq!(call_key("t", &first), call_key("t", &repeat));
        assert_eq!(call_key("t", &first), call_key("t", &bare));
        let other = json!({"name": "rename_symbol", "arguments": {"old": "f", "new": "h"}, "_meta": {"progressToken": 7}});
        assert_ne!(call_key("t", &first), call_key("t", &other));
        assert_eq!(call_key("t", &json!({"name": "guide"})), call_key("t", &json!({"name": "guide", "arguments": {}})));
    }

    #[test]
    fn finds_reply_in_sse_and_json() {
        let id = json!(7);
        let sse = b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\n\n\
event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":7,\"result\":{\"ok\":1}}\n\n";
        assert_eq!(reply_in(sse, "text/event-stream", &id).unwrap()["result"]["ok"], 1);
        let plain = br#"{"jsonrpc":"2.0","id":7,"result":{"ok":2}}"#;
        assert_eq!(reply_in(plain, "application/json", &id).unwrap()["result"]["ok"], 2);
        assert!(reply_in(b"data: nope\n\n", "text/event-stream", &id).is_none());
    }

    #[test]
    fn recognizes_the_readers_decline() {
        let no = json!({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": DECLINED}], "isError": true}});
        assert!(declined(&no));
        let error = json!({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "no function f"}], "isError": true}});
        assert!(!declined(&error));
        let answer = json!({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": DECLINED}], "isError": false}});
        assert!(!declined(&answer));
    }

    #[test]
    fn replies_take_the_current_request_id() {
        let reply = with_id(json!({"jsonrpc": "2.0", "id": null, "result": {}}), &json!("abc"));
        assert_eq!(reply["id"], "abc");
    }
}
