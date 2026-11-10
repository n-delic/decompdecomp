//! Tools the gateway answers itself, straight from the app's SQLite databases (read only).
//!
//! Step one of serving decompdecomp's tools without the Python app: get_claims, list_functions and the compact
//! project_info. Each answer is exactly the text the app's handler gives (src/decompnet/app/claim_state.py,
//! app/engine/briefs.py, app/tools.py): the same SQL, Python's number formatting and json.dumps. Whatever this
//! module isn't sure of (a project or helper the app hasn't described, a fork project, a missing row, any database
//! error) it leaves to the reader and the app: `None` is "not mine".
//!
//! The app sends what only it knows over the gateway's standard input every second (`Routes`): which project each
//! helper works, the helpers with state only the app has, the projects it is rechecking or sharing, and each active
//! project's database and the fixed parts of its project_info. The databases are opened read-only; the app keeps
//! writing them.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// Routes older than this are not trusted: the app may have stalled with a change unsent.
const STALE: Duration = Duration::from_secs(5);
pub const TOOLS: [&str; 3] = ["get_claims", "list_functions", "project_info"];

#[derive(Clone, Debug, Default)]
pub struct Project {
    pub id: i64,
    pub db: String,
    pub fork: bool,
    /// project_info(detail=false) before and after its progress line (app/tools.py compact_info_parts).
    pub info_head: String,
    pub info_tail: String,
}

#[derive(Clone, Debug, Default)]
pub struct Routes {
    pub catalog: String,
    pub assignments: HashMap<i64, String>,
    pub pairings: HashMap<i64, String>,
    pub manual: HashMap<i64, String>,
    pub app_only: HashSet<i64>,
    pub busy: HashSet<String>,
    pub projects: HashMap<String, Project>,
}

fn int_keys(value: Option<&Value>) -> HashMap<i64, String> {
    value
        .and_then(Value::as_object)
        .map(|map| {
            map.iter()
                .filter_map(|(k, v)| Some((k.parse().ok()?, v.as_str()?.to_string())))
                .collect()
        })
        .unwrap_or_default()
}

impl Routes {
    pub fn parse(value: &Value) -> Option<Routes> {
        let projects = value
            .get("projects")?
            .as_object()?
            .iter()
            .filter_map(|(slug, p)| {
                Some((
                    slug.clone(),
                    Project {
                        id: p.get("id")?.as_i64()?,
                        db: p.get("db")?.as_str()?.to_string(),
                        fork: p.get("fork").and_then(Value::as_bool).unwrap_or(true),
                        info_head: p.get("info_head")?.as_str()?.to_string(),
                        info_tail: p.get("info_tail")?.as_str()?.to_string(),
                    },
                ))
            })
            .collect();
        let numbers = |key: &str| -> HashSet<i64> {
            value.get(key).and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_i64).collect()).unwrap_or_default()
        };
        let names = |key: &str| -> HashSet<String> {
            value
                .get(key)
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                .unwrap_or_default()
        };
        Some(Routes {
            catalog: value.get("catalog")?.as_str()?.to_string(),
            assignments: int_keys(value.get("assignments")),
            pairings: int_keys(value.get("pairings")),
            manual: int_keys(value.get("manual")),
            app_only: numbers("app_only"),
            busy: names("busy"),
            projects,
        })
    }

    /// The app's own order (app/server.py project_for): a helper it started, a pairing, a manual connection.
    fn project_of(&self, agent: i64) -> Option<&String> {
        self.assignments.get(&agent).or_else(|| self.pairings.get(&agent)).or_else(|| self.manual.get(&agent))
    }
}

/// One answered call, for the app's transcript, session budget and tool usage (app/tool_reader.py apply_report).
pub struct Served {
    pub text: String,
    pub agent_id: i64,
    pub agent_name: String,
    pub slug: String,
}

pub struct Native {
    routes: Mutex<Option<(Routes, Instant)>>,
    connections: Mutex<HashMap<String, Arc<Mutex<Connection>>>>,
}

impl Native {
    pub fn new() -> Native {
        Native { routes: Mutex::new(None), connections: Mutex::new(HashMap::new()) }
    }

    pub fn set_routes(&self, routes: Routes) {
        *self.routes.lock().unwrap() = Some((routes, Instant::now()));
    }

    fn connection(&self, path: &str) -> Option<Arc<Mutex<Connection>>> {
        let mut connections = self.connections.lock().unwrap();
        if let Some(found) = connections.get(path) {
            return Some(found.clone());
        }
        let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX | OpenFlags::SQLITE_OPEN_URI;
        let connection = Connection::open_with_flags(path, flags).ok()?;
        connection.busy_timeout(Duration::from_secs(2)).ok()?;
        let shared = Arc::new(Mutex::new(connection));
        connections.insert(path.to_string(), shared.clone());
        Some(shared)
    }

    /// The answer the app would give, or None to let the reader and the app answer. Blocking: run off the runtime.
    pub fn answer(&self, auth: &str, tool: &str, arguments: &Value) -> Option<Served> {
        if !TOOLS.contains(&tool) {
            return None;
        }
        let routes = {
            let held = self.routes.lock().unwrap();
            let (routes, at) = held.as_ref()?;
            if at.elapsed() > STALE {
                return None;
            }
            routes.clone()
        };
        let token = auth.strip_prefix("Bearer ").unwrap_or(auth).trim();
        if token.is_empty() {
            return None;
        }
        let token_hash = hex(&Sha256::digest(token.as_bytes()));
        let (agent_id, agent_name) = {
            let catalog = self.connection(&routes.catalog)?;
            let catalog = catalog.lock().unwrap();
            catalog
                .query_row("SELECT id,name FROM agents WHERE token_hash=?", [&token_hash], |r| {
                    Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))
                })
                .optional()
                .ok()??
        };
        if routes.app_only.contains(&agent_id) {
            return None;
        }
        let slug = routes.project_of(agent_id)?.clone();
        if routes.busy.contains(&slug) {
            return None;
        }
        let project = routes.projects.get(&slug)?;
        if project.fork {
            return None; // forks answer from their build tree (forkengine.py)
        }
        let db = self.connection(&project.db)?;
        let db = db.lock().unwrap();
        let text = match tool {
            "get_claims" => get_claims(&db, agent_id, &slug)?,
            "list_functions" => list_functions(&db, project, arguments)?,
            "project_info" => project_info(&db, project, arguments)?,
            _ => return None,
        };
        Some(Served { text, agent_id, agent_name, slug })
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

// ------------------------------------------------------------------------------------------- Python's formatting

/// A value as Python's sqlite3 module returns it.
#[derive(Clone, Debug, PartialEq)]
pub enum Py {
    None,
    Int(i64),
    Float(f64),
    Str(String),
    Bool(bool),
}

impl Py {
    fn from(value: ValueRef) -> Option<Py> {
        Some(match value {
            ValueRef::Null => Py::None,
            ValueRef::Integer(i) => Py::Int(i),
            ValueRef::Real(f) => Py::Float(f),
            ValueRef::Text(t) => Py::Str(String::from_utf8(t.to_vec()).ok()?),
            ValueRef::Blob(_) => return None, // (Python would give bytes; nothing here reads one)
        })
    }

    /// str() / an f-string field.
    fn str(&self) -> String {
        match self {
            Py::None => "None".into(),
            Py::Int(i) => i.to_string(),
            Py::Float(f) => float_repr(*f),
            Py::Str(s) => s.clone(),
            Py::Bool(b) => (if *b { "True" } else { "False" }).into(),
        }
    }

    /// repr(), as in a tuple's repr.
    fn repr(&self) -> Option<String> {
        match self {
            Py::Str(_) => None, // (not needed; Python's quoting rules aren't reproduced)
            other => Some(other.str()),
        }
    }

    /// json.dumps of this value.
    fn json(&self) -> String {
        match self {
            Py::None => "null".into(),
            Py::Int(i) => i.to_string(),
            Py::Float(f) if f.is_nan() => "NaN".into(),
            Py::Float(f) if f.is_infinite() => (if *f > 0.0 { "Infinity" } else { "-Infinity" }).into(),
            Py::Float(f) => float_repr(*f),
            Py::Str(s) => json_string(s),
            Py::Bool(b) => (if *b { "true" } else { "false" }).into(),
        }
    }
}

/// Python's repr(float): the shortest digits that read back the same, positional from 1e-4 up to 1e16, else
/// d.ddde+XX (Python/pystrtod.c, mode 'r').
pub fn float_repr(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return (if f > 0.0 { "inf" } else { "-inf" }).into();
    }
    if f == 0.0 {
        return (if f.is_sign_negative() { "-0.0" } else { "0.0" }).into();
    }
    // Rust's {:e} is also the shortest round-trip form: "1.7943442804501233e9".
    let scientific = format!("{:e}", f.abs());
    let (mantissa, exponent) = scientific.split_once('e').unwrap();
    let exponent: i32 = exponent.parse().unwrap();
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let sign = if f < 0.0 { "-" } else { "" };
    if (-4..16).contains(&exponent) {
        let point = exponent + 1; // digits before the decimal point
        let text = if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if point as usize >= digits.len() {
            format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
        } else {
            format!("{}.{}", &digits[..point as usize], &digits[point as usize..])
        };
        format!("{sign}{text}")
    } else {
        let mantissa = if digits.len() > 1 { format!("{}.{}", &digits[..1], &digits[1..]) } else { digits };
        format!("{sign}{mantissa}e{}{:02}", if exponent < 0 { '-' } else { '+' }, exponent.abs())
    }
}

/// json.dumps of a string with ensure_ascii (Python's default).
pub fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

/// json.dumps of a dict, keys in insertion order, with its default ", " and ": " separators.
fn json_object(fields: &[(&str, String)]) -> String {
    let inner: Vec<String> = fields.iter().map(|(k, v)| format!("{}: {}", json_string(k), v)).collect();
    format!("{{{}}}", inner.join(", "))
}

// ---------------------------------------------------------------------------------------------------- the tools

type Row = HashMap<String, Py>;

fn rows(db: &Connection, sql: &str, params: &[&dyn rusqlite::ToSql]) -> Option<Vec<Row>> {
    let mut statement = db.prepare_cached(sql).ok()?;
    let names: Vec<String> = statement.column_names().iter().map(|s| s.to_string()).collect();
    let mut query = statement.query(params).ok()?;
    let mut out = Vec::new();
    while let Some(row) = query.next().ok()? {
        let mut values = HashMap::new();
        for (i, name) in names.iter().enumerate() {
            values.insert(name.clone(), Py::from(row.get_ref(i).ok()?)?);
        }
        out.push(values);
    }
    Some(out)
}

fn field<'a>(row: &'a Row, name: &str) -> Option<&'a Py> {
    row.get(name)
}

/// release_receipts.token: a claim's lease token.
fn claim_token(db: &Connection, function: &Row, agent_id: i64) -> Option<String> {
    let (Py::Int(function_id), Py::Int(project_id)) = (field(function, "id")?, field(function, "project_id")?) else {
        return None;
    };
    let claim = rows(db, "SELECT * FROM claims WHERE function_id=? AND agent_id=?", &[function_id, &agent_id])?;
    let Some(claim) = claim.first() else { return Some(String::new()) };
    let claimed_at = field(claim, "claimed_at")?;
    let claimed_by = field(claim, "agent_id")?;
    let name = field(function, "name")?;
    let (Py::Str(name), Py::Int(claimed_by)) = (name, claimed_by) else { return None };
    let claimed_param: Box<dyn rusqlite::ToSql> = match claimed_at {
        Py::Int(i) => Box::new(*i),
        Py::Float(f) => Box::new(*f),
        _ => return None,
    };
    let event = rows(
        db,
        "SELECT id FROM events WHERE project_id=? AND agent_id=? AND kind='claim' AND detail=? AND created_at>=? \
         ORDER BY id DESC LIMIT 1",
        &[project_id, claimed_by, name, claimed_param.as_ref()],
    )?;
    let event = match event.first().and_then(|e| e.get("id")) {
        Some(Py::Int(id)) => id.to_string(),
        None => "None".into(),
        _ => return None,
    };
    let text = format!("({}, {}, {}, {})", function_id, claimed_by, claimed_at.repr()?, event);
    Some(hex(&Sha256::digest(text.as_bytes()))[..24].to_string())
}

/// claim_state.get_claims
fn get_claims(db: &Connection, agent_id: i64, slug: &str) -> Option<String> {
    let found = rows(
        db,
        "SELECT f.id,f.project_id,f.name,f.unit,f.status,c.claimed_at,c.expires_at FROM claims c \
         JOIN functions f ON f.id=c.function_id JOIN projects p ON p.id=f.project_id \
         WHERE c.agent_id=? AND p.slug=? AND c.expires_at>? ORDER BY f.id LIMIT 21",
        &[&agent_id, &slug, &now()],
    )?;
    let mut claims = Vec::new();
    for function in found.iter().take(20) {
        let Py::Str(name) = field(function, "name")? else { return None };
        let next = format!("get_candidate(function=\"{name}\") to recover current C/revision; get_assembly for target code");
        claims.push(json_object(&[
            ("function", field(function, "name")?.json()),
            ("unit", field(function, "unit")?.json()),
            ("status", field(function, "status")?.json()),
            ("expires_at", field(function, "expires_at")?.json()),
            ("claim_token", json_string(&claim_token(db, function, agent_id)?)),
            ("next", json_string(&next)),
        ]));
    }
    Some(json_object(&[
        ("claims", format!("[{}]", claims.join(", "))),
        ("more", Py::Bool(found.len() > 20).json()),
        ("scope", json_string("Only your live claims in this project; no claims, sources or scores changed.")),
    ]))
}

/// RomNetwork.list_functions (ROM projects)
fn list_functions(db: &Connection, project: &Project, arguments: &Value) -> Option<String> {
    let status = match arguments.get("status") {
        None => "open".to_string(),
        Some(Value::String(s)) => s.clone(),
        _ => return None,
    };
    let limit = match arguments.get("limit") {
        None => 40,
        Some(v) => v.as_i64()?,
    };
    let filter = if status == "all" { "" } else { " AND f.status=?" };
    let sql = format!(
        "SELECT f.name, f.vram, f.n_insns, f.status, f.best_score, a.name AS claimed_by FROM functions f \
         LEFT JOIN claims c ON c.function_id=f.id LEFT JOIN agents a ON a.id=c.agent_id \
         WHERE f.project_id=?{filter} ORDER BY f.vram LIMIT ?"
    );
    let found = if status == "all" {
        rows(db, &sql, &[&project.id, &limit])?
    } else {
        rows(db, &sql, &[&project.id, &status, &limit])?
    };
    if found.is_empty() {
        return Some(format!("No {status} functions."));
    }
    let mut lines = Vec::new();
    for r in &found {
        let Py::Int(vram) = field(r, "vram")? else { return None }; // (None: the app's own error)
        if *vram < 0 {
            return None;
        }
        let mut line = format!(
            "- {} @0x{:08X}: {} insns, {}",
            field(r, "name")?.str(),
            vram,
            field(r, "n_insns")?.str(),
            field(r, "status")?.str()
        );
        let best = field(r, "best_score")?;
        if *best != Py::None {
            line += &format!(", best score {}", best.str());
        }
        match field(r, "claimed_by")? {
            Py::Str(by) if !by.is_empty() => line += &format!(", claimed by {by}"),
            Py::None | Py::Str(_) => {}
            _ => return None,
        }
        lines.push(line);
    }
    Some(lines.join("\n"))
}

/// tools.project_info(detail=false)
fn project_info(db: &Connection, project: &Project, arguments: &Value) -> Option<String> {
    match arguments.get("detail") {
        None | Some(Value::Bool(false)) => {}
        _ => return None,
    }
    let totals = rows(db, "SELECT total,matched,attempts FROM project_totals WHERE project_id=?", &[&project.id])?;
    let totals = totals.first()?;
    Some(format!(
        "{}progress: {}/{} functions matched; {} compiles\n{}",
        project.info_head,
        field(totals, "matched")?.str(),
        field(totals, "total")?.str(),
        field(totals, "attempts")?.str(),
        project.info_tail
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_print_as_python_repr() {
        for (value, python) in [
            (1794344280.4501233, "1794344280.4501233"),
            (1.0, "1.0"),
            (0.1, "0.1"),
            (12.5, "12.5"),
            (1e16, "1e+16"),
            (1.5e-5, "1.5e-05"),
            (0.0001, "0.0001"),
            (123456789012345.0, "123456789012345.0"),
            (-2.25, "-2.25"),
            (0.0, "0.0"),
        ] {
            assert_eq!(float_repr(value), python, "{value}");
        }
    }

    #[test]
    fn strings_dump_as_python_json() {
        assert_eq!(json_string("a\"b\\c\nd"), r#""a\"b\\c\nd""#);
        assert_eq!(json_string("\u{e9}\u{1f600}\u{7f}"), "\"\\u00e9\\ud83d\\ude00\\u007f\"");
        assert_eq!(json_object(&[("a", "1".into()), ("b", "null".into())]), r#"{"a": 1, "b": null}"#);
    }
}
