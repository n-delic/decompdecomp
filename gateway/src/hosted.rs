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
use std::time::{Duration, SystemTime, UNIX_EPOCH};

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
const CLAIM_SECONDS: f64 = 30.0 * 60.0;
const MAX_CLAIMS: i64 = 6;
const MAX_SOURCE: usize = 256 * 1024;
const MAX_NOTE: usize = 4000;
const PER_USER: usize = 16;
const PROTOCOL_FALLBACK: &str = "2025-06-18";

const PROJECT_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS claims(function_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, user_name TEXT NOT NULL,
    token TEXT NOT NULL, claimed_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS claims_user ON claims(user_id, expires_at);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT, function_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, user_name TEXT NOT NULL, source TEXT NOT NULL, reported_score INTEGER NOT NULL,
    created_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'reported', verified_score INTEGER, detail TEXT,
    verified_at REAL, verifier TEXT);
CREATE INDEX IF NOT EXISTS submissions_state ON submissions(state, id);
CREATE INDEX IF NOT EXISTS submissions_verified ON submissions(verified_at);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, function_id INTEGER, user_name TEXT NOT NULL,
    text TEXT NOT NULL, created_at REAL NOT NULL);
";

const SERVER_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE, role TEXT NOT NULL, created_at REAL NOT NULL);
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

    /// A verifier's compile of a submission: `score` None is a compile failure.
    pub fn verdict(&self, verifier: &User, id: i64, score: Option<i64>, detail: &str) -> Result<Value, Problem> {
        let mut db = self.db.lock().unwrap();
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let found: Option<(i64, String, String, i64, String)> = tx
            .query_row(
                "SELECT function_id,source,state,reported_score,user_name FROM submissions WHERE id=?",
                [id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
            )
            .optional()?;
        let Some((function, source, state, reported, by)) = found else { return problem(format!("no submission {id}")) };
        if state != "reported" {
            return problem(format!("submission {id} is already {state}"));
        }
        let at = now();
        // Rejected: it didn't compile, or it was worse than its author said.
        let accepted = matches!(score, Some(s) if s >= 0 && s <= reported);
        tx.execute(
            "UPDATE submissions SET state=?,verified_score=?,detail=?,verified_at=?,verifier=? WHERE id=?",
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

    /// Verified matches since a time, for the app that owns the full project to take in.
    pub fn matches(&self, since: f64) -> Result<Value, Problem> {
        self.read(|db| self.matches_on(db, since))
    }

    fn matches_on(&self, db: &Connection, since: f64) -> Result<Value, Problem> {
        let mut statement = db.prepare_cached(
            "SELECT id,name,unit,vram,matched_source,matched_by,matched_at FROM functions \
             WHERE status='matched' AND matched_at>? ORDER BY matched_at LIMIT 1000",
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
    known: Mutex<HashMap<String, User>>,
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
        if let Some(user) = self.known.lock().unwrap().get(&hashed) {
            return Some(user.clone());
        }
        let user = self
            .server
            .lock()
            .unwrap()
            .query_row("SELECT id,name,role FROM users WHERE token_hash=?", [&hashed], |r| {
                Ok(User { id: r.get(0)?, name: r.get(1)?, role: r.get(2)? })
            })
            .optional()
            .ok()??;
        self.known.lock().unwrap().insert(hashed, user.clone());
        Some(user)
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
    if hosted.user(&headers).is_none() {
        return json_response(StatusCode::UNAUTHORIZED, json!({"error": "a valid API token is required"}));
    }
    json_response(StatusCode::OK, json!({"projects": hosted.slugs()}))
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
    let detail = body.get("detail").and_then(Value::as_str).unwrap_or("").chars().take(4000).collect::<String>();
    blocking(move || project.verdict(&user, id, score, &detail)).await
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
        .route("/health", get(health))
        .layer(DefaultBodyLimit::max(max_body))
        .with_state(hosted)
}

/// `decomp-mcp-gateway hosted ...`
pub async fn main(args: Vec<String>) -> i32 {
    let mut args = args.into_iter();
    let command = args.next().unwrap_or_default();
    let (mut dir, mut listen, mut name, mut role) = (None, "127.0.0.1:8080".to_string(), None, "agent".to_string());
    while let Some(flag) = args.next() {
        let value = args.next();
        match (flag.as_str(), value) {
            ("--dir", Some(v)) => dir = Some(PathBuf::from(v)),
            ("--listen", Some(v)) => listen = v,
            ("--name", Some(v)) => name = Some(v),
            ("--role", Some(v)) => role = v,
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
        Ok(h) => Arc::new(h),
        Err(e) => {
            eprintln!("decomp-mcp-gateway hosted: {e}");
            return 3;
        }
    };
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
            eprintln!("decomp-mcp-gateway hosted: commands are serve and add-user, not {other:?}");
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
        assert_eq!(p.verdict(&verifier, ids[1], Some(4), "4 bytes differ").unwrap()["state"], "rejected");
        assert_eq!(p.verdict(&verifier, ids[0], Some(0), "").unwrap()["matched"], true);
        assert!(p.verdict(&verifier, ids[0], Some(0), "").is_err()); // already verified
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
