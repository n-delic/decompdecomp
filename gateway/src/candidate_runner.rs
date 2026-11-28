//! Parallel candidate compilation/evaluation coordinator (`candidate-runner` subcommand).
//!
//! Reads a JSON batch of command jobs from stdin, executes them concurrently across CPU cores
//! using `tokio::process::Command` with semaphore-bounded concurrency and strict per-job
//! timeouts, and writes structured JSON results to stdout in input order.

use std::collections::HashMap;
use std::io::{self, Read};
use std::path::Path;
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::Command;
use tokio::sync::Semaphore;

#[derive(Debug, Deserialize)]
pub struct BatchRequest {
    pub jobs: Vec<JobSpec>,
    #[serde(default)]
    pub max_concurrency: Option<usize>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct JobSpec {
    #[serde(default)]
    pub id: Option<String>,
    pub argv: Vec<String>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub env: HashMap<String, String>,
    #[serde(default)]
    pub stdin: Option<String>,
    #[serde(default)]
    pub source_file: Option<SourceFile>,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct SourceFile {
    pub path: String,
    pub content: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct BatchResponse {
    pub results: Vec<JobResult>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct JobResult {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    pub exit_code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub elapsed_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

fn error_result(id: Option<String>, started: Instant, msg: String) -> JobResult {
    JobResult {
        id,
        exit_code: None,
        stdout: String::new(),
        stderr: String::new(),
        timed_out: false,
        elapsed_ms: started.elapsed().as_millis() as u64,
        error: Some(msg),
    }
}

async fn run_one_job(job: JobSpec) -> JobResult {
    let started = Instant::now();
    if job.argv.is_empty() {
        return error_result(job.id, started, "empty argv".to_string());
    }

    if let Some(sf) = &job.source_file {
        let path = Path::new(&sf.path);
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                if let Err(err) = std::fs::create_dir_all(parent) {
                    return error_result(
                        job.id,
                        started,
                        format!("failed to create parent dir for {}: {err}", sf.path),
                    );
                }
            }
        }
        if let Err(err) = std::fs::write(path, sf.content.as_bytes()) {
            return error_result(
                job.id,
                started,
                format!("failed to write source file {}: {err}", sf.path),
            );
        }
    }

    let mut cmd = Command::new(&job.argv[0]);
    cmd.args(&job.argv[1..]);
    if let Some(cwd) = &job.cwd {
        cmd.current_dir(cwd);
    }
    if !job.env.is_empty() {
        cmd.envs(&job.env);
    }
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    if job.stdin.is_some() {
        cmd.stdin(Stdio::piped());
    } else {
        cmd.stdin(Stdio::null());
    }
    cmd.kill_on_drop(true);

    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(err) => {
            return error_result(job.id, started, format!("failed to spawn {}: {err}", job.argv[0]));
        }
    };

    if let Some(input) = job.stdin {
        if let Some(mut stdin_pipe) = child.stdin.take() {
            let _ = stdin_pipe.write_all(input.as_bytes()).await;
            let _ = stdin_pipe.shutdown().await;
        }
    }

    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();

    let stdout_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        if let Some(mut pipe) = stdout_pipe.take() {
            let _ = pipe.read_to_end(&mut buf).await;
        }
        buf
    });
    let stderr_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        if let Some(mut pipe) = stderr_pipe.take() {
            let _ = pipe.read_to_end(&mut buf).await;
        }
        buf
    });

    let (exit_code, timed_out) = match job.timeout_ms {
        Some(ms) => match tokio::time::timeout(Duration::from_millis(ms), child.wait()).await {
            Ok(Ok(status)) => (status.code(), false),
            Ok(Err(err)) => {
                stdout_task.abort();
                stderr_task.abort();
                return error_result(job.id, started, format!("wait failed: {err}"));
            }
            Err(_) => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                (None, true)
            }
        },
        None => match child.wait().await {
            Ok(status) => (status.code(), false),
            Err(err) => {
                stdout_task.abort();
                stderr_task.abort();
                return error_result(job.id, started, format!("wait failed: {err}"));
            }
        },
    };

    let stdout_bytes = tokio::time::timeout(Duration::from_millis(500), stdout_task)
        .await
        .ok()
        .and_then(Result::ok)
        .unwrap_or_default();
    let stderr_bytes = tokio::time::timeout(Duration::from_millis(500), stderr_task)
        .await
        .ok()
        .and_then(Result::ok)
        .unwrap_or_default();

    JobResult {
        id: job.id,
        exit_code,
        stdout: String::from_utf8_lossy(&stdout_bytes).into_owned(),
        stderr: String::from_utf8_lossy(&stderr_bytes).into_owned(),
        timed_out,
        elapsed_ms: started.elapsed().as_millis() as u64,
        error: None,
    }
}

pub async fn run_batch(req: BatchRequest) -> BatchResponse {
    if req.jobs.is_empty() {
        return BatchResponse {
            results: Vec::new(),
        };
    }

    let concurrency = req
        .max_concurrency
        .filter(|&n| n > 0)
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(4)
        });
    let sem = Arc::new(Semaphore::new(concurrency));

    let mut handles = Vec::with_capacity(req.jobs.len());
    for job in req.jobs {
        let sem = sem.clone();
        handles.push(tokio::spawn(async move {
            let _permit = sem.acquire_owned().await.expect("semaphore open");
            run_one_job(job).await
        }));
    }

    let mut results = Vec::with_capacity(handles.len());
    for handle in handles {
        match handle.await {
            Ok(res) => results.push(res),
            Err(err) => results.push(JobResult {
                id: None,
                exit_code: None,
                stdout: String::new(),
                stderr: String::new(),
                timed_out: false,
                elapsed_ms: 0,
                error: Some(format!("task join error: {err}")),
            }),
        }
    }

    BatchResponse { results }
}

pub fn serve() -> i32 {
    let mut input = String::new();
    if let Err(err) = io::stdin().read_to_string(&mut input) {
        eprintln!("candidate-runner: failed to read stdin: {err}");
        return 1;
    }
    let req: BatchRequest = match serde_json::from_str(&input) {
        Ok(req) => req,
        Err(err) => {
            eprintln!("candidate-runner: invalid JSON request: {err}");
            return 2;
        }
    };

    let rt = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(err) => {
            eprintln!("candidate-runner: failed to start runtime: {err}");
            return 1;
        }
    };

    let resp = rt.block_on(run_batch(req));
    match serde_json::to_string(&resp) {
        Ok(json) => {
            println!("{json}");
            0
        }
        Err(err) => {
            eprintln!("candidate-runner: failed to serialize response: {err}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn parallel_execution_and_exit_codes() {
        let req = BatchRequest {
            max_concurrency: Some(4),
            jobs: vec![
                JobSpec {
                    id: Some("ok".into()),
                    argv: vec!["sh".into(), "-c".into(), "echo hello; echo err >&2".into()],
                    cwd: None,
                    env: HashMap::new(),
                    stdin: None,
                    source_file: None,
                    timeout_ms: Some(5000),
                },
                JobSpec {
                    id: Some("fail".into()),
                    argv: vec!["sh".into(), "-c".into(), "exit 7".into()],
                    cwd: None,
                    env: HashMap::new(),
                    stdin: None,
                    source_file: None,
                    timeout_ms: Some(5000),
                },
                JobSpec {
                    id: Some("env_and_stdin".into()),
                    argv: vec!["sh".into(), "-c".into(), "cat; printf ':%s' \"$MY_VAR\"".into()],
                    cwd: None,
                    env: HashMap::from([("MY_VAR".into(), "42".into())]),
                    stdin: Some("abc".into()),
                    source_file: None,
                    timeout_ms: Some(5000),
                },
            ],
        };

        let resp = run_batch(req).await;
        assert_eq!(resp.results.len(), 3);

        assert_eq!(resp.results[0].id.as_deref(), Some("ok"));
        assert_eq!(resp.results[0].exit_code, Some(0));
        assert_eq!(resp.results[0].stdout.trim(), "hello");
        assert_eq!(resp.results[0].stderr.trim(), "err");
        assert!(!resp.results[0].timed_out);
        assert!(resp.results[0].error.is_none());

        assert_eq!(resp.results[1].id.as_deref(), Some("fail"));
        assert_eq!(resp.results[1].exit_code, Some(7));
        assert!(!resp.results[1].timed_out);

        assert_eq!(resp.results[2].id.as_deref(), Some("env_and_stdin"));
        assert_eq!(resp.results[2].exit_code, Some(0));
        assert_eq!(resp.results[2].stdout, "abc:42");
    }

    #[tokio::test]
    async fn per_job_timeout_kills_slow_process() {
        let req = BatchRequest {
            max_concurrency: Some(2),
            jobs: vec![
                JobSpec {
                    id: Some("slow".into()),
                    argv: vec!["sh".into(), "-c".into(), "sleep 5".into()],
                    cwd: None,
                    env: HashMap::new(),
                    stdin: None,
                    source_file: None,
                    timeout_ms: Some(150),
                },
                JobSpec {
                    id: Some("fast".into()),
                    argv: vec!["sh".into(), "-c".into(), "echo done".into()],
                    cwd: None,
                    env: HashMap::new(),
                    stdin: None,
                    source_file: None,
                    timeout_ms: Some(5000),
                },
            ],
        };

        let started = Instant::now();
        let resp = run_batch(req).await;
        assert!(started.elapsed() < Duration::from_secs(3));
        assert_eq!(resp.results.len(), 2);

        assert!(resp.results[0].timed_out);
        assert_eq!(resp.results[0].exit_code, None);

        assert!(!resp.results[1].timed_out);
        assert_eq!(resp.results[1].exit_code, Some(0));
        assert_eq!(resp.results[1].stdout.trim(), "done");
    }

    #[tokio::test]
    async fn semaphore_enforces_concurrency_limit() {
        // 3 jobs each sleeping 120ms with max_concurrency=1 must take at least ~300ms.
        let make_job = |id: &str| JobSpec {
            id: Some(id.into()),
            argv: vec!["sh".into(), "-c".into(), "sleep 0.12".into()],
            cwd: None,
            env: HashMap::new(),
            stdin: None,
            source_file: None,
            timeout_ms: Some(5000),
        };
        let req = BatchRequest {
            max_concurrency: Some(1),
            jobs: vec![make_job("a"), make_job("b"), make_job("c")],
        };

        let started = Instant::now();
        let resp = run_batch(req).await;
        let elapsed = started.elapsed();

        assert_eq!(resp.results.len(), 3);
        assert!(resp.results.iter().all(|r| r.exit_code == Some(0)));
        assert!(
            elapsed >= Duration::from_millis(300),
            "expected serial execution >= 300ms, got {:?}",
            elapsed
        );
    }

    #[tokio::test]
    async fn writes_source_file_before_running() {
        let dir = std::env::temp_dir().join(format!("decomp_cr_test_{}", std::process::id()));
        let file = dir.join("cand.c");
        let file_str = file.to_string_lossy().into_owned();

        let req = BatchRequest {
            max_concurrency: Some(1),
            jobs: vec![JobSpec {
                id: Some("src".into()),
                argv: vec!["cat".into(), file_str.clone()],
                cwd: None,
                env: HashMap::new(),
                stdin: None,
                source_file: Some(SourceFile {
                    path: file_str.clone(),
                    content: "int f(void) { return 1; }\n".into(),
                }),
                timeout_ms: Some(5000),
            }],
        };

        let resp = run_batch(req).await;
        let _ = std::fs::remove_dir_all(&dir);

        assert_eq!(resp.results[0].exit_code, Some(0));
        assert_eq!(resp.results[0].stdout, "int f(void) { return 1; }\n");
    }
}
