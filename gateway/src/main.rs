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
mod verifier;

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
                _ => return Err(format!("unknown option {flag}")),
            }
        }
        if config.upstream.is_empty() {
            return Err("--upstream is required (the app's MCP address)".into());
        }
        Ok(config)
    }
}

/// One tool call running in the app. Agents that repeat it wait on `done`.
struct Call {
    started: Instant,
    done: watch::Receiver<Option<Value>>,
    finished_at: Mutex<Option<Instant>>,
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
        let call_id = format!("gw{:x}{:x}", std::process::id(), self.reports.fetch_add(1, Ordering::Relaxed));
        let report = json!({"call": {
            "agent_id": served.agent_id, "agent_name": served.agent_name, "slug": served.slug, "name": tool,
            "arguments": arguments, "call_id": call_id, "content": served.text,
            "timings": {"dispatch_ms": (started.elapsed().as_secs_f64() * 1000.0 * 100.0).round() / 100.0, "gateway": true},
        }});
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{report}");
        let _ = out.flush();
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
        for name in ["authorization", "mcp-protocol-version", "user-agent", "x-decomp-connection-check"] {
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
                    let call = Arc::new(Call { started: Instant::now(), done: receiver, finished_at: Mutex::new(None) });
                    let mut calls = self.calls.lock().unwrap();
                    // Another request for the same call may have started it while this one took permits.
                    if let Some(raced) = calls.get(&key) {
                        raced.clone()
                    } else {
                        calls.insert(key.clone(), call.clone());
                        drop(calls);
                        let gateway = self.clone();
                        let started = call.clone();
                        let (message, headers, tool) = (message.clone(), headers.clone(), tool.clone());
                        tokio::spawn(async move {
                            let _permits = permits;
                            let limit = gateway.config.upstream_limit;
                            let mut read = gateway.from_here(&message, &headers, &tool).await;
                            if read.is_none() {
                                read = gateway.from_reader(&message, &headers, &tool).await;
                            }
                            let reply = match read {
                                Some(reply) => Upstream::Reply(reply),
                                None => gateway.send_patiently(&message, &headers, limit).await,
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
                            let _ = sender.send(Some(reply));
                        });
                        call
                    }
                }
            }
        };
        let mut done = call.done.clone();
        let waited = tokio::time::timeout(self.config.call_deadline, done.wait_for(|r| r.is_some())).await;
        if let Ok(Ok(reply)) = waited {
            let reply = reply.clone().unwrap_or(Value::Null);
            // Delivered: the next identical call is a new call.
            let mut calls = self.calls.lock().unwrap();
            if calls.get(&key).is_some_and(|c| Arc::ptr_eq(c, &call)) {
                calls.remove(&key);
            }
            self.served.fetch_add(1, Ordering::Relaxed);
            return with_id(reply, &id);
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

#[tokio::main]
async fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
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
    });
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
    // The app reads this line to learn the port.
    println!("LISTENING {}", address.port());
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
