//! Hosted mode: decompdecomp's shared projects served by this program alone, for agents on other machines.
//!
//!     decomp-mcp-gateway hosted serve --dir DIR [--listen 127.0.0.1:8080]
//!     decomp-mcp-gateway hosted add-user --dir DIR --name NAME [--role agent|verifier|admin]
//!
//! DIR holds `server.sqlite3` (users and their tokens) and `projects/<slug>.sqlite3`, one small file per project
//! made by `decomp hosted-export` (src/decompnet/hosted_export.py): each function's name, address, size, target bytes,
//! relocations, assembly and compiler, the project's context headers, and its status. No attempt history,
//! transcripts or logs.
//!
//! Agents run on their own machines with their own harness. They connect to `/p/<slug>/mcp`, fetch functions,
//! claim them, compile on their own computer and submit the C with the score they measured. This program never
//! compiles: a submission is "reported" until a verifier (a trusted machine with the toolchains, such as the
//! decompdecomp app) compiles it and records the verdict through `/v1/p/<slug>/...`. Only verified scores change a
//! function: a verified 0 marks it matched. Here, unlike the desktop app, this program is the only thing that
//! claims, so a claim is one atomic update of the project's file.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, Path as UrlPath, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::sync::Semaphore;

pub const FORMAT: &str = "1";
/// The verification packs' format (src/decompnet/hosted_pack.py FORMAT).
const PACK_FORMAT: &str = "1";
/// How a published project merges into the live one: the same script the Python exporter runs.
const MERGE_SQL: &str = include_str!("../../src/decompnet/hosted_merge.sql");
/// The largest project or pack a publish may upload.
const MAX_UPLOAD: usize = 1024 * 1024 * 1024;
const CLAIM_SECONDS: f64 = 30.0 * 60.0;
const MAX_CLAIMS: i64 = 6;
const MAX_SOURCE: usize = 256 * 1024;
const MAX_NOTE: usize = 4000;
const PER_USER: usize = 16;
const PROTOCOL_FALLBACK: &str = "2025-06-18";
/// How long a verifier holds a submission it leased before another may take it.
const LEASE_SECONDS: f64 = 10.0 * 60.0;
/// Verifiers that couldn't judge a submission this many times: it is marked unverifiable.
const MAX_TRIES: i64 = 3;
/// How long the server trusts what it remembers of users, access and its policy: changes made from the command line
/// (another process) apply within this; changes over HTTP at once.
const REMEMBER: Duration = Duration::from_secs(10);
/// Tool calls a user may make a minute, unless `serve --rate` says otherwise.
pub const RATE: f64 = 600.0;

const PROJECT_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS claims(function_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, user_name TEXT NOT NULL,
    token TEXT NOT NULL, claimed_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS claims_user ON claims(user_id, expires_at);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT, function_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, user_name TEXT NOT NULL, source TEXT NOT NULL, reported_score INTEGER NOT NULL,
    created_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'reported', verified_score INTEGER, detail TEXT,
    verified_at REAL, verifier TEXT, lease_verifier INTEGER, lease_until REAL, tries INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS submissions_state ON submissions(state, id);
CREATE INDEX IF NOT EXISTS submissions_verified ON submissions(verified_at);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, function_id INTEGER, user_name TEXT NOT NULL,
    text TEXT NOT NULL, created_at REAL NOT NULL);
";

const SERVER_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE, role TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS access(user_id INTEGER NOT NULL, slug TEXT NOT NULL, PRIMARY KEY(user_id, slug));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS verifiers(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL,
    projects TEXT NOT NULL, toolchains TEXT NOT NULL, slots INTEGER NOT NULL, version TEXT NOT NULL,
    registered_at REAL NOT NULL, last_seen REAL NOT NULL, UNIQUE(user_id, name));
";

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn hash_token(token: &str) -> String {
    hex(&Sha256::digest(token.as_bytes()))
}

fn new_token(bytes: usize) -> String {
    let mut buffer = vec![0u8; bytes];
    getrandom::getrandom(&mut buffer).expect("random bytes");
    hex(&buffer)
}

/// Assembly, C and headers are stored zlib-compressed (hosted_export.py): a project stays a few MB.
fn pack(text: &str) -> Vec<u8> {
    use std::io::Write;
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::best());
    encoder.write_all(text.as_bytes()).expect("in memory");
    encoder.finish().expect("in memory")
}

/// A stored text column: compressed (a blob), plain text, or nothing.
fn unpack(value: rusqlite::types::ValueRef) -> Option<String> {
    use rusqlite::types::ValueRef;
    use std::io::Read;
    match value {
        ValueRef::Blob(bytes) => {
            let mut text = String::new();
            flate2::read::ZlibDecoder::new(bytes).read_to_string(&mut text).ok()?;
            Some(text)
        }
        ValueRef::Text(text) => String::from_utf8(text.to_vec()).ok(),
        _ => None,
    }
}

fn open(path: &Path) -> rusqlite::Result<Connection> {
    let db = Connection::open(path)?;
    db.busy_timeout(Duration::from_secs(5))?;
    db.pragma_update(None, "journal_mode", "WAL")?;
    db.pragma_update(None, "synchronous", "NORMAL")?;
    db.pragma_update(None, "foreign_keys", "ON")?;
    Ok(db)
}

/// A tool's failure the agent can act on: the text is the answer.
#[derive(Debug)]
pub struct Problem(pub String);

impl From<rusqlite::Error> for Problem {
    fn from(error: rusqlite::Error) -> Problem {
        Problem(format!("the project database failed: {error}"))
    }
}

type Answer = Result<String, Problem>;

fn problem<T>(text: impl Into<String>) -> Result<T, Problem> {
    Err(Problem(text.into()))
}

#[derive(Clone, Debug)]
pub struct User {
    pub id: i64,
    pub name: String,
    pub role: String,
}

// ---------------------------------------------------------------------------------------------------- one project

pub struct Project {
    pub slug: String,
    path: PathBuf,
    /// Claims, submissions and notes: one writer, so each is one atomic update.
    db: Mutex<Connection>,
    /// Reads run beside it on their own connections (WAL readers never wait for the writer).
    readers: Mutex<Vec<Connection>>,
}

const READERS_KEPT: usize = 32;

struct Function {
    id: i64,
    name: String,
    unit: Option<String>,
    vram: Option<i64>,
    size: Option<i64>,
    n_insns: Option<i64>,
    compiler: String,
    status: String,
    best_score: Option<i64>,
}

const FUNCTION_COLUMNS: &str = "id,name,unit,vram,size,n_insns,compiler,status,best_score";

fn function_row(r: &rusqlite::Row) -> rusqlite::Result<Function> {
    Ok(Function {
        id: r.get(0)?,
        name: r.get(1)?,
        unit: r.get(2)?,
        vram: r.get(3)?,
        size: r.get(4)?,
        n_insns: r.get(5)?,
        compiler: r.get::<_, Option<String>>(6)?.unwrap_or_default(),
        status: r.get(7)?,
        best_score: r.get(8)?,
    })
}

fn address(vram: Option<i64>) -> String {
    vram.map(|v| format!("0x{v:08X}")).unwrap_or_else(|| "(no address)".into())
}

fn compiler_text(compiler: &str) -> String {
    let value: Value = serde_json::from_str(compiler).unwrap_or(Value::Null);
    let toolchain = value.get("toolchain").and_then(Value::as_str).unwrap_or("(project default)");
    let flags: Vec<&str> =
        value.get("flags").and_then(Value::as_array).map(|f| f.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
    format!("{toolchain} {}", flags.join(" ")).trim().to_string()
}

impl Project {
    pub fn open(path: &Path, slug: &str) -> Result<Project, String> {
        let db = open(path).map_err(|e| e.to_string())?;
        let format: Option<String> = db
            .query_row("SELECT value FROM meta WHERE key='format'", [], |r| r.get(0))
            .optional()
            .map_err(|e| format!("{slug} isn't a hosted project ({e})"))?;
        if format.as_deref() != Some(FORMAT) {
            return Err(format!("{slug} is hosted format {format:?}; this program reads format {FORMAT}"));
        }
        db.execute_batch(PROJECT_SCHEMA).map_err(|e| e.to_string())?;
        // Files from before verifiers leased submissions.
        let columns: Vec<String> = db
            .prepare("PRAGMA table_info(submissions)")
            .and_then(|mut s| s.query_map([], |r| r.get::<_, String>(1))?.collect())
            .map_err(|e| e.to_string())?;
        for (column, ddl) in [("lease_verifier", "INTEGER"), ("lease_until", "REAL"), ("tries", "INTEGER NOT NULL DEFAULT 0")] {
            if !columns.iter().any(|c| c == column) {
                db.execute_batch(&format!("ALTER TABLE submissions ADD COLUMN {column} {ddl}")).map_err(|e| e.to_string())?;
            }
        }
        db.execute_batch("CREATE INDEX IF NOT EXISTS submissions_lease ON submissions(state, lease_until, id)")
            .map_err(|e| e.to_string())?;
        Ok(Project { slug: slug.to_string(), path: path.to_path_buf(), db: Mutex::new(db), readers: Mutex::new(Vec::new()) })
    }

    /// Run `work` on a reading connection of its own.
    fn read<T>(&self, work: impl FnOnce(&Connection) -> Result<T, Problem>) -> Result<T, Problem> {
        let reader = self.readers.lock().unwrap().pop();
        let reader = match reader {
            Some(reader) => reader,
            None => {
                let flags = rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX;
                let reader = Connection::open_with_flags(&self.path, flags)?;
                reader.busy_timeout(Duration::from_secs(5))?;
                reader
            }
        };
        let out = work(&reader);
        let mut readers = self.readers.lock().unwrap();
        if readers.len() < READERS_KEPT {
            readers.push(reader);
        }
        out
    }

    fn meta(db: &Connection, key: &str) -> Option<String> {
        db.query_row("SELECT value FROM meta WHERE key=?", [key], |r| r.get(0)).optional().ok().flatten()
    }

    /// A function by name, name@unit or address (0x80001234).
    fn function(db: &Connection, what: &str) -> Result<Function, Problem> {
        let what = what.trim();
        if what.is_empty() {
            return problem("Name a function (its name, name@unit or address).");
        }
        let sql = |filter: &str| format!("SELECT {FUNCTION_COLUMNS} FROM functions WHERE {filter} ORDER BY id LIMIT 2");
        let found: Vec<Function> = if let Some(hexa) = what.strip_prefix("0x").or_else(|| what.strip_prefix("0X")) {
            let Ok(vram) = i64::from_str_radix(hexa, 16) else { return problem(format!("{what} isn't an address")) };
            db.prepare_cached(&sql("vram=?"))?.query_map([vram], function_row)?.collect::<Result<_, _>>()?
        } else if let Some((name, unit)) = what.split_once('@') {
            db.prepare_cached(&sql("name=? AND unit=?"))?.query_map([name, unit], function_row)?.collect::<Result<_, _>>()?
        } else {
            db.prepare_cached(&sql("name=?"))?.query_map([what], function_row)?.collect::<Result<_, _>>()?
        };
        match found.len() {
            0 => problem(format!("No function {what:?} in this project. list_functions shows them.")),
            1 => Ok(found.into_iter().next().unwrap()),
            _ => problem(format!("Several functions are called {what:?}: name one as name@unit or by its address.")),
        }
    }

    fn live_claim(db: &Connection, function: i64) -> rusqlite::Result<Option<(i64, String, String, f64)>> {
        db.query_row(
            "SELECT user_id,user_name,token,expires_at FROM claims WHERE function_id=? AND expires_at>?",
            params![function, now()],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()
    }

    pub fn call(&self, user: &User, tool: &str, args: &Value) -> Answer {
        let text = |key: &str| args.get(key).and_then(Value::as_str).unwrap_or("").to_string();
        match tool {
            "project_info" => return self.read(|db| self.project_info(db)),
            "list_functions" => {
                let status = args.get("status").and_then(Value::as_str).unwrap_or("open").to_string();
                let limit = args.get("limit").and_then(Value::as_i64).unwrap_or(40).clamp(1, 1000);
                return self.read(|db| self.list_functions(db, &status, limit));
            }
            "get_function" => {
                let section = args.get("section").and_then(Value::as_str).unwrap_or("summary").to_string();
                return self.read(|db| self.get_function(db, &text("function"), &section));
            }
            "get_claims" => return self.read(|db| self.get_claims(db, user)),
            _ => {}
        }
        let mut db = self.db.lock().unwrap();
        match tool {
            "claim_function" => {
                let strategy = args.get("strategy").and_then(Value::as_str).unwrap_or("easiest").to_string();
                self.claim(&mut db, user, &text("function"), &strategy)
            }
            "release_function" => self.release(&mut db, user, &text("function"), &text("claim_token"), &text("notes")),
            "submit_result" => {
                let Some(score) = args.get("score").and_then(Value::as_i64).filter(|s| *s >= 0) else {
                    return problem("score is the difference you measured: 0 for byte-identical, else a positive number.");
                };
                self.submit(&mut db, user, &text("function"), &text("claim_token"), &text("source"), score)
            }
            "add_note" => self.add_note(&db, user, &text("function"), &text("text")),
            _ => problem(format!("unknown tool {tool:?}")),
        }
    }

    fn project_info(&self, db: &Connection) -> Answer {
        let (total, matched, open): (i64, i64, i64) = db.query_row(
            "SELECT COUNT(*), SUM(status='matched'), SUM(status='open') FROM functions",
            [],
            |r| Ok((r.get(0)?, r.get::<_, Option<i64>>(1)?.unwrap_or(0), r.get::<_, Option<i64>>(2)?.unwrap_or(0))),
        )?;
        let pending: i64 =
            db.query_row("SELECT COUNT(*) FROM submissions WHERE state='reported'", [], |r| r.get(0))?;
        let name = Self::meta(db, "name").unwrap_or_else(|| self.slug.clone());
        let platform = Self::meta(db, "platform").unwrap_or_default();
        let compiler = Self::meta(db, "compiler").map(|c| compiler_text(&c)).unwrap_or_default();
        let mut out = format!(
            "{name} ({})\nplatform: {platform}\ncompiler: {compiler}\nprogress: {matched}/{total} functions matched; \
             {open} open; {pending} submissions waiting for verification\n\
             You compile on your own computer: get_function(section=\"target\") gives the bytes to match, \
             section=\"context\" the header, section=\"assembly\" the instructions. Submit your C and the score you \
             measured with submit_result; a match counts once a verifier has compiled it too.\n\
             Next: get_claims() to recover held work, or claim_function().",
            self.slug
        );
        if let Some(wish) = Self::meta(db, "instructions").filter(|w| !w.is_empty()) {
            out += &format!("\nThe project owner's instructions (they come first):\n{wish}");
        }
        Ok(out)
    }

    fn list_functions(&self, db: &Connection, status: &str, limit: i64) -> Answer {
        let mut statement = db.prepare_cached(
            "SELECT f.name,f.vram,f.n_insns,f.status,f.best_score,c.user_name FROM functions f \
             LEFT JOIN claims c ON c.function_id=f.id AND c.expires_at>?2 WHERE ?1='all' OR f.status=?1 \
             ORDER BY f.vram LIMIT ?3",
        )?;
        let lines: Vec<String> = statement
            .query_map(params![status, now(), limit], |r| {
                let mut line = format!(
                    "- {} @{}: {} insns, {}",
                    r.get::<_, String>(0)?,
                    address(r.get(1)?),
                    r.get::<_, Option<i64>>(2)?.map(|n| n.to_string()).unwrap_or_else(|| "?".into()),
                    r.get::<_, String>(3)?
                );
                if let Some(best) = r.get::<_, Option<i64>>(4)? {
                    line += &format!(", best score {best}");
                }
                if let Some(by) = r.get::<_, Option<String>>(5)? {
                    line += &format!(", claimed by {by}");
                }
                Ok(line)
            })?
            .collect::<Result<_, _>>()?;
        if lines.is_empty() {
            return Ok(format!("No {status} functions."));
        }
        Ok(lines.join("\n"))
    }

    fn get_function(&self, db: &Connection, what: &str, section: &str) -> Answer {
        let f = Self::function(db, what)?;
        let one = |sql: &str| -> rusqlite::Result<Option<String>> {
            db.query_row(sql, [f.id], |r| Ok(unpack(r.get_ref(0)?))).optional().map(Option::flatten)
        };
        let summary = || -> Result<String, Problem> {
            let claim = Self::live_claim(db, f.id)?;
            Ok(format!(
                "{} @{}  unit {}\n{} bytes, {} instructions; status {}{}\ncompiler: {}{}",
                f.name,
                address(f.vram),
                f.unit.clone().unwrap_or_else(|| "(main image)".into()),
                f.size.map(|s| s.to_string()).unwrap_or_else(|| "?".into()),
                f.n_insns.map(|s| s.to_string()).unwrap_or_else(|| "?".into()),
                f.status,
                f.best_score.map(|s| format!(", best verified score {s}")).unwrap_or_default(),
                compiler_text(&f.compiler),
                claim.map(|(_, by, _, _)| format!("\nclaimed by {by}")).unwrap_or_default()
            ))
        };
        let assembly = || one("SELECT asm FROM functions WHERE id=?").map(|a| a.unwrap_or_else(|| "(no assembly)".into()));
        let target = || -> Result<String, Problem> {
            let (bytes, relocs): (Option<Vec<u8>>, Option<String>) =
                db.query_row("SELECT target,relocs FROM functions WHERE id=?", [f.id], |r| Ok((r.get(0)?, r.get(1)?)))?;
            let bytes = bytes.unwrap_or_default();
            Ok(format!(
                "target ({} bytes, hex): {}\nrelocations: {}\nYour compiled function must equal these bytes, \
                 relocated symbols aside; the score is how many differ (0 = byte-identical).",
                bytes.len(),
                hex(&bytes),
                relocs.unwrap_or_else(|| "[]".into())
            ))
        };
        let context = || -> Result<String, Problem> {
            let unit = f.unit.clone().unwrap_or_default();
            let header: Option<(i64, String)> = db
                .query_row(
                    "SELECT version,header FROM contexts WHERE unit IN (?,'') ORDER BY unit='' LIMIT 1",
                    [&unit],
                    |r| Ok((r.get(0)?, unpack(r.get_ref(1)?).unwrap_or_default())),
                )
                .optional()?;
            Ok(match header {
                Some((version, header)) => format!("// context.h version {version}\n{header}"),
                None => "(no context header)".into(),
            })
        };
        let source = |column: &str| {
            one(&format!("SELECT {column} FROM functions WHERE id=?"))
                .map(|s| s.map(|s| format!("```c\n{s}\n```")).unwrap_or_else(|| "(none yet)".into()))
        };
        let notes = || -> Result<String, Problem> {
            let mut statement = db.prepare_cached(
                "SELECT user_name,text FROM notes WHERE function_id=? ORDER BY id DESC LIMIT 20",
            )?;
            let lines: Vec<String> = statement
                .query_map([f.id], |r| Ok(format!("- {}: {}", r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                .collect::<Result<_, _>>()?;
            Ok(if lines.is_empty() { "(no notes)".into() } else { lines.join("\n") })
        };
        Ok(match section {
            "summary" => format!("{}\n\nassembly:\n{}", summary()?, assembly()?),
            "assembly" => assembly()?,
            "target" => target()?,
            "context" => context()?,
            "best" => source("COALESCE(best_source,matched_source)")?,
            "matched" => source("matched_source")?,
            "notes" => notes()?,
            "all" => format!(
                "{}\n\nassembly:\n{}\n\n{}\n\ncontext:\n{}\n\nbest verified C:\n{}\n\nnotes:\n{}",
                summary()?,
                assembly()?,
                target()?,
                context()?,
                source("COALESCE(best_source,matched_source)")?,
                notes()?
            ),
            other => {
                return problem(format!(
                    "No section {other:?}: summary, assembly, target, context, best, matched, notes or all."
                ))
            }
        })
    }

    fn receipt(db: &Connection, f: &Function, token: &str, expires_at: f64) -> Answer {
        let (asm, best): (Option<String>, Option<String>) = db.query_row(
            "SELECT asm,best_source FROM functions WHERE id=?",
            [f.id],
            |r| Ok((unpack(r.get_ref(0)?), unpack(r.get_ref(1)?))),
        )?;
        Ok(format!(
            "CLAIMED {} @{} ({} bytes, {} instructions), unit {}\nclaim_token: {token}\nexpires in {} minutes \
             (each submit_result renews it)\ncompiler: {}\n{}\nassembly:\n{}\n\nNext: get_function(function=\"{}\", \
             section=\"target\") and section=\"context\", compile on your computer, then submit_result.",
            f.name,
            address(f.vram),
            f.size.map(|s| s.to_string()).unwrap_or_else(|| "?".into()),
            f.n_insns.map(|s| s.to_string()).unwrap_or_else(|| "?".into()),
            f.unit.clone().unwrap_or_else(|| "(main image)".into()),
            ((expires_at - now()) / 60.0).round() as i64,
            compiler_text(&f.compiler),
            best.map(|b| format!("best verified C so far (score {}):\n```c\n{b}\n```", f.best_score.unwrap_or(-1)))
                .unwrap_or_else(|| "No verified C yet.".into()),
            asm.unwrap_or_else(|| "(no assembly)".into()),
            f.name
        ))
    }

    fn claim(&self, db: &mut Connection, user: &User, what: &str, strategy: &str) -> Answer {
        let order = match strategy {
            "" | "easiest" => "f.n_insns ASC, f.id",
            "hardest" => "f.n_insns DESC, f.id",
            "closest" => "f.best_score IS NULL, f.best_score ASC, f.n_insns ASC",
            "random" => "random()",
            other => return problem(format!("No strategy {other:?}: easiest, hardest, closest or random.")),
        };
        let at = now();
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let f = if what.trim().is_empty() {
            let sql = format!(
                "SELECT {} FROM functions f LEFT JOIN claims c ON c.function_id=f.id AND c.expires_at>? \
                 WHERE f.status='open' AND c.function_id IS NULL ORDER BY {order} LIMIT 1",
                FUNCTION_COLUMNS.split(',').map(|c| format!("f.{c}")).collect::<Vec<_>>().join(",")
            );
            match tx.query_row(&sql, [at], function_row).optional()? {
                Some(f) => f,
                None => return Ok("Nothing to claim: every open function is claimed or matched.".into()),
            }
        } else {
            let f = Self::function(&tx, what)?;
            if f.status != "open" {
                return problem(format!("{} is {}: claim another function.", f.name, f.status));
            }
            if let Some((holder, by, token, _)) = Self::live_claim(&tx, f.id)? {
                if holder != user.id {
                    return problem(format!("{} is claimed by {by}: claim another function.", f.name));
                }
                let renewed = at + CLAIM_SECONDS; // claiming one you hold again: the same claim, renewed
                tx.execute("UPDATE claims SET expires_at=? WHERE function_id=?", params![renewed, f.id])?;
                tx.commit()?;
                return Self::receipt(db, &f, &token, renewed);
            }
            f
        };
        let held: i64 = tx.query_row(
            "SELECT COUNT(*) FROM claims WHERE user_id=? AND expires_at>?",
            params![user.id, at],
            |r| r.get(0),
        )?;
        if held >= MAX_CLAIMS {
            return problem(format!(
                "You hold {held} claims in this project: submit or release one first (get_claims lists them)."
            ));
        }
        let token = new_token(12);
        let expires = at + CLAIM_SECONDS;
        tx.execute(
            "INSERT OR REPLACE INTO claims(function_id,user_id,user_name,token,claimed_at,expires_at) VALUES(?,?,?,?,?,?)",
            params![f.id, user.id, user.name, token, at, expires],
        )?;
        tx.commit()?;
        Self::receipt(db, &f, &token, expires)
    }

    fn get_claims(&self, db: &Connection, user: &User) -> Answer {
        let mut statement = db.prepare_cached(
            "SELECT f.name,f.unit,f.status,c.token,c.expires_at FROM claims c JOIN functions f ON f.id=c.function_id \
             WHERE c.user_id=? AND c.expires_at>? ORDER BY f.id",
        )?;
        let claims: Vec<Value> = statement
            .query_map(params![user.id, now()], |r| {
                Ok(json!({"function": r.get::<_, String>(0)?, "unit": r.get::<_, Option<String>>(1)?,
                          "status": r.get::<_, String>(2)?, "claim_token": r.get::<_, String>(3)?,
                          "expires_at": r.get::<_, f64>(4)?}))
            })?
            .collect::<Result<_, _>>()?;
        Ok(json!({"claims": claims, "scope": "Only your live claims in this project; nothing changed."}).to_string())
    }

    /// The caller's live claim on a function, checked against its token.
    fn held(db: &Connection, user: &User, what: &str, token: &str) -> Result<Function, Problem> {
        let f = Self::function(db, what)?;
        match Self::live_claim(db, f.id)? {
            Some((holder, _, held, _)) if holder == user.id && (token.is_empty() || held == token) => Ok(f),
            Some((holder, _, _, _)) if holder == user.id => {
                problem(format!("That claim_token is old: get_claims gives the current one for {}.", f.name))
            }
            _ => problem(format!("You don't hold {}: claim it first.", f.name)),
        }
    }

    fn release(&self, db: &mut Connection, user: &User, what: &str, token: &str, notes: &str) -> Answer {
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let f = Self::held(&tx, user, what, token)?;
        tx.execute("DELETE FROM claims WHERE function_id=?", [f.id])?;
        let notes = notes.trim();
        if !notes.is_empty() {
            tx.execute(
                "INSERT INTO notes(function_id,user_name,text,created_at) VALUES(?,?,?,?)",
                params![f.id, user.name, &notes[..notes.len().min(MAX_NOTE)], now()],
            )?;
        }
        tx.commit()?;
        Ok(format!("Released {}. claim_function() for the next one.", f.name))
    }

    fn submit(&self, db: &mut Connection, user: &User, what: &str, token: &str, source: &str, score: i64) -> Answer {
        if source.trim().is_empty() {
            return problem("Send the complete C of the function as source.");
        }
        if source.len() > MAX_SOURCE {
            return problem(format!("source is over {} KiB: send only this function and what it needs.", MAX_SOURCE / 1024));
        }
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let f = Self::held(&tx, user, what, token)?;
        let at = now();
        tx.execute(
            "INSERT INTO submissions(function_id,user_id,user_name,source,reported_score,created_at) VALUES(?,?,?,?,?,?)",
            params![f.id, user.id, user.name, source, score, at],
        )?;
        let id = tx.last_insert_rowid();
        tx.execute("UPDATE claims SET expires_at=? WHERE function_id=?", params![at + CLAIM_SECONDS, f.id])?;
        tx.commit()?;
        Ok(if score == 0 {
            format!(
                "Submission #{id} recorded: you measured a match for {}. It counts once a verifier has compiled it too; \
                 keep your claim until then or release it.",
                f.name
            )
        } else {
            format!(
                "Submission #{id} recorded for {} with score {score}; your claim is renewed for 30 minutes. Keep \
                 improving, or release it with notes on what you tried.",
                f.name
            )
        })
    }

    fn add_note(&self, db: &Connection, user: &User, what: &str, text: &str) -> Answer {
        let text = text.trim();
        if text.is_empty() {
            return problem("Write the note as text.");
        }
        let function = if what.trim().is_empty() { None } else { Some(Self::function(db, what)?.id) };
        db.execute(
            "INSERT INTO notes(function_id,user_name,text,created_at) VALUES(?,?,?,?)",
            params![function, user.name, &text[..text.len().min(MAX_NOTE)], now()],
        )?;
        Ok("Note saved.".into())
    }

    // ---------------------------------------------------------------------------------------------- verification

    pub fn pending(&self, limit: i64) -> Result<Value, Problem> {
        self.read(|db| self.pending_on(db, limit))
    }

    fn pending_on(&self, db: &Connection, limit: i64) -> Result<Value, Problem> {
        let mut statement = db.prepare_cached(
            "SELECT s.id,f.name,f.unit,f.vram,f.size,f.compiler,s.source,s.reported_score,s.user_name,s.created_at \
             FROM submissions s JOIN functions f ON f.id=s.function_id WHERE s.state='reported' ORDER BY s.id LIMIT ?",
        )?;
        let rows: Vec<Value> = statement
            .query_map([limit.clamp(1, 500)], |r| {
                Ok(json!({"id": r.get::<_, i64>(0)?, "function": r.get::<_, String>(1)?,
                          "unit": r.get::<_, Option<String>>(2)?, "vram": r.get::<_, Option<i64>>(3)?,
                          "size": r.get::<_, Option<i64>>(4)?,
                          "compiler": serde_json::from_str::<Value>(&r.get::<_, Option<String>>(5)?.unwrap_or_default()).unwrap_or(Value::Null),
                          "source": r.get::<_, String>(6)?, "reported_score": r.get::<_, i64>(7)?,
                          "user": r.get::<_, String>(8)?, "created_at": r.get::<_, f64>(9)?}))
            })?
            .collect::<Result<_, _>>()?;
        Ok(json!({"submissions": rows}))
    }

    /// A verifier's compile of a submission: `score` None is a compile failure. `lease` is the verifier (from
    /// /v1/verifiers) that leased it, if any: a submission another verifier holds is left to that one.
    pub fn verdict(&self, verifier: &User, lease: Option<i64>, id: i64, score: Option<i64>, detail: &str) -> Result<Value, Problem> {
        let mut db = self.db.lock().unwrap();
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let found: Option<(i64, String, String, i64, String, Option<i64>, Option<f64>)> = tx
            .query_row(
                "SELECT function_id,source,state,reported_score,user_name,lease_verifier,lease_until FROM submissions WHERE id=?",
                [id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?)),
            )
            .optional()?;
        let Some((function, source, state, reported, by, holder, until)) = found else {
            return problem(format!("no submission {id}"));
        };
        if state != "reported" {
            return problem(format!("submission {id} is already {state}"));
        }
        let at = now();
        if let (Some(holder), Some(until)) = (holder, until) {
            if until > at && Some(holder) != lease {
                return problem(format!("submission {id} is leased to another verifier"));
            }
        }
        // Scores measured by agents' own tools differ from the verifier's for anything but a match, so any compile
        // is recorded with the verifier's score. Rejected: it didn't compile, or it claimed a match that isn't one.
        let accepted = matches!(score, Some(s) if s >= 0 && !(reported == 0 && s != 0));
        tx.execute(
            "UPDATE submissions SET state=?,verified_score=?,detail=?,verified_at=?,verifier=?,lease_verifier=NULL,\
             lease_until=NULL WHERE id=?",
            params![if accepted { "verified" } else { "rejected" }, score, detail, at, verifier.name, id],
        )?;
        let mut matched = false;
        if let (true, Some(score)) = (accepted, score) {
            if score == 0 {
                // (its matching C is its best: not stored twice)
                matched = tx.execute(
                    "UPDATE functions SET status='matched',matched_source=?,matched_by=?,matched_at=?,best_score=0,\
                     best_source=NULL WHERE id=? AND status!='matched'",
                    params![pack(&source), by, at, function],
                )? > 0;
                tx.execute("DELETE FROM claims WHERE function_id=?", [function])?;
            } else {
                tx.execute(
                    "UPDATE functions SET best_score=?,best_source=? WHERE id=? AND (best_score IS NULL OR best_score>?)",
                    params![score, pack(&source), function, score],
                )?;
            }
        }
        tx.commit()?;
        Ok(json!({"id": id, "state": if accepted { "verified" } else { "rejected" }, "matched": matched}))
    }

    /// Lease up to `max` reported submissions to a verifier (those whose compiler it has, if it names any).
    pub fn lease(&self, verifier: i64, toolchains: &[String], max: i64) -> Result<Vec<Value>, Problem> {
        let mut db = self.db.lock().unwrap();
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let at = now();
        let wanted = serde_json::to_string(toolchains).unwrap_or_else(|_| "[]".into());
        let found: Vec<Value> = {
            let mut statement = tx.prepare_cached(
                "SELECT s.id,f.id,f.name,f.unit,f.vram,f.size,f.target,f.relocs,f.compiler,s.source,s.reported_score \
                 FROM submissions s JOIN functions f ON f.id=s.function_id \
                 WHERE s.state='reported' AND (s.lease_until IS NULL OR s.lease_until<?1) \
                 AND (?2='[]' OR json_extract(f.compiler,'$.toolchain') IN (SELECT value FROM json_each(?2))) \
                 ORDER BY s.id LIMIT ?3",
            )?;
            let rows = statement.query_map(params![at, wanted, max.clamp(1, 100)], |r| {
                Ok(json!({"id": r.get::<_, i64>(0)?, "slug": self.slug,
                          "function": {"id": r.get::<_, i64>(1)?, "name": r.get::<_, String>(2)?,
                                       "unit": r.get::<_, Option<String>>(3)?, "vram": r.get::<_, Option<i64>>(4)?,
                                       "size": r.get::<_, Option<i64>>(5)?,
                                       "target": hex(&r.get::<_, Option<Vec<u8>>>(6)?.unwrap_or_default()),
                                       "relocs": r.get::<_, Option<String>>(7)?},
                          "compiler": serde_json::from_str::<Value>(&r.get::<_, Option<String>>(8)?.unwrap_or_default()).unwrap_or(Value::Null),
                          "source": r.get::<_, String>(9)?, "reported_score": r.get::<_, i64>(10)?}))
            })?;
            rows.collect::<Result<_, _>>()?
        };
        for item in &found {
            tx.execute(
                "UPDATE submissions SET lease_verifier=?,lease_until=? WHERE id=?",
                params![verifier, at + LEASE_SECONDS, item["id"].as_i64()],
            )?;
        }
        tx.commit()?;
        Ok(found)
    }

    /// A verifier couldn't judge a submission (its copy of the game differs, its compiler broke): another may.
    pub fn release_lease(&self, verifier: i64, id: i64, reason: &str) -> Result<Value, Problem> {
        let mut db = self.db.lock().unwrap();
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let tries: Option<i64> = tx
            .query_row(
                "SELECT tries FROM submissions WHERE id=? AND state='reported' AND lease_verifier=?",
                params![id, verifier],
                |r| r.get(0),
            )
            .optional()?;
        let Some(tries) = tries else { return problem(format!("submission {id} isn't leased to this verifier")) };
        let state = if tries + 1 >= MAX_TRIES { "unverifiable" } else { "reported" };
        tx.execute(
            "UPDATE submissions SET tries=tries+1,lease_verifier=NULL,lease_until=NULL,state=?,detail=? WHERE id=?",
            params![state, reason.chars().take(4000).collect::<String>(), id],
        )?;
        tx.commit()?;
        Ok(json!({"id": id, "state": state}))
    }

    /// Merge a fresh export into this project (MERGE_SQL), on its writer, in one transaction.
    fn merge(&self, fresh: &Path) -> Result<i64, Problem> {
        let mut db = self.db.lock().unwrap();
        db.execute("ATTACH DATABASE ?1 AS incoming", [fresh.to_string_lossy()])?;
        let result = (|| -> Result<i64, Problem> {
            let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let removed: i64 = tx.query_row(
                "SELECT COUNT(*) FROM main.functions WHERE status NOT IN ('matched','removed') \
                 AND id NOT IN (SELECT id FROM incoming.functions)",
                [],
                |r| r.get(0),
            )?;
            tx.execute_batch(MERGE_SQL)?;
            tx.commit()?;
            Ok(removed)
        })();
        db.execute("DETACH DATABASE incoming", [])?;
        result
    }

    /// Verified matches since a time, for the app that owns the full project to take in.
    pub fn matches(&self, since: f64) -> Result<Value, Problem> {
        self.read(|db| self.matches_on(db, since))
    }

    fn matches_on(&self, db: &Connection, since: f64) -> Result<Value, Problem> {
        let mut statement = db.prepare_cached(
            // Matches verified here: not the ones the project came with (hosted_export.py).
            "SELECT id,name,unit,vram,matched_source,matched_by,matched_at FROM functions \
             WHERE status='matched' AND matched_at>? AND id IN \
             (SELECT function_id FROM submissions WHERE state='verified' AND verified_score=0) \
             ORDER BY matched_at LIMIT 1000",
        )?;
        let rows: Vec<Value> = statement
            .query_map([since], |r| {
                Ok(json!({"id": r.get::<_, i64>(0)?, "function": r.get::<_, String>(1)?,
                          "unit": r.get::<_, Option<String>>(2)?, "vram": r.get::<_, Option<i64>>(3)?,
                          "source": unpack(r.get_ref(4)?), "by": r.get::<_, Option<String>>(5)?,
                          "matched_at": r.get::<_, f64>(6)?}))
            })?
            .collect::<Result<_, _>>()?;
        Ok(json!({"matches": rows}))
    }
}

// ------------------------------------------------------------------------------------------------------ the server

pub struct Hosted {
    dir: PathBuf,
    server: Mutex<Connection>,
    /// token hash -> user, so a request doesn't wait on the users' file (a new user is found on the first miss).
    known: Mutex<HashMap<String, (User, Instant)>>,
    /// (user, project) -> may use it; and "open" or "invite" (settings 'access').
    access: Mutex<HashMap<(i64, String), (bool, Instant)>>,
    policy: Mutex<Option<(String, Instant)>>,
    /// user -> (calls left, when counted): `rate` calls a minute each.
    buckets: Mutex<HashMap<i64, (f64, Instant)>>,
    pub rate: f64,
    projects: Mutex<HashMap<String, Arc<Project>>>,
    users: Mutex<HashMap<i64, Arc<Semaphore>>>,
}

fn valid_slug(slug: &str) -> bool {
    !slug.is_empty() && slug.len() <= 100 && slug.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

impl Hosted {
    pub fn open(dir: &Path) -> Result<Hosted, String> {
        std::fs::create_dir_all(dir.join("projects")).map_err(|e| e.to_string())?;
        let server = open(&dir.join("server.sqlite3")).map_err(|e| e.to_string())?;
        server.execute_batch(SERVER_SCHEMA).map_err(|e| e.to_string())?;
        Ok(Hosted {
            dir: dir.to_path_buf(),
            server: Mutex::new(server),
            known: Mutex::new(HashMap::new()),
            access: Mutex::new(HashMap::new()),
            policy: Mutex::new(None),
            buckets: Mutex::new(HashMap::new()),
            rate: RATE,
            projects: Mutex::new(HashMap::new()),
            users: Mutex::new(HashMap::new()),
        })
    }

    pub fn add_user(&self, name: &str, role: &str) -> Result<String, String> {
        if !matches!(role, "agent" | "verifier" | "admin") {
            return Err(format!("role is agent, verifier or admin, not {role:?}"));
        }
        let token = format!("dd_{}", new_token(24));
        self.server
            .lock()
            .unwrap()
            .execute(
                "INSERT INTO users(name,token_hash,role,created_at) VALUES(?,?,?,?)",
                params![name, hash_token(&token), role, now()],
            )
            .map_err(|e| e.to_string())?;
        Ok(token)
    }

    pub fn user(&self, headers: &HeaderMap) -> Option<User> {
        let auth = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
        let token = auth.strip_prefix("Bearer ").unwrap_or(auth).trim();
        if token.is_empty() {
            return None;
        }
        let hashed = hash_token(token);
        if let Some((user, at)) = self.known.lock().unwrap().get(&hashed) {
            if at.elapsed() < REMEMBER {
                return Some(user.clone());
            }
        }
        let user = self
            .server
            .lock()
            .unwrap()
            .query_row("SELECT id,name,role FROM users WHERE token_hash=?", [&hashed], |r| {
                Ok(User { id: r.get(0)?, name: r.get(1)?, role: r.get(2)? })
            })
            .optional()
            .ok()?;
        let mut known = self.known.lock().unwrap();
        match user {
            Some(user) => {
                known.insert(hashed, (user.clone(), Instant::now()));
                Some(user)
            }
            None => {
                known.remove(&hashed); // removed, or its token replaced
                None
            }
        }
    }

    fn forget(&self) {
        self.known.lock().unwrap().clear();
        self.access.lock().unwrap().clear();
        *self.policy.lock().unwrap() = None;
    }

    /// "open" (every user may use every project) or "invite" (agents and verifiers only the projects granted them).
    pub fn policy(&self) -> String {
        if let Some((policy, at)) = self.policy.lock().unwrap().clone() {
            if at.elapsed() < REMEMBER {
                return policy;
            }
        }
        let policy: String = self
            .server
            .lock()
            .unwrap()
            .query_row("SELECT value FROM settings WHERE key='access'", [], |r| r.get(0))
            .optional()
            .ok()
            .flatten()
            .unwrap_or_else(|| "open".into());
        *self.policy.lock().unwrap() = Some((policy.clone(), Instant::now()));
        policy
    }

    pub fn set_policy(&self, policy: &str) -> Result<(), String> {
        if !matches!(policy, "open" | "invite") {
            return Err(format!("access is open or invite, not {policy:?}"));
        }
        self.server
            .lock()
            .unwrap()
            .execute("INSERT OR REPLACE INTO settings VALUES('access',?)", [policy])
            .map_err(|e| e.to_string())?;
        self.forget();
        Ok(())
    }

    pub fn allowed(&self, user: &User, slug: &str) -> bool {
        if user.role == "admin" || self.policy() == "open" {
            return true;
        }
        let key = (user.id, slug.to_string());
        if let Some((allowed, at)) = self.access.lock().unwrap().get(&key) {
            if at.elapsed() < REMEMBER {
                return *allowed;
            }
        }
        let allowed = self
            .server
            .lock()
            .unwrap()
            .query_row("SELECT 1 FROM access WHERE user_id=? AND slug=?", params![user.id, slug], |_| Ok(()))
            .optional()
            .ok()
            .flatten()
            .is_some();
        self.access.lock().unwrap().insert(key, (allowed, Instant::now()));
        allowed
    }

    /// One tool call against the user's budget of `rate` a minute.
    pub fn spend(&self, user: &User) -> bool {
        let mut buckets = self.buckets.lock().unwrap();
        let (left, at) = buckets.entry(user.id).or_insert((self.rate, Instant::now()));
        *left = (*left + at.elapsed().as_secs_f64() * self.rate / 60.0).min(self.rate);
        *at = Instant::now();
        if *left < 1.0 {
            return false;
        }
        *left -= 1.0;
        true
    }

    fn user_id(&self, name: &str) -> Result<i64, String> {
        self.server
            .lock()
            .unwrap()
            .query_row("SELECT id FROM users WHERE name=?", [name], |r| r.get(0))
            .optional()
            .map_err(|e| e.to_string())?
            .ok_or_else(|| format!("no user {name:?}"))
    }

    /// The user and everything theirs on the server (access, verifier entries); their claims run out.
    pub fn remove_user(&self, name: &str) -> Result<(), String> {
        let id = self.user_id(name)?;
        {
            let db = self.server.lock().unwrap();
            for sql in ["DELETE FROM access WHERE user_id=?", "DELETE FROM verifiers WHERE user_id=?", "DELETE FROM users WHERE id=?"] {
                db.execute(sql, [id]).map_err(|e| e.to_string())?;
            }
        }
        self.forget();
        Ok(())
    }

    /// A new token for a user; the old one stops working.
    pub fn rotate(&self, name: &str) -> Result<String, String> {
        let id = self.user_id(name)?;
        let token = format!("dd_{}", new_token(24));
        self.server
            .lock()
            .unwrap()
            .execute("UPDATE users SET token_hash=? WHERE id=?", params![hash_token(&token), id])
            .map_err(|e| e.to_string())?;
        self.forget();
        Ok(token)
    }

    pub fn grant(&self, name: &str, slug: &str, allow: bool) -> Result<(), String> {
        if !valid_slug(slug) {
            return Err(format!("{slug:?} isn't a project name"));
        }
        let id = self.user_id(name)?;
        let sql = if allow { "INSERT OR IGNORE INTO access VALUES(?,?)" } else { "DELETE FROM access WHERE user_id=? AND slug=?" };
        self.server.lock().unwrap().execute(sql, params![id, slug]).map_err(|e| e.to_string())?;
        self.forget();
        Ok(())
    }

    pub fn users_list(&self) -> Result<Value, String> {
        let db = self.server.lock().unwrap();
        let mut statement = db
            .prepare(
                "SELECT u.name,u.role,u.created_at,(SELECT json_group_array(slug) FROM access a WHERE a.user_id=u.id) \
                 FROM users u ORDER BY u.name",
            )
            .map_err(|e| e.to_string())?;
        let users: Vec<Value> = statement
            .query_map([], |r| {
                Ok(json!({"name": r.get::<_, String>(0)?, "role": r.get::<_, String>(1)?,
                          "created_at": r.get::<_, f64>(2)?,
                          "projects": serde_json::from_str::<Value>(&r.get::<_, String>(3)?).unwrap_or(Value::Null)}))
            })
            .map_err(|e| e.to_string())?
            .collect::<Result<_, _>>()
            .map_err(|e| e.to_string())?;
        Ok(json!({"access": self.policy_now(&db), "users": users}))
    }

    fn policy_now(&self, db: &Connection) -> String {
        db.query_row("SELECT value FROM settings WHERE key='access'", [], |r| r.get(0))
            .optional()
            .ok()
            .flatten()
            .unwrap_or_else(|| "open".into())
    }

    pub fn project(&self, slug: &str) -> Option<Arc<Project>> {
        if !valid_slug(slug) {
            return None;
        }
        let mut projects = self.projects.lock().unwrap();
        if let Some(found) = projects.get(slug) {
            return Some(found.clone());
        }
        let path = self.dir.join("projects").join(format!("{slug}.sqlite3"));
        if !path.is_file() {
            return None;
        }
        match Project::open(&path, slug) {
            Ok(project) => {
                let project = Arc::new(project);
                projects.insert(slug.to_string(), project.clone());
                Some(project)
            }
            Err(error) => {
                eprintln!("decomp-mcp-gateway: {error}");
                None
            }
        }
    }

    pub fn slugs(&self) -> Vec<String> {
        let mut found: Vec<String> = std::fs::read_dir(self.dir.join("projects"))
            .map(|entries| {
                entries
                    .filter_map(|e| e.ok()?.file_name().to_str()?.strip_suffix(".sqlite3").map(String::from))
                    .filter(|s| valid_slug(s))
                    .collect()
            })
            .unwrap_or_default();
        found.sort();
        found
    }

    /// A compiler server says what it can judge; the same user and name again updates its entry. Its id is what it
    /// leases with.
    pub fn register(&self, user: &User, body: &Value) -> Result<Value, Problem> {
        let name = body.get("name").and_then(Value::as_str).unwrap_or("").trim().to_string();
        if name.is_empty() || name.len() > 100 {
            return problem("name the verifier (up to 100 characters)");
        }
        let strings = |key: &str| -> Vec<String> {
            body.get(key)
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                .unwrap_or_default()
        };
        let projects: Vec<String> = strings("projects").into_iter().filter(|s| valid_slug(s)).collect();
        let toolchains = strings("toolchains");
        let slots = body.get("slots").and_then(Value::as_i64).unwrap_or(1).clamp(1, 256);
        let version = body.get("version").and_then(Value::as_str).unwrap_or("").chars().take(100).collect::<String>();
        let at = now();
        let db = self.server.lock().unwrap();
        db.execute(
            "INSERT INTO verifiers(user_id,name,projects,toolchains,slots,version,registered_at,last_seen) \
             VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET projects=excluded.projects,\
             toolchains=excluded.toolchains,slots=excluded.slots,version=excluded.version,last_seen=excluded.last_seen",
            params![user.id, name, json!(projects).to_string(), json!(toolchains).to_string(), slots, version, at, at],
        )?;
        let id: i64 =
            db.query_row("SELECT id FROM verifiers WHERE user_id=? AND name=?", params![user.id, name], |r| r.get(0))?;
        drop(db);
        let served: Vec<&String> = projects
            .iter()
            .filter(|s| self.dir.join("projects").join(format!("{s}.sqlite3")).is_file() && self.allowed(user, s))
            .collect();
        Ok(json!({"verifier": id, "projects": served}))
    }

    /// Install an uploaded project file or verification pack (`decomp hosted-publish`): a new project as it is, an
    /// existing one merged in one transaction (MERGE_SQL), so the server keeps its claims, submissions and matches.
    /// A pack replaces the old one whole.
    pub fn install(&self, slug: &str, kind: &str, data: &[u8]) -> Result<Value, Problem> {
        if !valid_slug(slug) {
            return problem(format!("{slug:?} isn't a project name"));
        }
        let incoming = self.dir.join("incoming");
        std::fs::create_dir_all(&incoming).map_err(|e| Problem(e.to_string()))?;
        let upload = incoming.join(format!("{slug}-{}.sqlite3", new_token(6)));
        std::fs::write(&upload, data).map_err(|e| Problem(e.to_string()))?;
        let result = self.install_file(slug, kind, &upload);
        let _ = std::fs::remove_file(&upload);
        result
    }

    fn install_file(&self, slug: &str, kind: &str, upload: &Path) -> Result<Value, Problem> {
        let (format, named, functions, file_kind) = {
            let db = Connection::open_with_flags(upload, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
                .map_err(|e| Problem(format!("that isn't a SQLite file: {e}")))?;
            let meta = |key: &str| -> Option<String> {
                db.query_row("SELECT value FROM meta WHERE key=?", [key], |r| r.get(0)).optional().ok().flatten()
            };
            let functions: i64 = db.query_row("SELECT COUNT(*) FROM functions", [], |r| r.get(0)).unwrap_or(-1);
            (meta("format"), meta("slug"), functions, meta("kind").unwrap_or_else(|| "project".into()))
        };
        if file_kind != kind {
            return problem(format!("that file is a {file_kind}, not a {kind}"));
        }
        if named.as_deref() != Some(slug) {
            return problem(format!("that file is the project {named:?}, not {slug:?}"));
        }
        if functions < 0 {
            return problem("that file has no functions table");
        }
        match kind {
            "pack" => {
                if format.as_deref() != Some(PACK_FORMAT) {
                    return problem(format!("that pack is format {format:?}; this server reads {PACK_FORMAT}"));
                }
                let packs = self.dir.join("packs");
                std::fs::create_dir_all(&packs).map_err(|e| Problem(e.to_string()))?;
                let part = packs.join(format!(".{slug}.part"));
                std::fs::copy(upload, &part).map_err(|e| Problem(e.to_string()))?;
                std::fs::rename(&part, packs.join(format!("{slug}.sqlite3"))).map_err(|e| Problem(e.to_string()))?;
                Ok(json!({"project": slug, "kind": "pack", "functions": functions}))
            }
            "project" => {
                if format.as_deref() != Some(FORMAT) {
                    return problem(format!("that project is format {format:?}; this server reads {FORMAT}"));
                }
                let live = self.dir.join("projects").join(format!("{slug}.sqlite3"));
                {
                    // A project nobody has opened yet: installed as it is (under the lock that opens projects).
                    let projects = self.projects.lock().unwrap();
                    if !projects.contains_key(slug) && !live.exists() {
                        let part = live.with_file_name(format!(".{slug}.part"));
                        std::fs::copy(upload, &part).map_err(|e| Problem(e.to_string()))?;
                        std::fs::rename(&part, &live).map_err(|e| Problem(e.to_string()))?;
                        drop(projects);
                        let _ = self.project(slug).ok_or_else(|| Problem("the new project didn't open".into()))?;
                        return Ok(json!({"project": slug, "kind": "project", "created": true, "functions": functions}));
                    }
                }
                let project = self.project(slug).ok_or_else(|| Problem(format!("{slug} didn't open")))?;
                let removed = project.merge(upload)?;
                Ok(json!({"project": slug, "kind": "project", "created": false, "functions": functions, "removed": removed}))
            }
            other => problem(format!("kind is project or pack, not {other:?}")),
        }
    }

    /// The verifier's projects and toolchains, if it is this user's.
    fn verifier_of(&self, user: &User, id: i64) -> Result<(Vec<String>, Vec<String>), Problem> {
        let db = self.server.lock().unwrap();
        let found: Option<(String, String)> = db
            .query_row(
                "SELECT projects,toolchains FROM verifiers WHERE id=? AND user_id=?",
                params![id, user.id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        let Some((projects, toolchains)) = found else { return problem("register this verifier first (POST /v1/verifiers)") };
        db.execute("UPDATE verifiers SET last_seen=? WHERE id=?", params![now(), id])?;
        drop(db);
        let projects: Vec<String> = serde_json::from_str(&projects).unwrap_or_default();
        let projects = projects.into_iter().filter(|slug| self.allowed(user, slug)).collect();
        Ok((projects, serde_json::from_str(&toolchains).unwrap_or_default()))
    }

    /// Up to `max` submissions for a verifier, across the projects it registered for.
    pub fn lease(&self, user: &User, verifier: i64, max: i64) -> Result<Value, Problem> {
        let (projects, toolchains) = self.verifier_of(user, verifier)?;
        let mut work = Vec::new();
        for slug in projects {
            let left = max - work.len() as i64;
            if left <= 0 {
                break;
            }
            if let Some(project) = self.project(&slug) {
                work.extend(project.lease(verifier, &toolchains, left)?);
            }
        }
        Ok(json!({"work": work, "lease_seconds": LEASE_SECONDS}))
    }

    pub fn release(&self, user: &User, verifier: i64, slug: &str, id: i64, reason: &str) -> Result<Value, Problem> {
        self.verifier_of(user, verifier)?;
        let Some(project) = self.project(slug) else { return problem("no such project") };
        project.release_lease(verifier, id, reason)
    }

    pub fn verifiers(&self) -> Result<Value, Problem> {
        let db = self.server.lock().unwrap();
        let mut statement = db.prepare(
            "SELECT v.id,v.name,u.name,v.projects,v.toolchains,v.slots,v.version,v.last_seen FROM verifiers v \
             JOIN users u ON u.id=v.user_id ORDER BY v.id",
        )?;
        let rows: Vec<Value> = statement
            .query_map([], |r| {
                Ok(json!({"id": r.get::<_, i64>(0)?, "name": r.get::<_, String>(1)?, "user": r.get::<_, String>(2)?,
                          "projects": serde_json::from_str::<Value>(&r.get::<_, String>(3)?).unwrap_or(Value::Null),
                          "toolchains": serde_json::from_str::<Value>(&r.get::<_, String>(4)?).unwrap_or(Value::Null),
                          "slots": r.get::<_, i64>(5)?, "version": r.get::<_, String>(6)?,
                          "last_seen": r.get::<_, f64>(7)?}))
            })?
            .collect::<Result<_, _>>()?;
        Ok(json!({"verifiers": rows}))
    }

    fn lane(&self, user: &User) -> Arc<Semaphore> {
        self.users.lock().unwrap().entry(user.id).or_insert_with(|| Arc::new(Semaphore::new(PER_USER))).clone()
    }
}

// --------------------------------------------------------------------------------------------------------- MCP

fn string(description: &str) -> Value {
    json!({"type": "string", "description": description})
}

pub fn tool_list() -> Value {
    let function = string("the function's name, name@unit, or address such as 0x80001234");
    let token = string("the claim_token from claim_function or get_claims");
    json!([
        {"name": "project_info", "description": "Which game this is, its compiler, progress, and how to work here.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "list_functions", "description": "Functions in this project by address: status, size, best verified score, who holds them.",
         "inputSchema": {"type": "object", "properties": {
            "status": string("open (default), matched, asm or all"),
            "limit": {"type": "integer", "description": "at most this many (default 40, up to 1000)"}}}},
        {"name": "get_function", "description": "A function's facts and assembly; section=target gives the bytes your compile must equal, context the header, best the best verified C, matched its matching C, notes what others found, all everything.",
         "inputSchema": {"type": "object", "properties": {"function": function, "section": string("summary (default), assembly, target, context, best, matched, notes or all")}, "required": ["function"]}},
        {"name": "claim_function", "description": "Claim the next open function (or a named one) for 30 minutes; returns its claim_token, compiler and assembly.",
         "inputSchema": {"type": "object", "properties": {"function": string("optional: a specific function"), "strategy": string("easiest (default), hardest, closest or random")}}},
        {"name": "get_claims", "description": "Your live claims in this project and their claim tokens.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "submit_result", "description": "Record C you compiled on your own computer and the score you measured (0 = byte-identical). A match counts once a verifier compiles it too. Renews your claim.",
         "inputSchema": {"type": "object", "properties": {"function": function, "claim_token": token, "source": string("the complete C of the function"), "score": {"type": "integer", "description": "the difference you measured; 0 is a match"}}, "required": ["function", "claim_token", "source", "score"]}},
        {"name": "release_function", "description": "Give a claim back, with notes on what you tried and what is left.",
         "inputSchema": {"type": "object", "properties": {"function": function, "claim_token": token, "notes": string("what you tried and what is left")}, "required": ["function", "claim_token"]}},
        {"name": "add_note", "description": "Leave a note for everyone on a function (or the project).",
         "inputSchema": {"type": "object", "properties": {"text": string("the note"), "function": string("optional: the function it's about")}, "required": ["text"]}},
    ])
}

const INSTRUCTIONS: &str = "decompdecomp hosted project: decompile one function at a time into C that compiles to \
the same bytes. Claim a function, read its assembly, target bytes and context, compile on your own computer with the \
project's compiler, and submit your C with the score you measured. Start with project_info.";

fn rpc_result(id: &Value, result: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "result": result})
}

fn rpc_error(id: &Value, code: i64, message: &str) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
}

fn text_result(text: String, error: bool) -> Value {
    json!({"content": [{"type": "text", "text": text}], "isError": error})
}

async fn one(hosted: &Arc<Hosted>, project: &Arc<Project>, user: &User, message: Value) -> Option<Value> {
    let id = message.get("id").cloned()?; // a notification: nothing to answer
    let method = message.get("method").and_then(Value::as_str).unwrap_or("");
    Some(match method {
        "ping" => rpc_result(&id, json!({})),
        "initialize" => {
            let version = message.pointer("/params/protocolVersion").and_then(Value::as_str).unwrap_or(PROTOCOL_FALLBACK);
            rpc_result(&id, json!({"protocolVersion": version, "capabilities": {"tools": {"listChanged": false}},
                                   "serverInfo": {"name": "decomp", "title": "decompdecomp (hosted)", "version": env!("CARGO_PKG_VERSION")},
                                   "instructions": INSTRUCTIONS}))
        }
        "tools/list" => rpc_result(&id, json!({"tools": tool_list()})),
        "tools/call" => {
            let tool = message.pointer("/params/name").and_then(Value::as_str).unwrap_or("").to_string();
            let args = message.pointer("/params/arguments").cloned().unwrap_or_else(|| json!({}));
            if !hosted.spend(user) {
                return Some(rpc_result(&id, text_result(format!(
                    "Too many calls: this server allows {} a minute. Wait a few seconds, then repeat this call.",
                    hosted.rate as i64), true)));
            }
            let Ok(_permit) = hosted.lane(user).try_acquire_owned() else {
                return Some(rpc_result(&id, text_result(format!("You have {PER_USER} calls running: wait for them."), true)));
            };
            let (project, user) = (project.clone(), user.clone());
            let answer = tokio::task::spawn_blocking(move || project.call(&user, &tool, &args)).await;
            match answer {
                Ok(Ok(text)) => rpc_result(&id, text_result(text, false)),
                Ok(Err(Problem(text))) => rpc_result(&id, text_result(text, true)),
                Err(_) => rpc_result(&id, text_result("the server failed on this call; try again".into(), true)),
            }
        }
        _ => rpc_error(&id, -32601, "method not found"),
    })
}

fn json_response(status: StatusCode, value: Value) -> Response {
    let mut response = (status, value.to_string()).into_response();
    response.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/json"));
    response
}

async fn mcp(State(hosted): State<Arc<Hosted>>, UrlPath(slug): UrlPath<String>, headers: HeaderMap, body: Bytes) -> Response {
    let Some(user) = hosted.user(&headers) else {
        return json_response(StatusCode::UNAUTHORIZED, rpc_error(&Value::Null, -32001, "a valid API token is required"));
    };
    let Some(project) = hosted.project(&slug) else {
        return json_response(StatusCode::NOT_FOUND, rpc_error(&Value::Null, -32004, "no such project"));
    };
    if !hosted.allowed(&user, &slug) {
        return json_response(StatusCode::FORBIDDEN, rpc_error(&Value::Null, -32003, "this server hasn't given you this project"));
    }
    let parsed: Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(e) => return json_response(StatusCode::BAD_REQUEST, rpc_error(&Value::Null, -32700, &e.to_string())),
    };
    match parsed {
        Value::Array(messages) => {
            let mut replies = Vec::new();
            for message in messages {
                if let Some(reply) = one(&hosted, &project, &user, message).await {
                    replies.push(reply);
                }
            }
            if replies.is_empty() {
                StatusCode::ACCEPTED.into_response()
            } else {
                json_response(StatusCode::OK, Value::Array(replies))
            }
        }
        message @ Value::Object(_) => match one(&hosted, &project, &user, message).await {
            Some(reply) => json_response(StatusCode::OK, reply),
            None => StatusCode::ACCEPTED.into_response(),
        },
        _ => json_response(StatusCode::BAD_REQUEST, rpc_error(&Value::Null, -32600, "not a JSON-RPC message")),
    }
}

async fn mcp_other() -> Response {
    let mut response = StatusCode::METHOD_NOT_ALLOWED.into_response();
    response.headers_mut().insert(header::ALLOW, HeaderValue::from_static("POST"));
    response
}

async fn projects(State(hosted): State<Arc<Hosted>>, headers: HeaderMap) -> Response {
    let Some(user) = hosted.user(&headers) else {
        return json_response(StatusCode::UNAUTHORIZED, json!({"error": "a valid API token is required"}));
    };
    let mine: Vec<String> = hosted.slugs().into_iter().filter(|s| hosted.allowed(&user, s)).collect();
    json_response(StatusCode::OK, json!({"projects": mine}))
}

fn verifier(hosted: &Hosted, headers: &HeaderMap) -> Result<User, Response> {
    match hosted.user(headers) {
        Some(user) if user.role == "verifier" || user.role == "admin" => Ok(user),
        Some(_) => Err(json_response(StatusCode::FORBIDDEN, json!({"error": "verifiers only"}))),
        None => Err(json_response(StatusCode::UNAUTHORIZED, json!({"error": "a valid API token is required"}))),
    }
}

async fn blocking(work: impl FnOnce() -> Result<Value, Problem> + Send + 'static) -> Response {
    match tokio::task::spawn_blocking(work).await {
        Ok(Ok(value)) => json_response(StatusCode::OK, value),
        Ok(Err(Problem(text))) => json_response(StatusCode::CONFLICT, json!({"error": text})),
        Err(_) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({"error": "failed"})),
    }
}

async fn pending(
    State(hosted): State<Arc<Hosted>>,
    UrlPath(slug): UrlPath<String>,
    Query(query): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    if let Err(denied) = verifier(&hosted, &headers) {
        return denied;
    }
    let Some(project) = hosted.project(&slug) else { return json_response(StatusCode::NOT_FOUND, json!({"error": "no such project"})) };
    let limit = query.get("limit").and_then(|l| l.parse().ok()).unwrap_or(50);
    blocking(move || project.pending(limit)).await
}

async fn verdict(State(hosted): State<Arc<Hosted>>, UrlPath(slug): UrlPath<String>, headers: HeaderMap, body: Bytes) -> Response {
    let user = match verifier(&hosted, &headers) {
        Ok(user) => user,
        Err(denied) => return denied,
    };
    let Some(project) = hosted.project(&slug) else { return json_response(StatusCode::NOT_FOUND, json!({"error": "no such project"})) };
    let Ok(body) = serde_json::from_slice::<Value>(&body) else {
        return json_response(StatusCode::BAD_REQUEST, json!({"error": "send JSON: {id, score (null: didn't compile), detail}"}));
    };
    let Some(id) = body.get("id").and_then(Value::as_i64) else {
        return json_response(StatusCode::BAD_REQUEST, json!({"error": "id is required"}));
    };
    let score = body.get("score").and_then(Value::as_i64);
    let lease = body.get("verifier").and_then(Value::as_i64);
    let detail = body.get("detail").and_then(Value::as_str).unwrap_or("").chars().take(4000).collect::<String>();
    blocking(move || project.verdict(&user, lease, id, score, &detail)).await
}

async fn matches(
    State(hosted): State<Arc<Hosted>>,
    UrlPath(slug): UrlPath<String>,
    Query(query): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    if let Err(denied) = verifier(&hosted, &headers) {
        return denied;
    }
    let Some(project) = hosted.project(&slug) else { return json_response(StatusCode::NOT_FOUND, json!({"error": "no such project"})) };
    let since = query.get("since").and_then(|s| s.parse().ok()).unwrap_or(0.0);
    blocking(move || project.matches(since)).await
}

fn admin(hosted: &Hosted, headers: &HeaderMap) -> Result<User, Response> {
    match hosted.user(headers) {
        Some(user) if user.role == "admin" => Ok(user),
        Some(_) => Err(json_response(StatusCode::FORBIDDEN, json!({"error": "admins only"}))),
        None => Err(json_response(StatusCode::UNAUTHORIZED, json!({"error": "a valid API token is required"}))),
    }
}

fn json_body(body: &Bytes) -> Result<Value, Response> {
    serde_json::from_slice::<Value>(body)
        .map_err(|_| json_response(StatusCode::BAD_REQUEST, json!({"error": "send a JSON object"})))
}

/// POST /v1/verifiers: a compiler server registers {name, projects, toolchains, slots, version}.
async fn register(State(hosted): State<Arc<Hosted>>, headers: HeaderMap, body: Bytes) -> Response {
    let user = match verifier(&hosted, &headers) {
        Ok(user) => user,
        Err(denied) => return denied,
    };
    let body = match json_body(&body) {
        Ok(b) => b,
        Err(bad) => return bad,
    };
    blocking(move || hosted.register(&user, &body)).await
}

/// GET /v1/verifiers: the registered compiler servers (admins).
async fn list_verifiers(State(hosted): State<Arc<Hosted>>, headers: HeaderMap) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    blocking(move || hosted.verifiers()).await
}

/// POST /v1/work/lease {verifier, max}: submissions to judge.
async fn lease(State(hosted): State<Arc<Hosted>>, headers: HeaderMap, body: Bytes) -> Response {
    let user = match verifier(&hosted, &headers) {
        Ok(user) => user,
        Err(denied) => return denied,
    };
    let body = match json_body(&body) {
        Ok(b) => b,
        Err(bad) => return bad,
    };
    let Some(id) = body.get("verifier").and_then(Value::as_i64) else {
        return json_response(StatusCode::BAD_REQUEST, json!({"error": "verifier is required"}));
    };
    let max = body.get("max").and_then(Value::as_i64).unwrap_or(1);
    blocking(move || hosted.lease(&user, id, max)).await
}

/// POST /v1/work/release {verifier, slug, id, reason}: a submission this verifier couldn't judge.
async fn release_work(State(hosted): State<Arc<Hosted>>, headers: HeaderMap, body: Bytes) -> Response {
    let user = match verifier(&hosted, &headers) {
        Ok(user) => user,
        Err(denied) => return denied,
    };
    let body = match json_body(&body) {
        Ok(b) => b,
        Err(bad) => return bad,
    };
    let (Some(verifier_id), Some(slug), Some(id)) = (
        body.get("verifier").and_then(Value::as_i64),
        body.get("slug").and_then(Value::as_str).map(String::from),
        body.get("id").and_then(Value::as_i64),
    ) else {
        return json_response(StatusCode::BAD_REQUEST, json!({"error": "verifier, slug and id are required"}));
    };
    let reason = body.get("reason").and_then(Value::as_str).unwrap_or("").to_string();
    blocking(move || hosted.release(&user, verifier_id, &slug, id, &reason)).await
}

/// POST /v1/users {name, role}: a new user and its token (admins), to register agents or compiler servers remotely.
async fn create_user(State(hosted): State<Arc<Hosted>>, headers: HeaderMap, body: Bytes) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    let body = match json_body(&body) {
        Ok(b) => b,
        Err(bad) => return bad,
    };
    let name = body.get("name").and_then(Value::as_str).unwrap_or("").trim().to_string();
    let role = body.get("role").and_then(Value::as_str).unwrap_or("agent").to_string();
    if name.is_empty() {
        return json_response(StatusCode::BAD_REQUEST, json!({"error": "name is required"}));
    }
    match hosted.add_user(&name, &role) {
        Ok(token) => json_response(StatusCode::OK, json!({"name": name, "role": role, "token": token})),
        Err(e) => json_response(StatusCode::CONFLICT, json!({"error": e})),
    }
}

/// GET /v1/users (admins): every user, its role and projects, and the server's access policy.
async fn list_users(State(hosted): State<Arc<Hosted>>, headers: HeaderMap) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    match hosted.users_list() {
        Ok(users) => json_response(StatusCode::OK, users),
        Err(e) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({"error": e})),
    }
}

/// DELETE /v1/users/{name} (admins)
async fn remove_user(State(hosted): State<Arc<Hosted>>, UrlPath(name): UrlPath<String>, headers: HeaderMap) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    match hosted.remove_user(&name) {
        Ok(()) => json_response(StatusCode::OK, json!({"removed": name})),
        Err(e) => json_response(StatusCode::NOT_FOUND, json!({"error": e})),
    }
}

/// POST /v1/users/{name}/token (admins): a new token; the old one stops working.
async fn rotate(State(hosted): State<Arc<Hosted>>, UrlPath(name): UrlPath<String>, headers: HeaderMap) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    match hosted.rotate(&name) {
        Ok(token) => json_response(StatusCode::OK, json!({"name": name, "token": token})),
        Err(e) => json_response(StatusCode::NOT_FOUND, json!({"error": e})),
    }
}

/// POST /v1/users/{name}/access {project, allow} (admins)
async fn grant(State(hosted): State<Arc<Hosted>>, UrlPath(name): UrlPath<String>, headers: HeaderMap, body: Bytes) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    let body = match json_body(&body) {
        Ok(b) => b,
        Err(bad) => return bad,
    };
    let project = body.get("project").and_then(Value::as_str).unwrap_or("").to_string();
    let allow = body.get("allow").and_then(Value::as_bool).unwrap_or(true);
    match hosted.grant(&name, &project, allow) {
        Ok(()) => json_response(StatusCode::OK, json!({"name": name, "project": project, "allow": allow})),
        Err(e) => json_response(StatusCode::BAD_REQUEST, json!({"error": e})),
    }
}

/// POST /v1/policy {access: open|invite} (admins)
async fn policy(State(hosted): State<Arc<Hosted>>, headers: HeaderMap, body: Bytes) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    let body = match json_body(&body) {
        Ok(b) => b,
        Err(bad) => return bad,
    };
    match hosted.set_policy(body.get("access").and_then(Value::as_str).unwrap_or("")) {
        Ok(()) => json_response(StatusCode::OK, json!({"access": hosted.policy()})),
        Err(e) => json_response(StatusCode::BAD_REQUEST, json!({"error": e})),
    }
}

/// GET /v1/p/{slug}/pack (verifiers and admins): the project's verification pack (hosted_pack.py). It holds game data,
/// so agents never get it. Its ETag is its SHA-256: a verifier sends If-None-Match and gets 304 when it has it.
async fn verification_pack(State(hosted): State<Arc<Hosted>>, UrlPath(slug): UrlPath<String>, headers: HeaderMap) -> Response {
    let user = match verifier(&hosted, &headers) {
        Ok(user) => user,
        Err(denied) => return denied,
    };
    if !valid_slug(&slug) || !hosted.allowed(&user, &slug) {
        return json_response(StatusCode::NOT_FOUND, json!({"error": "no such pack"}));
    }
    let path = hosted.dir.join("packs").join(format!("{slug}.sqlite3"));
    let Ok(Ok(data)) = tokio::task::spawn_blocking(move || std::fs::read(path)).await else {
        return json_response(StatusCode::NOT_FOUND, json!({"error": "this project has no verification pack yet"}));
    };
    let tag = format!("\"{}\"", hex(&Sha256::digest(&data)));
    if headers.get(header::IF_NONE_MATCH).and_then(|v| v.to_str().ok()) == Some(tag.as_str()) {
        return StatusCode::NOT_MODIFIED.into_response();
    }
    let mut response = (StatusCode::OK, data).into_response();
    response.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/vnd.sqlite3"));
    if let Ok(value) = HeaderValue::from_str(&tag) {
        response.headers_mut().insert(header::ETAG, value);
    }
    response
}

/// POST /v1/p/{slug}/upload?kind=project|pack (admins): `decomp hosted-publish`'s files, the raw SQLite file as the body.
async fn upload(
    State(hosted): State<Arc<Hosted>>,
    UrlPath(slug): UrlPath<String>,
    Query(query): Query<HashMap<String, String>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(denied) = admin(&hosted, &headers) {
        return denied;
    }
    let kind = query.get("kind").cloned().unwrap_or_else(|| "project".into());
    match tokio::task::spawn_blocking(move || hosted.install(&slug, &kind, &body)).await {
        Ok(Ok(value)) => json_response(StatusCode::OK, value),
        Ok(Err(Problem(text))) => json_response(StatusCode::BAD_REQUEST, json!({"error": text})),
        Err(_) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({"error": "failed"})),
    }
}

async fn health() -> Response {
    json_response(StatusCode::OK, json!({"ok": true, "mode": "hosted"}))
}

pub fn router(hosted: Arc<Hosted>, max_body: usize) -> Router {
    Router::new()
        .route("/p/{slug}/mcp", post(mcp).get(mcp_other).delete(mcp_other))
        .route("/v1/projects", get(projects))
        .route("/v1/p/{slug}/pending", get(pending))
        .route("/v1/p/{slug}/verdicts", post(verdict))
        .route("/v1/p/{slug}/matches", get(matches))
        .route("/v1/p/{slug}/pack", get(verification_pack))
        .route("/v1/p/{slug}/upload", post(upload).layer(DefaultBodyLimit::max(MAX_UPLOAD)))
        .route("/v1/verifiers", post(register).get(list_verifiers))
        .route("/v1/work/lease", post(lease))
        .route("/v1/work/release", post(release_work))
        .route("/v1/users", post(create_user).get(list_users))
        .route("/v1/users/{name}", axum::routing::delete(remove_user))
        .route("/v1/users/{name}/token", post(rotate))
        .route("/v1/users/{name}/access", post(grant))
        .route("/v1/policy", post(policy))
        .route("/health", get(health))
        .layer(DefaultBodyLimit::max(max_body))
        .with_state(hosted)
}

/// `decomp-mcp-gateway hosted ...`
pub async fn main(args: Vec<String>) -> i32 {
    let mut args = args.into_iter();
    let command = args.next().unwrap_or_default();
    let (mut dir, mut listen, mut name, mut role) = (None, "127.0.0.1:8080".to_string(), None, "agent".to_string());
    let (mut project, mut access, mut rate) = (None, None, RATE);
    while let Some(flag) = args.next() {
        let value = args.next();
        match (flag.as_str(), value) {
            ("--dir", Some(v)) => dir = Some(PathBuf::from(v)),
            ("--listen", Some(v)) => listen = v,
            ("--name", Some(v)) => name = Some(v),
            ("--role", Some(v)) => role = v,
            ("--project", Some(v)) => project = Some(v),
            ("--access", Some(v)) => access = Some(v),
            ("--rate", Some(v)) => match v.parse::<f64>() {
                Ok(r) if r >= 1.0 => rate = r,
                _ => {
                    eprintln!("decomp-mcp-gateway hosted: --rate is calls a minute (at least 1)");
                    return 2;
                }
            },
            (other, _) => {
                eprintln!("decomp-mcp-gateway hosted: unknown or incomplete option {other}");
                return 2;
            }
        }
    }
    let Some(dir) = dir else {
        eprintln!("decomp-mcp-gateway hosted {command}: --dir is required");
        return 2;
    };
    let hosted = match Hosted::open(&dir) {
        Ok(mut h) => {
            h.rate = rate;
            Arc::new(h)
        }
        Err(e) => {
            eprintln!("decomp-mcp-gateway hosted: {e}");
            return 3;
        }
    };
    let report = |result: Result<String, String>| match result {
        Ok(text) => {
            println!("{text}");
            0
        }
        Err(e) => {
            eprintln!("decomp-mcp-gateway hosted {command}: {e}");
            1
        }
    };
    let named = || name.clone().ok_or_else(|| "--name is required".to_string());
    match command.as_str() {
        "remove-user" => return report(named().and_then(|n| hosted.remove_user(&n).map(|_| format!("removed {n}")))),
        "rotate-token" => return report(named().and_then(|n| hosted.rotate(&n))),
        "list-users" => return report(hosted.users_list().map(|v| serde_json::to_string_pretty(&v).unwrap_or_default())),
        "grant" | "revoke" => {
            let allow = command == "grant";
            return report(named().and_then(|n| {
                let p = project.clone().ok_or("--project is required")?;
                hosted.grant(&n, &p, allow).map(|_| format!("{n} {} {p}", if allow { "may use" } else { "may no longer use" }))
            }));
        }
        "policy" => {
            return report(match access.clone() {
                Some(a) => hosted.set_policy(&a).map(|_| format!("access: {a}")),
                None => Ok(format!("access: {}", hosted.policy())),
            })
        }
        _ => {}
    }
    match command.as_str() {
        "add-user" => {
            let Some(name) = name else {
                eprintln!("decomp-mcp-gateway hosted add-user: --name is required");
                return 2;
            };
            match hosted.add_user(&name, &role) {
                Ok(token) => {
                    println!("{token}");
                    0
                }
                Err(e) => {
                    eprintln!("decomp-mcp-gateway hosted add-user: {e}");
                    1
                }
            }
        }
        "serve" => {
            let listener = match tokio::net::TcpListener::bind(&listen).await {
                Ok(l) => l,
                Err(e) => {
                    eprintln!("decomp-mcp-gateway hosted: can't listen on {listen}: {e}");
                    return 3;
                }
            };
            println!("LISTENING {}", listener.local_addr().map(|a| a.port()).unwrap_or(0));
            let app = router(hosted, 4 * 1024 * 1024);
            let server = axum::serve(listener, app).with_graceful_shutdown(async {
                let _ = tokio::signal::ctrl_c().await;
            });
            if let Err(e) = server.await {
                eprintln!("decomp-mcp-gateway hosted: {e}");
                return 1;
            }
            0
        }
        other => {
            eprintln!(
                "decomp-mcp-gateway hosted: commands are serve, add-user, remove-user, rotate-token, list-users, grant, \
                 revoke and policy, not {other:?}"
            );
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn project() -> (tempfile_dir::Dir, Project) {
        let dir = tempfile_dir::Dir::new();
        let path = dir.0.join("p.sqlite3");
        let db = Connection::open(&path).unwrap();
        db.execute_batch(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
             INSERT INTO meta VALUES('format','1'),('name','Game');
             CREATE TABLE contexts(unit TEXT PRIMARY KEY, version INTEGER, header TEXT);
             CREATE TABLE functions(id INTEGER PRIMARY KEY, name TEXT, unit TEXT, vram INTEGER, rom_offset INTEGER,
               size INTEGER, n_insns INTEGER, target BLOB, relocs TEXT, asm TEXT, compiler TEXT, status TEXT,
               best_score INTEGER, best_source TEXT, matched_source TEXT, matched_by TEXT, matched_at REAL);
             INSERT INTO functions(id,name,vram,size,n_insns,status,asm,compiler) VALUES
               (1,'small',2147487744,8,2,'open','jr ra','{}'),(2,'big',2147487752,40,10,'open','...','{}'),
               (3,'done',2147487800,8,2,'matched','jr ra','{}');",
        )
        .unwrap();
        drop(db);
        let project = Project::open(&path, "p").unwrap();
        (dir, project)
    }

    fn user(id: i64) -> User {
        User { id, name: format!("u{id}"), role: "agent".into() }
    }

    fn token_of(text: &str) -> String {
        text.lines().find_map(|l| l.strip_prefix("claim_token: ")).unwrap().to_string()
    }

    #[test]
    fn claims_are_exclusive_and_easiest_first() {
        let (_dir, p) = project();
        let first = p.call(&user(1), "claim_function", &json!({})).unwrap();
        assert!(first.starts_with("CLAIMED small"), "{first}");
        let second = p.call(&user(2), "claim_function", &json!({})).unwrap();
        assert!(second.starts_with("CLAIMED big"), "{second}");
        assert!(p.call(&user(3), "claim_function", &json!({})).unwrap().starts_with("Nothing to claim"));
        let taken = p.call(&user(2), "claim_function", &json!({"function": "small"})).unwrap_err();
        assert!(taken.0.contains("claimed by u1"));
        assert!(p.call(&user(1), "claim_function", &json!({"function": "done"})).unwrap_err().0.contains("matched"));
    }

    #[test]
    fn a_verified_zero_matches_and_a_rejected_one_changes_nothing() {
        let (_dir, p) = project();
        let receipt = p.call(&user(1), "claim_function", &json!({"function": "small"})).unwrap();
        let token = token_of(&receipt);
        let wrong = p.call(&user(1), "submit_result", &json!({"function": "small", "claim_token": "old", "source": "x", "score": 0}));
        assert!(wrong.unwrap_err().0.contains("old"));
        p.call(&user(1), "submit_result", &json!({"function": "small", "claim_token": token, "source": "void small(){}", "score": 0})).unwrap();
        p.call(&user(1), "submit_result", &json!({"function": "small", "claim_token": token, "source": "lie", "score": 0})).unwrap();
        let pending = p.pending(10).unwrap();
        let ids: Vec<i64> = pending["submissions"].as_array().unwrap().iter().map(|s| s["id"].as_i64().unwrap()).collect();
        assert_eq!(ids.len(), 2);
        let verifier = User { id: 9, name: "v".into(), role: "verifier".into() };
        assert_eq!(p.verdict(&verifier, None, ids[1], Some(4), "4 bytes differ").unwrap()["state"], "rejected");
        assert_eq!(p.verdict(&verifier, None, ids[0], Some(0), "").unwrap()["matched"], true);
        assert!(p.verdict(&verifier, None, ids[0], Some(0), "").is_err()); // already verified
        let info = p.call(&user(1), "get_function", &json!({"function": "small", "section": "matched"})).unwrap();
        assert!(info.contains("void small(){}"));
        assert!(p.call(&user(1), "get_claims", &json!({})).unwrap().contains("\"claims\":[]"));
        assert_eq!(p.matches(0.0).unwrap()["matches"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn release_needs_the_claim_and_keeps_notes() {
        let (_dir, p) = project();
        let token = token_of(&p.call(&user(1), "claim_function", &json!({})).unwrap());
        assert!(p.call(&user(2), "release_function", &json!({"function": "small", "claim_token": token})).is_err());
        p.call(&user(1), "release_function", &json!({"function": "small", "claim_token": token, "notes": "tried a loop"})).unwrap();
        assert!(p.call(&user(2), "get_function", &json!({"function": "small", "section": "notes"})).unwrap().contains("u1: tried a loop"));
        assert!(p.call(&user(2), "claim_function", &json!({})).unwrap().starts_with("CLAIMED small"));
    }

    #[test]
    fn leases_are_exclusive_and_three_releases_give_up() {
        let (_dir, p) = project();
        let token = token_of(&p.call(&user(1), "claim_function", &json!({"function": "small"})).unwrap());
        for source in ["a", "b"] {
            p.call(&user(1), "submit_result", &json!({"function": "small", "claim_token": token, "source": source, "score": 3})).unwrap();
        }
        let first = p.lease(10, &[], 1).unwrap();
        let second = p.lease(11, &[], 5).unwrap();
        assert_eq!((first.len(), second.len()), (1, 1));
        assert_ne!(first[0]["id"], second[0]["id"]);
        assert!(p.lease(12, &[], 5).unwrap().is_empty()); // both leased
        assert!(p.lease(12, &["ido-7.1".to_string()], 5).unwrap().is_empty());
        let id = first[0]["id"].as_i64().unwrap();
        let checker = User { id: 9, name: "v".into(), role: "verifier".into() };
        assert!(p.verdict(&checker, Some(11), id, Some(1), "").unwrap_err().0.contains("another verifier"));
        assert!(p.release_lease(11, id, "not mine").is_err());
        for (round, verifier) in [10, 12, 13].into_iter().enumerate() {
            if round > 0 {
                assert_eq!(p.lease(verifier, &[], 1).unwrap()[0]["id"].as_i64(), Some(id));
            }
            let state = p.release_lease(verifier, id, "no compiler").unwrap()["state"].clone();
            assert_eq!(state, if round == 2 { "unverifiable" } else { "reported" });
        }
        let other = second[0]["id"].as_i64().unwrap();
        assert_eq!(p.verdict(&checker, Some(11), other, Some(2), "").unwrap()["state"], "verified");
    }

    mod tempfile_dir {
        pub struct Dir(pub std::path::PathBuf);
        impl Dir {
            pub fn new() -> Dir {
                let path = std::env::temp_dir().join(format!("hosted-test-{}", super::new_token(8)));
                std::fs::create_dir_all(&path).unwrap();
                Dir(path)
            }
        }
        impl Drop for Dir {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }
}
