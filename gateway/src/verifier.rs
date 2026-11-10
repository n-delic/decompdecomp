//! Verifier mode: a compiler server that judges a hosted server's submissions.
//!
//!     decomp-mcp-gateway verifier --server https://decomp.example --token TOKEN --name mac-studio \
//!         [--slots 4] [--judge decomp --judge-arg judge-worker] [--idle-exit SECONDS]
//!
//! Agents compile on their own machines and report a score; a hosted server (hosted.rs) counts a match only once a
//! trusted machine has compiled the same C and got the same bytes. This is that machine's side:
//! - it runs one judge process per slot. A judge reads one JSON request per line and answers one per line;
//!   `decomp judge-worker` (src/decompnet/hosted_judge.py) judges with decompdecomp's own compilers and linker
//!   against this computer's copy of each game. Any program speaking the same lines will do.
//! - it asks a judge which projects and toolchains it has, and registers them with the server;
//! - it leases submissions up to its free slots, judges them, and posts each verdict;
//! - what it can't judge (a different copy of the game, a judge that crashed or hung) it releases for another
//!   verifier. Hung or crashed judges are replaced.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Semaphore;

const JUDGE_LIMIT: Duration = Duration::from_secs(300);
const REREGISTER: Duration = Duration::from_secs(300);

fn log(message: impl AsRef<str>) {
    eprintln!("decomp-mcp-gateway verifier: {}", message.as_ref());
}

struct Options {
    server: String,
    token: String,
    name: String,
    slots: usize,
    judge: Vec<String>,
    poll: Duration,
    idle_exit: Option<Duration>,
}

impl Options {
    fn parse(args: Vec<String>) -> Result<Options, String> {
        let mut options = Options {
            server: String::new(),
            token: std::env::var("DECOMP_VERIFIER_TOKEN").unwrap_or_default(),
            name: String::new(),
            slots: 2,
            judge: Vec::new(),
            poll: Duration::from_secs(3),
            idle_exit: None,
        };
        let mut judge_args = Vec::new();
        let mut args = args.into_iter();
        while let Some(flag) = args.next() {
            let value = args.next().ok_or_else(|| format!("{flag} needs a value"))?;
            match flag.as_str() {
                "--server" => options.server = value.trim_end_matches('/').to_string(),
                "--token" => options.token = value,
                "--name" => options.name = value,
                "--slots" => options.slots = value.parse().ok().filter(|n| *n > 0).ok_or("--slots is a count")?,
                "--judge" => options.judge = vec![value],
                "--judge-arg" => judge_args.push(value),
                "--poll" => options.poll = Duration::from_secs_f64(value.parse().map_err(|_| "--poll is seconds")?),
                "--idle-exit" => {
                    options.idle_exit = Some(Duration::from_secs_f64(value.parse().map_err(|_| "--idle-exit is seconds")?))
                }
                _ => return Err(format!("unknown option {flag}")),
            }
        }
        if options.judge.is_empty() {
            options.judge = vec!["decomp".into(), "judge-worker".into()];
        }
        options.judge.extend(judge_args);
        if options.server.is_empty() || options.token.is_empty() || options.name.is_empty() {
            return Err("--server, --token (or DECOMP_VERIFIER_TOKEN) and --name are required".into());
        }
        Ok(options)
    }
}

/// One judge process: a request line in, a reply line out.
struct Judge {
    _child: Child,
    input: ChildStdin,
    output: Lines<BufReader<ChildStdout>>,
}

impl Judge {
    fn start(command: &[String]) -> Result<Judge, String> {
        let mut child = Command::new(&command[0])
            .args(&command[1..])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| format!("can't start the judge {:?}: {e}", command[0]))?;
        let input = child.stdin.take().ok_or("no judge stdin")?;
        let output = BufReader::new(child.stdout.take().ok_or("no judge stdout")?).lines();
        Ok(Judge { _child: child, input, output })
    }

    async fn ask(&mut self, request: &Value, limit: Duration) -> Result<Value, String> {
        let exchange = async {
            self.input.write_all(format!("{request}\n").as_bytes()).await.map_err(|e| e.to_string())?;
            self.input.flush().await.map_err(|e| e.to_string())?;
            match self.output.next_line().await {
                Ok(Some(line)) => serde_json::from_str::<Value>(&line).map_err(|e| format!("the judge said {line:?}: {e}")),
                Ok(None) => Err("the judge ended".into()),
                Err(e) => Err(e.to_string()),
            }
        };
        tokio::time::timeout(limit, exchange).await.map_err(|_| format!("the judge took over {} s", limit.as_secs()))?
    }
}

struct Verifier {
    options: Options,
    http: reqwest::Client,
    judges: Mutex<Vec<Judge>>,
    id: AtomicU64,
    judged: AtomicU64,
    released: AtomicU64,
}

impl Verifier {
    async fn post(&self, path: &str, body: &Value) -> Result<Value, String> {
        let response = self
            .http
            .post(format!("{}{path}", self.options.server))
            .bearer_auth(&self.options.token)
            .json(body)
            .send()
            .await
            .map_err(|e| e.to_string())?;
        let status = response.status();
        let value: Value = response.json().await.map_err(|e| e.to_string())?;
        if !status.is_success() {
            return Err(format!("{path}: HTTP {status}: {}", value.get("error").and_then(Value::as_str).unwrap_or("")));
        }
        Ok(value)
    }

    fn judge(&self) -> Result<Judge, String> {
        match self.judges.lock().unwrap().pop() {
            Some(judge) => Ok(judge),
            None => Judge::start(&self.options.judge),
        }
    }

    async fn register(&self) -> Result<(), String> {
        let mut judge = self.judge()?;
        let described = judge.ask(&json!({"op": "describe"}), Duration::from_secs(120)).await?;
        self.judges.lock().unwrap().push(judge);
        let projects = described.get("projects").cloned().unwrap_or_else(|| json!([]));
        let body = json!({"name": self.options.name, "projects": projects,
                          "toolchains": described.get("toolchains").cloned().unwrap_or_else(|| json!([])),
                          "slots": self.options.slots,
                          "version": described.get("version").and_then(Value::as_str).unwrap_or("")});
        let registered = self.post("/v1/verifiers", &body).await?;
        let id = registered.get("verifier").and_then(Value::as_u64).ok_or("the server gave no verifier id")?;
        self.id.store(id, Ordering::Relaxed);
        log(format!(
            "registered as verifier {id} for {} (this computer has {})",
            registered.get("projects").map(|p| p.to_string()).unwrap_or_default(),
            projects
        ));
        Ok(())
    }

    async fn run_one(self: Arc<Self>, item: Value) {
        let id = item.get("id").and_then(Value::as_i64).unwrap_or(0);
        let slug = item.get("slug").and_then(Value::as_str).unwrap_or("").to_string();
        let verifier = self.id.load(Ordering::Relaxed);
        let request = json!({"op": "judge", "slug": slug, "function": item.get("function"),
                             "compiler": item.get("compiler"), "source": item.get("source")});
        let outcome = match self.judge() {
            Ok(mut judge) => match judge.ask(&request, JUDGE_LIMIT).await {
                Ok(reply) => {
                    self.judges.lock().unwrap().push(judge);
                    Ok(reply)
                }
                Err(e) => Err(e), // (dropped: a judge that hung or ended is replaced)
            },
            Err(e) => Err(e),
        };
        let result = match outcome {
            Ok(reply) if reply.get("error").is_none() => {
                let body = json!({"id": id, "verifier": verifier, "score": reply.get("score"),
                                  "detail": reply.get("detail").and_then(Value::as_str).unwrap_or("")});
                self.post(&format!("/v1/p/{slug}/verdicts"), &body).await.map(|v| {
                    self.judged.fetch_add(1, Ordering::Relaxed);
                    log(format!("{slug} #{id}: {} (score {})", v["state"].as_str().unwrap_or("?"), reply["score"]));
                })
            }
            Ok(reply) => Err(reply["error"].as_str().unwrap_or("the judge failed").to_string()),
            Err(e) => Err(e),
        };
        if let Err(why) = result {
            log(format!("{slug} #{id}: couldn't judge it: {why}"));
            self.released.fetch_add(1, Ordering::Relaxed);
            let body = json!({"verifier": verifier, "slug": slug, "id": id, "reason": format!("verifier {}: {why}", self.options.name)});
            if let Err(e) = self.post("/v1/work/release", &body).await {
                log(format!("{slug} #{id}: release failed ({e}); its lease runs out instead"));
            }
        }
    }
}

pub async fn main(args: Vec<String>) -> i32 {
    let options = match Options::parse(args) {
        Ok(o) => o,
        Err(e) => {
            log(e);
            return 2;
        }
    };
    let slots = options.slots;
    let verifier = Arc::new(Verifier {
        http: reqwest::Client::builder().timeout(Duration::from_secs(60)).build().expect("HTTP client"),
        options,
        judges: Mutex::new(Vec::new()),
        id: AtomicU64::new(0),
        judged: AtomicU64::new(0),
        released: AtomicU64::new(0),
    });
    let mut pause = Duration::from_secs(1);
    while let Err(e) = verifier.register().await {
        log(format!("can't register yet: {e}"));
        tokio::time::sleep(pause).await;
        pause = (pause * 2).min(Duration::from_secs(60));
    }
    let running = Arc::new(Semaphore::new(slots));
    let mut registered = Instant::now();
    let mut idle_since = Instant::now();
    loop {
        if registered.elapsed() > REREGISTER {
            if let Err(e) = verifier.register().await {
                log(format!("re-registering failed: {e}"));
            }
            registered = Instant::now();
        }
        let free = running.available_permits();
        let mut leased = 0;
        if free > 0 {
            let body = json!({"verifier": verifier.id.load(Ordering::Relaxed), "max": free});
            match verifier.post("/v1/work/lease", &body).await {
                Ok(reply) => {
                    for item in reply.get("work").and_then(Value::as_array).cloned().unwrap_or_default() {
                        let permit = running.clone().acquire_owned().await.expect("open semaphore");
                        let verifier = verifier.clone();
                        leased += 1;
                        tokio::spawn(async move {
                            verifier.run_one(item).await;
                            drop(permit);
                        });
                    }
                }
                Err(e) if e.contains("register this verifier") => {
                    let _ = verifier.register().await;
                }
                Err(e) => log(format!("leasing failed: {e}")),
            }
        }
        let busy = running.available_permits() < slots;
        if leased > 0 || busy {
            idle_since = Instant::now();
        } else if let Some(limit) = verifier.options.idle_exit {
            if idle_since.elapsed() >= limit {
                log(format!(
                    "idle for {} s: {} judged, {} released",
                    limit.as_secs(),
                    verifier.judged.load(Ordering::Relaxed),
                    verifier.released.load(Ordering::Relaxed)
                ));
                return 0;
            }
        }
        let wait = if leased > 0 { Duration::from_millis(100) } else { verifier.options.poll };
        tokio::time::sleep(wait).await;
    }
}
