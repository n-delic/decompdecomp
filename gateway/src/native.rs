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
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Routes older than this are not trusted: the app may have stalled with a change unsent.
const STALE: Duration = Duration::from_secs(5);
pub const TOOLS: [&str; 6] = ["get_claims", "list_functions", "project_info", "get_assembly", "get_context", "guide"];

#[derive(Clone, Debug, Default)]
pub struct Project {
    pub id: i64,
    pub db: String,
    pub fork: bool,
    /// project_info(detail=false) before and after its progress line (app/tools.py compact_info_parts).
    pub info_head: String,
    pub info_tail: String,
    /// The console id guide's texts are chosen by (the overview's platform).
    pub platform: Option<String>,
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
    /// helper -> the gateway's tools its tool settings disable (the app answers, saying so).
    pub hidden: HashMap<i64, HashSet<String>>,
    /// (helper, function) with a compile diff the app keeps: get_assembly's default view starts at its mismatch.
    pub diffs: HashSet<(i64, i64)>,
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
                        platform: p.get("platform").and_then(Value::as_str).map(String::from),
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
            hidden: value
                .get("hidden")
                .and_then(Value::as_object)
                .map(|map| {
                    map.iter()
                        .filter_map(|(k, v)| {
                            Some((k.parse().ok()?, v.as_array()?.iter().filter_map(|t| t.as_str().map(String::from)).collect()))
                        })
                        .collect()
                })
                .unwrap_or_default(),
            diffs: value
                .get("diffs")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|pair| Some((pair.get(0)?.as_i64()?, pair.get(1)?.as_i64()?))).collect())
                .unwrap_or_default(),
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

/// What guide answers (tool_reader.guides), sent by the app once per platform.
#[derive(Default)]
pub struct Guides {
    tool_help: HashMap<String, String>,
    missing_tool_help: String,
    platforms: HashMap<String, (String, HashMap<String, String>)>,
}

impl Guides {
    pub fn parse(value: &Value) -> Option<Guides> {
        let strings = |v: &Value| -> HashMap<String, String> {
            v.as_object()
                .map(|m| m.iter().filter_map(|(k, v)| Some((k.clone(), v.as_str()?.to_string()))).collect())
                .unwrap_or_default()
        };
        Some(Guides {
            tool_help: strings(value.get("tool_help")?),
            missing_tool_help: value.get("missing_tool_help")?.as_str()?.to_string(),
            platforms: value
                .get("platforms")?
                .as_object()?
                .iter()
                .filter_map(|(platform, g)| {
                    Some((platform.clone(), (g.get("listing")?.as_str()?.to_string(), strings(g.get("topics")?))))
                })
                .collect(),
        })
    }
}

pub struct Native {
    routes: Mutex<Option<(Routes, Instant)>>,
    guides: Mutex<Option<Arc<Guides>>>,
    connections: Mutex<HashMap<String, Arc<Mutex<Connection>>>>,
}

impl Native {
    pub fn new() -> Native {
        Native { routes: Mutex::new(None), guides: Mutex::new(None), connections: Mutex::new(HashMap::new()) }
    }

    pub fn set_guides(&self, guides: Guides) {
        *self.guides.lock().unwrap() = Some(Arc::new(guides));
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

    /// The project an agent's token belongs to, from the routes the app last sent (however old: an agent's project
    /// doesn't change). Blocking: run off the runtime.
    pub fn project_of_bearer(&self, auth: &str) -> Option<String> {
        let routes = self.routes.lock().unwrap().as_ref()?.0.clone();
        let token = auth.strip_prefix("Bearer ").unwrap_or(auth).trim();
        if token.is_empty() {
            return None;
        }
        let token_hash = hex(&Sha256::digest(token.as_bytes()));
        let agent_id = {
            let catalog = self.connection(&routes.catalog)?;
            let catalog = catalog.lock().unwrap();
            catalog
                .query_row("SELECT id FROM agents WHERE token_hash=?", [&token_hash], |r| r.get::<_, i64>(0))
                .optional()
                .ok()??
        };
        routes.project_of(agent_id).cloned()
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
        if routes.app_only.contains(&agent_id) || routes.hidden.get(&agent_id).is_some_and(|h| h.contains(tool)) {
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
            "get_context" => get_context(&db, agent_id, &slug, project, arguments)?,
            "get_assembly" => get_assembly(&db, agent_id, &agent_name, &slug, project, &routes, arguments)?,
            "guide" => {
                let guides = self.guides.lock().unwrap().clone()?;
                guide(&guides, project, arguments)?
            }
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

// ------------------------------------------------------------------------------------- stored text and symbols

/// A text column as the app's views give it (large_text_storage.py): plain text, or the SHA-256 of a payload in
/// text_payloads, zlib-compressed (payload_storage.unpack, checksum included).
fn stored_text(db: &Connection, value: ValueRef) -> Option<Option<String>> {
    match value {
        ValueRef::Null => Some(None),
        ValueRef::Text(t) => Some(Some(String::from_utf8(t.to_vec()).ok()?)),
        ValueRef::Blob(digest) => {
            let key = hex(digest);
            let payload: Vec<u8> = db
                .query_row("SELECT payload FROM text_payloads WHERE hash=?", [&key], |r| r.get(0))
                .optional()
                .ok()??;
            let raw = inflate(&payload)?;
            if hex(&Sha256::digest(&raw)) != key {
                return None;
            }
            Some(Some(String::from_utf8(raw).ok()?))
        }
        _ => None,
    }
}

fn inflate(data: &[u8]) -> Option<Vec<u8>> {
    use std::io::Read;
    let mut out = Vec::new();
    flate2::read::ZlibDecoder::new(data).read_to_end(&mut out).ok()?;
    Some(out)
}

#[derive(Clone)]
struct Symbol {
    name: String,
    size: i64,
    kind: String,
}

/// rom/symbols.py SymbolTable, as the engine builds it (core.py symbols): by name, by address, sorted starts.
struct SymbolTable {
    by_name: HashMap<String, Symbol>,
    by_addr: std::collections::BTreeMap<i64, Symbol>,
}

impl SymbolTable {
    fn load(db: &Connection, project: i64) -> Option<SymbolTable> {
        let found = rows(
            db,
            "SELECT s.name, s.address, s.size, s.kind, f.id IS NOT NULL AS fn FROM symbols s \
             LEFT JOIN functions f ON f.project_id=s.project_id AND f.name=s.name WHERE s.project_id=?",
            &[&project],
        )?;
        let mut table = SymbolTable { by_name: HashMap::new(), by_addr: std::collections::BTreeMap::new() };
        for r in &found {
            let (Py::Str(name), Py::Int(address), Py::Int(size), Py::Str(kind)) =
                (field(r, "name")?, field(r, "address")?, field(r, "size")?, field(r, "kind")?)
            else {
                return None;
            };
            let symbol = Symbol { name: name.clone(), size: *size, kind: kind.clone() };
            // add(alias=True): the address's last symbol names it; every name stays findable
            table.by_name.insert(name.clone(), symbol.clone());
            table.by_addr.insert(*address, symbol);
        }
        Some(table)
    }

    fn name_at(&self, address: i64) -> (String, i64) {
        if let Some(symbol) = self.by_addr.get(&address) {
            return (symbol.name.clone(), 0);
        }
        if let Some((start, symbol)) = self.by_addr.range(..=address).next_back() {
            if address < start + symbol.size {
                return (symbol.name.clone(), address - start);
            }
        }
        (format!("D_{address:08X}"), 0)
    }

    fn label(&self, address: i64) -> String {
        match self.name_at(address) {
            (name, 0) => name,
            (name, offset) => format!("{name}+0x{offset:X}"),
        }
    }
}

/// The symbol table, kept until the database's symbol or naming revision for the project moves (store.read_version
/// kinds the engine's own caches follow).
type SymbolCache = HashMap<(String, i64), ((i64, i64), Arc<SymbolTable>)>;
static SYMBOLS: std::sync::OnceLock<Mutex<SymbolCache>> = std::sync::OnceLock::new();

fn symbols_for(db: &Connection, project: i64) -> Option<Arc<SymbolTable>> {
    let revision = |kind: &str| -> i64 {
        db.query_row("SELECT revision FROM read_revisions WHERE kind=?", [format!("{kind}:{project}")], |r| r.get(0))
            .unwrap_or(0)
    };
    let version = (revision("scope_symbols"), revision("naming"));
    let key = (db.path().unwrap_or("").to_string(), project);
    let cache = SYMBOLS.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some((seen, table)) = cache.lock().unwrap().get(&key) {
        if *seen == version {
            return Some(table.clone());
        }
    }
    let table = Arc::new(SymbolTable::load(db, project)?);
    cache.lock().unwrap().insert(key, (version, table.clone()));
    Some(table)
}

fn pattern(cell: &'static std::sync::OnceLock<fancy_regex::Regex>, text: &str) -> &'static fancy_regex::Regex {
    cell.get_or_init(|| fancy_regex::Regex::new(text).expect("a valid pattern"))
}

static AUTO_TOKEN: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
static INSN: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
static GLABEL: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
static LABEL: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
static LOCAL_REF: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
static META_COLUMNS: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
static ADDRESS_COLUMN: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
const LOCAL_NAME: &str = r"\.?L([0-9A-F]{8})(?:_(?:overlay_[0-9]{3}|ovl_[0-9]{2,3}))?";

/// Python's str.splitlines (what the engine splits assembly with).
fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        let breaks = matches!(c, '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}');
        if breaks {
            lines.push(&text[start..i]);
            let mut next = i + c.len_utf8();
            if c == '\r' {
                if let Some(&(j, '\n')) = chars.peek() {
                    chars.next();
                    next = j + 1;
                }
            }
            start = next;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// RomNetwork.rename_tokens: automatic names under their current names (func_80001234 -> deflate).
fn rename_tokens(symbols: &SymbolTable, text: &str) -> Option<String> {
    let re = pattern(&AUTO_TOKEN, r"\b(?:func|D|B|jtbl|RO|STR|FLT|DBL)_[0-9A-F]{8}\b");
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for found in re.find_iter(text) {
        let found = found.ok()?;
        let original = found.as_str();
        let address = i64::from_str_radix(&original[original.len() - 8..], 16).ok()?;
        let (name, offset) = symbols.name_at(address);
        let replacement = if original.starts_with("func_") {
            match symbols.by_name.get(&name) {
                Some(symbol) if offset == 0 && symbol.kind == "func" => name,
                _ => original.to_string(),
            }
        } else {
            symbols.label(address)
        };
        out.push_str(&text[last..found.start()]);
        out.push_str(&replacement);
        last = found.end();
    }
    out.push_str(&text[last..]);
    Some(out)
}

/// rom/split.py draft_asm: labels older imports lost, recovered from recorded instruction addresses.
fn draft_asm(text: &str, name: &str, vram: Option<i64>) -> Option<String> {
    let insn = pattern(&INSN, r"^\s*/\* ([0-9A-F]+) ([0-9A-F]{8}) ([0-9A-F]{8}) \*/\s+(\S+)\s*(.*?)\s*$");
    let glabel = pattern(&GLABEL, r"^\s*glabel\s+(\S+)");
    let label = pattern(&LABEL, &format!(r"^\s*(?:jlabel\s+)?({LOCAL_NAME}):?\s*$"));
    let local_ref = pattern(&LOCAL_REF, &format!(r"(?<![\w.$]){LOCAL_NAME}(?![\w.$])"));
    let address_of = |line: &str| -> Option<Option<i64>> {
        match insn.captures(line).ok()? {
            Some(c) => Some(Some(i64::from_str_radix(c.get(2)?.as_str(), 16).ok()?)),
            None => Some(None),
        }
    };
    let lines = splitlines(text);
    let mut first = None;
    for (i, line) in lines.iter().enumerate() {
        if let Some(address) = address_of(line)? {
            first = Some((i, address));
            break;
        }
    }
    let Some((first_line, first_address)) = first else { return Some(text.to_string()) };
    if vram.is_some_and(|v| v != first_address) {
        return Some(text.to_string());
    }
    if vram.is_none() {
        let mut entries = Vec::new();
        for line in &lines {
            if let Some(c) = glabel.captures(line).ok()? {
                entries.push(c.get(1)?.as_str().to_string());
            }
        }
        if entries != [name.to_string()] {
            return Some(text.to_string());
        }
    }
    let mut entry = None;
    for line in &lines[..first_line] {
        if let Some(c) = glabel.captures(line).ok()? {
            entry = Some(c.get(1)?.as_str().to_string());
            break;
        }
    }
    let mut text = text.to_string();
    if let Some(entry) = entry.filter(|e| e != name) {
        let rename = fancy_regex::Regex::new(&format!(r"(?<![\w.]){}(?!\w)", fancy_regex::escape(&entry))).ok()?;
        text = rename.replace_all(&text, fancy_regex::NoExpand(name)).into_owned();
    }
    let lines = splitlines(&text);
    let mut defined = HashSet::new();
    for line in &lines {
        if let Some(c) = label.captures(line).ok()? {
            defined.insert(c.get(1)?.as_str().to_string());
        }
    }
    let mut missing: Vec<(String, i64)> = Vec::new(); // (a dict: one entry per label, the last address)
    let joined = format!("{text}\n");
    for found in local_ref.captures_iter(&joined) {
        let c = found.ok()?;
        let whole = c.get(0)?.as_str().to_string();
        if defined.contains(&whole) {
            continue;
        }
        let address = i64::from_str_radix(c.get(1)?.as_str(), 16).ok()?;
        match missing.iter_mut().find(|(l, _)| *l == whole) {
            Some(slot) => slot.1 = address,
            None => missing.push((whole, address)),
        }
    }
    let mut by_address: HashMap<i64, Vec<String>> = HashMap::new();
    for (l, address) in missing {
        by_address.entry(address).or_default().push(l);
    }
    let mut out = Vec::new();
    for line in &lines {
        if let Some(address) = address_of(line)? {
            if let Some(mut labels) = by_address.remove(&address) {
                labels.sort();
                out.extend(labels.into_iter().map(|l| format!("{l}:")));
            }
        }
        out.push(line.to_string());
    }
    Some(out.join("\n") + if text.ends_with('\n') { "\n" } else { "" })
}

/// Python's truthiness of a JSON value (the agent's selection policy flags).
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// agent_tokens.assembly_text: presentation columns removed unless the helper's selection keeps them.
fn assembly_text(db: &Connection, slug: &str, agent_name: &str, text: &str) -> Option<String> {
    let policy: Option<String> = db
        .query_row("SELECT policy FROM agent_selection WHERE project=? AND agent=?", [slug, agent_name], |r| r.get(0))
        .optional()
        .ok()?;
    let keep = match policy {
        Some(policy) => serde_json::from_str::<Value>(&policy).ok()?.get("assembly_metadata").is_some_and(truthy),
        None => false,
    };
    if keep {
        return Some(text.to_string());
    }
    let meta = pattern(&META_COLUMNS, r"(?m)^(\s*)/\*\s*[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s*\*/[ \t]*");
    let column = pattern(&ADDRESS_COLUMN, r"(?m)^[ \t]*(?:0x)?[0-9a-fA-F]{8,16}[ \t]+(?=[A-Za-z.])");
    let text = meta.replace_all(text, "$1").into_owned();
    Some(column.replace_all(&text, "    ").into_owned())
}

/// The function a helper names, when it is exactly a stored name (anything looser is the app's: it resolves
/// claims, units, addresses and C++ names).
fn exact_function(db: &Connection, slug: &str, name: &str) -> Option<Row> {
    let found = rows(
        db,
        "SELECT f.id,f.project_id,f.name,f.unit,f.vram FROM functions f JOIN projects p ON p.id=f.project_id \
         WHERE p.slug=? AND f.name=?",
        &[&slug, &name],
    )?;
    found.into_iter().next()
}

fn integer(arguments: &Value, key: &str, default: i64) -> Option<i64> {
    match arguments.get(key) {
        None => Some(default),
        Some(v) => v.as_i64(),
    }
}

/// agent_tokens.assembly for ROM projects.
fn get_assembly(db: &Connection, agent_id: i64, agent_name: &str, slug: &str, project: &Project, routes: &Routes,
                arguments: &Value) -> Option<String> {
    let name = arguments.get("function")?.as_str()?;
    let mut start = integer(arguments, "start", 0)?;
    let mut limit = integer(arguments, "limit", 60)?;
    let mut focus = match arguments.get("focus") {
        None => String::new(),
        Some(Value::String(s)) => s.clone(),
        _ => return None,
    };
    let f = exact_function(db, slug, name)?;
    let Py::Int(function_id) = field(&f, "id")? else { return None };
    let kept = routes.diffs.contains(&(agent_id, *function_id));
    let mut automatic = focus.is_empty() && start == 0;
    if focus == "page" {
        focus.clear();
        automatic = false;
    }
    if automatic {
        if kept {
            return None; // its first mismatch: the app's
        }
        focus = "body".into();
    }
    let Py::Str(fn_name) = field(&f, "name")? else { return None };
    let vram = match field(&f, "vram")? {
        Py::Int(v) => Some(*v),
        Py::None => None,
        _ => return None,
    };
    let asm = {
        let mut statement = db.prepare_cached("SELECT asm FROM functions WHERE id=?").ok()?;
        let mut found = statement.query([function_id]).ok()?;
        let row = found.next().ok()??;
        stored_text(db, row.get_ref(0).ok()?)??
    };
    let symbols = symbols_for(db, project.id)?;
    let text = draft_asm(&rename_tokens(&symbols, &asm)?, fn_name, vram)?;
    let lines = splitlines(&text);
    let mut prefix = String::new();
    if !focus.is_empty() {
        let hits: Vec<usize> = if focus == "body" {
            vec![20.min(lines.len().saturating_sub(1))]
        } else if focus.starts_with("mismatch") {
            let parts: Vec<&str> = focus.split(':').collect();
            if parts.len() > 2 {
                return None; // (the app's error)
            }
            if parts.len() == 2 && parts[1].trim().parse::<i64>().ok().filter(|n| *n >= 1).is_none() {
                return None;
            }
            if kept {
                return None;
            }
            Vec::new() // nothing kept: no mismatch to find
        } else {
            if focus.chars().count() > 160 {
                return None;
            }
            let anchor = fancy_regex::Regex::new(&format!(r"(?<![\w]){}(?![\w])", fancy_regex::escape(&focus))).ok()?;
            let mut hits = Vec::new();
            for (i, line) in lines.iter().enumerate() {
                if anchor.is_match(line).ok()? {
                    hits.push(i);
                }
            }
            hits
        };
        if hits.is_empty() && focus.starts_with("mismatch") {
            let page = json!({"function": name, "start": start, "limit": limit.clamp(1, 40), "focus": "page"});
            let fallback = get_assembly(db, agent_id, agent_name, slug, project, routes, &page)?;
            return Some(format!(
                "Mismatch location unavailable for the current evidence; showing a bounded target page instead. No \
                 mismatch location or score inferred.\n{fallback}"
            ));
        }
        if hits.is_empty() {
            return Some("No target region located for this focus. Use paged get_assembly or a shorter symbol anchor.".into());
        }
        start = (hits[0] as i64 - 8).max(0);
        limit = limit.clamp(1, 40);
        let listed: Vec<String> = hits.iter().take(10).map(|h| h.to_string()).collect();
        prefix = format!(
            "Focus at target line {}; matching lines: {}. Use focus=\"page\" with start/limit for exact paging; \
             mismatch:N selects another differing instruction.\n",
            hits[0],
            listed.join(", ")
        );
    }
    let start = start.max(0) as usize;
    let limit = limit.clamp(1, 200) as usize;
    let end = (start + limit).min(lines.len());
    let next = if end < lines.len() {
        format!("Next page: get_assembly(function=\"{fn_name}\", start={end}, limit={limit}, focus=\"page\").")
    } else {
        "End of target assembly.".into()
    };
    let shown = if start < end { lines[start..end].join("\n") } else { String::new() };
    Some(format!(
        "{prefix}Target assembly lines {start}:{end} of {} (zero-based, end exclusive). {next}\n{}",
        lines.len(),
        assembly_text(db, slug, agent_name, &shown)?
    ))
}

/// The latest header of a unit, else the project-wide one (Network._context).
fn latest_context(db: &Connection, project: i64, unit: &str) -> Option<Option<(i64, Py)>> {
    let query = |unit: &str| -> Option<Option<(i64, Py)>> {
        let mut statement = db
            .prepare_cached(
                "SELECT version, header_hash, (SELECT payload FROM header_blobs WHERE hash=header_hash) \
                 FROM context_history WHERE project_id=? AND unit=? ORDER BY version DESC LIMIT 1",
            )
            .ok()?;
        let mut found = statement.query(params_of(project, unit)).ok()?;
        let Some(row) = found.next().ok()? else { return Some(None) };
        let version: i64 = row.get(0).ok()?;
        let digest: Option<String> = row.get(1).ok()?;
        let header = match digest {
            None => Py::None,
            Some(digest) => {
                let payload: Vec<u8> = row.get::<_, Option<Vec<u8>>>(2).ok()??;
                let raw = inflate(&payload)?;
                if hex(&Sha256::digest(&raw)) != digest {
                    return None;
                }
                Py::Str(String::from_utf8(raw).ok()?)
            }
        };
        Some(Some((version, header)))
    };
    match query(unit)? {
        Some(found) => Some(Some(found)),
        None if !unit.is_empty() => query(""),
        None => Some(None),
    }
}

fn params_of(project: i64, unit: &str) -> (i64, String) {
    (project, unit.to_string())
}

/// tools._get_context / Network.get_context
fn get_context(db: &Connection, agent_id: i64, slug: &str, project: &Project, arguments: &Value) -> Option<String> {
    let mut function = match arguments.get("function") {
        None => String::new(),
        Some(Value::String(s)) => s.clone(),
        _ => return None,
    };
    if function.trim().is_empty() {
        let held = rows(
            db,
            "SELECT f.name FROM functions f JOIN claims c ON c.function_id=f.id JOIN projects p ON p.id=f.project_id \
             WHERE c.agent_id=? AND p.slug=?",
            &[&agent_id, &slug],
        )?;
        function = if held.len() == 1 {
            let Py::Str(name) = field(&held[0], "name")? else { return None };
            name.clone()
        } else {
            String::new()
        };
    }
    let (project_slug, ctx) = if !function.is_empty() {
        let f = exact_function(db, slug, &function)?;
        let unit = match field(&f, "unit")? {
            Py::Str(u) => u.clone(),
            Py::None => String::new(),
            _ => return None,
        };
        let Py::Int(pid) = field(&f, "project_id")? else { return None };
        let owner = rows(db, "SELECT slug FROM projects WHERE id=?", &[pid])?;
        let Py::Str(owner) = field(owner.first()?, "slug")? else { return None };
        (owner.clone(), latest_context(db, *pid, &unit)??)
    } else {
        let per_unit = rows(
            db,
            "SELECT 1 FROM context_history WHERE project_id=? AND unit != '' LIMIT 1",
            &[&project.id],
        )?;
        if !per_unit.is_empty() {
            return None; // (the app says to name a function)
        }
        (slug.to_string(), latest_context(db, project.id, "")??)
    };
    Some(format!(
        "// context.h for {project_slug} — version {} (prepended to every compile as #include \"context.h\")\n{}",
        ctx.0,
        ctx.1.str()
    ))
}

/// tools.guide_text(topic, the project's platform), from the texts the app sent.
fn guide(guides: &Guides, project: &Project, arguments: &Value) -> Option<String> {
    let topic = match arguments.get("topic") {
        None => String::new(),
        Some(Value::String(s)) => s.clone(),
        _ => return None,
    };
    if !topic.is_ascii() {
        return None; // (Python's lower() and Rust's agree on ASCII)
    }
    let topic = topic.trim().to_ascii_lowercase();
    if let Some(name) = topic.strip_prefix("tool:") {
        return Some(guides.tool_help.get(name).cloned().unwrap_or_else(|| guides.missing_tool_help.clone()));
    }
    let (listing, topics) = guides.platforms.get(project.platform.as_ref()?)?;
    Some(topics.get(&topic).cloned().unwrap_or_else(|| listing.clone()))
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
