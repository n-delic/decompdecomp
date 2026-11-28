//! Direct read-only SQLite answers for high-frequency window GET endpoints.
//!
//! Answers pure-SQLite window reads directly from `decomp.db` (and `project-databases/<id>.sqlite3`)
//! without touching Python or the reader subprocess. Any auth mismatch, missing database, unsupported
//! query, or SQLite error returns `None` so `window.rs` falls back to the reader/app unchanged.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::http::{header, HeaderMap, Method, Uri};
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};

const TOKEN_AGENT: &str = "decompdecomp-window/";
const EVENTS_PER_ASK: i64 = 200;

#[derive(Clone, Debug)]
pub struct WindowDb {
    catalog_path: PathBuf,
    window_secret: String,
}

impl WindowDb {
    pub fn new(catalog_path: PathBuf, window_secret: String) -> Option<WindowDb> {
        if window_secret.is_empty() {
            return None;
        }
        Some(WindowDb { catalog_path, window_secret })
    }

    pub fn from_config(
        window_db: Option<&str>,
        state: Option<&str>,
        window_secret: Option<String>,
    ) -> Option<WindowDb> {
        let secret = window_secret.filter(|s| !s.is_empty())?;
        let catalog_path = if let Some(path) = window_db {
            PathBuf::from(path)
        } else if let Some(state_path) = state {
            Path::new(state_path).parent()?.join("decomp.db")
        } else {
            return None;
        };
        Self::new(catalog_path, secret)
    }

    /// Match the app's guard for direct window reads: no `Origin` header, and a `User-Agent` carrying
    /// the per-launch window secret (`decompdecomp-window/<secret>`).
    fn authorized(&self, headers: &HeaderMap) -> bool {
        if headers.contains_key(header::ORIGIN) {
            return false;
        }
        let ua = headers.get(header::USER_AGENT).and_then(|v| v.to_str().ok()).unwrap_or("");
        let token = token_of(ua);
        constant_time_eq(token.as_bytes(), self.window_secret.as_bytes())
    }

    fn project_db_path(&self, pid: i64) -> PathBuf {
        if let Some(parent) = self.catalog_path.parent() {
            let split = parent.join("project-databases").join(format!("{pid}.sqlite3"));
            if split.is_file() {
                return split;
            }
        }
        self.catalog_path.clone()
    }

    /// Answer a supported read-only window GET request, or return `None` to fall back to reader/app.
    pub fn answer(&self, method: &Method, uri: &Uri, headers: &HeaderMap) -> Option<Value> {
        if method != Method::GET || !self.authorized(headers) {
            return None;
        }
        let query = crate::window::pairs(uri.query().unwrap_or(""));
        match uri.path() {
            "/api/stats" => {
                let slug = query_param(&query, "slug").filter(|s| !s.is_empty())?;
                self.stats(slug)
            }
            "/api/events" => {
                if query_param(&query, "recent").is_some_and(|v| !v.is_empty()) {
                    return None;
                }
                let after = match query_param(&query, "after") {
                    Some(raw) if !raw.is_empty() && raw.bytes().all(|b| b.is_ascii_digit()) => {
                        Some(raw.parse::<i64>().ok()?)
                    }
                    _ => None,
                };
                self.events_after(after, EVENTS_PER_ASK)
            }
            _ => None,
        }
    }

    /// Exact match for `GuiMixin.stats(slug)` in `src/decompnet/app/engine/gui.py`.
    fn stats(&self, slug: &str) -> Option<Value> {
        let catalog = open_readonly(&self.catalog_path)?;
        let pid: i64 = catalog
            .query_row("SELECT id FROM projects WHERE slug=?1", [slug], |r| r.get(0))
            .ok()?;

        let mut names: HashMap<i64, String> = HashMap::new();
        {
            let mut stmt = catalog.prepare("SELECT id, name FROM agents").ok()?;
            let rows = stmt
                .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
                .ok()?;
            for row in rows {
                let (id, name) = row.ok()?;
                names.insert(id, name);
            }
        }

        let proj_path = self.project_db_path(pid);
        let split_conn = if proj_path != self.catalog_path {
            Some(open_readonly(&proj_path)?)
        } else {
            None
        };
        let proj = split_conn.as_ref().unwrap_or(&catalog);

        #[derive(Default)]
        struct AgentHistory {
            compiles: i64,
            worked: HashSet<i64>,
            improved: HashSet<i64>,
            last: Option<f64>,
        }

        let mut history: HashMap<i64, AgentHistory> = HashMap::new();
        let mut best: HashMap<i64, i64> = HashMap::new();
        {
            let mut stmt = proj
                .prepare(
                    "SELECT t.id, t.function_id, t.agent_id, t.compiled, t.score, t.created_at, f.n_insns \
                     FROM functions f JOIN attempts t ON t.function_id=f.id \
                     WHERE f.project_id=?1 ORDER BY t.function_id, t.id",
                )
                .ok()?;
            let rows = stmt
                .query_map([pid], |r| {
                    Ok((
                        r.get::<_, i64>(1)?,
                        r.get::<_, Option<i64>>(2)?,
                        r.get::<_, i64>(3)?,
                        r.get::<_, Option<i64>>(4)?,
                        r.get::<_, Option<f64>>(5)?,
                        r.get::<_, Option<i64>>(6)?,
                    ))
                })
                .ok()?;
            for row in rows {
                let (fid, aid_opt, compiled, score_opt, created_at, n_insns) = row.ok()?;
                if let Some(aid) = aid_opt {
                    let entry = history.entry(aid).or_default();
                    entry.compiles += 1;
                    entry.worked.insert(fid);
                    if let Some(at) = created_at {
                        entry.last = Some(entry.last.map_or(at, |prev| prev.max(at)));
                    }
                    if compiled != 0 {
                        if let Some(score) = score_opt {
                            let prev_best = best.get(&fid).copied().unwrap_or(n_insns.unwrap_or(0) * 100);
                            if score >= 0 && score < prev_best {
                                entry.improved.insert(fid);
                            }
                        }
                    }
                }
                if compiled != 0 {
                    if let Some(score) = score_opt {
                        best.entry(fid)
                            .and_modify(|b| *b = (*b).min(score))
                            .or_insert(score);
                    }
                }
            }
        }

        let mut matched: HashMap<String, i64> = HashMap::new();
        {
            let mut stmt = proj
                .prepare(
                    "SELECT matched_agent_id, COUNT(*) FROM functions \
                     WHERE project_id=?1 AND status='matched' GROUP BY matched_agent_id",
                )
                .ok()?;
            let rows = stmt
                .query_map([pid], |r| Ok((r.get::<_, Option<i64>>(0)?, r.get::<_, i64>(1)?)))
                .ok()?;
            for row in rows {
                let (aid_opt, count) = row.ok()?;
                if let Some(name) = aid_opt.and_then(|aid| names.get(&aid)) {
                    matched.insert(name.clone(), count);
                }
            }
        }

        let mut polished: HashMap<String, i64> = HashMap::new();
        {
            let mut stmt = proj
                .prepare(
                    "SELECT agent_id, COUNT(DISTINCT detail) FROM events \
                     WHERE project_id=?1 AND kind='readable' GROUP BY agent_id",
                )
                .ok()?;
            let rows = stmt
                .query_map([pid], |r| Ok((r.get::<_, Option<i64>>(0)?, r.get::<_, i64>(1)?)))
                .ok()?;
            for row in rows {
                let (aid_opt, count) = row.ok()?;
                if let Some(name) = aid_opt.and_then(|aid| names.get(&aid)) {
                    polished.insert(name.clone(), count);
                }
            }
        }

        let mut board: Vec<(i64, i64, Value)> = Vec::new();
        for (aid, r) in &history {
            let Some(name) = names.get(aid) else { continue };
            let m = matched.remove(name).unwrap_or(0);
            let p = polished.remove(name).unwrap_or(0);
            let imp = r.improved.len() as i64;
            board.push((
                m,
                r.compiles,
                json!({
                    "agent": name,
                    "compiles": r.compiles,
                    "worked": r.worked.len(),
                    "matched": m,
                    "last": r.last,
                    "polished": p,
                    "improved": imp,
                }),
            ));
        }
        for (name, m) in matched {
            let p = polished.remove(&name).unwrap_or(0);
            board.push((
                m,
                0,
                json!({
                    "agent": name,
                    "compiles": 0,
                    "matched": m,
                    "last": Value::Null,
                    "polished": p,
                    "improved": 0,
                }),
            ));
        }
        for (name, p) in polished {
            board.push((
                0,
                0,
                json!({
                    "agent": name,
                    "compiles": 0,
                    "matched": 0,
                    "polished": p,
                    "last": Value::Null,
                    "improved": 0,
                }),
            ));
        }
        board.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| b.1.cmp(&a.1)));
        let agents: Vec<Value> = board.into_iter().map(|(_, _, v)| v).collect();

        let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs_f64();
        let mut per_hour = [0i64; 24];
        {
            let mut stmt = proj
                .prepare(
                    "SELECT matched_at FROM functions \
                     WHERE project_id=?1 AND status='matched' AND matched_at > ?2",
                )
                .ok()?;
            let rows = stmt
                .query_map(rusqlite::params![pid, now - 86400.0], |r| r.get::<_, Option<f64>>(0))
                .ok()?;
            for row in rows {
                if let Some(matched_at) = row.ok()? {
                    let bucket = ((now - matched_at) / 3600.0).floor() as i64;
                    let idx = (23 - bucket.clamp(0, 23)) as usize;
                    per_hour[idx] += 1;
                }
            }
        }

        let compiles_last_hour: i64 = proj
            .query_row(
                "SELECT COUNT(*) FROM attempts t JOIN functions f ON f.id=t.function_id \
                 WHERE f.project_id=?1 AND t.created_at > ?2",
                rusqlite::params![pid, now - 3600.0],
                |r| r.get(0),
            )
            .ok()?;

        Some(json!({
            "agents": agents,
            "matches_per_hour": per_hour,
            "compiles_last_hour": compiles_last_hour,
        }))
    }

    /// Exact match for `GuiMixin.events_after(after, limit)` when using the single catalog DB
    /// (falls back to upstream when per-project `activity_index` sync is active).
    fn events_after(&self, after: Option<i64>, limit: i64) -> Option<Value> {
        let catalog = open_readonly(&self.catalog_path)?;
        // If per-project databases / activity_index are active, let the app run `activity_index.sync`.
        if catalog
            .query_row("SELECT 1 FROM settings WHERE key='activity-index-ready'", [], |_| Ok(()))
            .is_ok()
        {
            return None;
        }
        if let Some(parent) = self.catalog_path.parent() {
            let split_dir = parent.join("project-databases");
            if split_dir.is_dir()
                && std::fs::read_dir(&split_dir)
                    .ok()
                    .is_some_and(|mut d| d.any(|e| e.ok().is_some_and(|f| f.path().extension().is_some_and(|x| x == "sqlite3"))))
            {
                return None;
            }
        }

        let last: i64 = catalog
            .query_row("SELECT COALESCE(MAX(id), 0) FROM events", [], |r| r.get(0))
            .ok()?;
        let Some(after_id) = after else {
            return Some(json!({"last": last, "events": []}));
        };

        let mut stmt = catalog
            .prepare(
                "SELECT e.id, e.kind, e.detail, e.created_at, a.name AS agent, p.slug, p.name AS project \
                 FROM events e \
                 JOIN projects p ON p.id=e.project_id \
                 LEFT JOIN agents a ON a.id=e.agent_id \
                 WHERE NOT (COALESCE(a.name, '') IN ('progress-judge', '@progress-judge') \
                            OR COALESCE(a.name, '') LIKE 'progress-judge.%') \
                   AND e.kind IN ('match','link','autopilot','compiler','reopen','not_c','knowledge',\
                                  'collab','import','reimport','files','paused','stopped','regression') \
                   AND e.id > ?1 AND e.id <= ?2 \
                 ORDER BY e.id LIMIT ?3",
            )
            .ok()?;
        let rows = stmt
            .query_map(rusqlite::params![after_id, last, limit], |r| {
                Ok(json!({
                    "id": r.get::<_, i64>(0)?,
                    "kind": r.get::<_, Option<String>>(1)?,
                    "detail": r.get::<_, Option<String>>(2)?,
                    "created_at": r.get::<_, Option<f64>>(3)?,
                    "agent": r.get::<_, Option<String>>(4)?,
                    "slug": r.get::<_, String>(5)?,
                    "project": r.get::<_, String>(6)?,
                }))
            })
            .ok()?;

        let mut events = Vec::new();
        for row in rows {
            events.push(row.ok()?);
        }
        let out_last = if events.len() as i64 == limit {
            events.last()?.get("id")?.as_i64()?
        } else {
            last
        };
        Some(json!({"last": out_last, "events": events}))
    }
}

fn open_readonly(path: &Path) -> Option<Connection> {
    if !path.is_file() {
        return None;
    }
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let conn = Connection::open_with_flags(path, flags).ok()?;
    conn.busy_timeout(Duration::from_millis(250)).ok()?;
    Some(conn)
}

fn query_param<'a>(pairs: &'a [(String, String)], name: &str) -> Option<&'a str> {
    pairs.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
}

fn token_of(user_agent: &str) -> &str {
    let Some((_, rest)) = user_agent.split_once(TOKEN_AGENT) else {
        return "";
    };
    rest.split_once(' ').map_or(rest, |(token, _)| token)
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (&x, &y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn temp_dir(name: &str) -> PathBuf {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!("decomp-window-db-{name}-{}-{id}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn init_db(path: &Path) -> Connection {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            "CREATE TABLE projects(id INTEGER PRIMARY KEY, slug TEXT, name TEXT);
             CREATE TABLE agents(id INTEGER PRIMARY KEY, name TEXT);
             CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
             CREATE TABLE functions(id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT, n_insns INTEGER,
                                    status TEXT, matched_agent_id INTEGER, matched_at REAL);
             CREATE TABLE attempts(id INTEGER PRIMARY KEY, function_id INTEGER, agent_id INTEGER,
                                   compiled INTEGER, score INTEGER, created_at REAL);
             CREATE TABLE events(id INTEGER PRIMARY KEY, project_id INTEGER, agent_id INTEGER,
                                 kind TEXT, detail TEXT, created_at REAL);",
        )
        .unwrap();
        conn
    }

    fn auth_headers(secret: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert(
            header::USER_AGENT,
            HeaderValue::from_str(&format!("Mozilla/5.0 decompdecomp-window/{secret} Safari")).unwrap(),
        );
        h
    }

    #[test]
    fn stats_matches_python_schema_and_auth() {
        let dir = temp_dir("stats");
        let db_path = dir.join("decomp.db");
        let conn = init_db(&db_path);
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64();
        conn.execute("INSERT INTO projects VALUES(1, 'game', 'Game')", []).unwrap();
        conn.execute("INSERT INTO agents VALUES(10, 'alice')", []).unwrap();
        conn.execute("INSERT INTO agents VALUES(11, 'bob')", []).unwrap();
        conn.execute(
            "INSERT INTO functions VALUES(100, 1, 'f1', 10, 'matched', 10, ?1)",
            [now - 100.0],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO functions VALUES(101, 1, 'f2', 10, 'open', NULL, NULL)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO attempts VALUES(1, 100, 10, 1, 200, ?1)",
            [now - 500.0],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO attempts VALUES(2, 100, 10, 1, 0, ?1)",
            [now - 100.0],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO events VALUES(1, 1, 11, 'readable', 'f1', ?1)",
            [now - 50.0],
        )
        .unwrap();
        drop(conn);

        let wdb = WindowDb::new(db_path, "secret123".into()).unwrap();
        let uri: Uri = "/api/stats?slug=game".parse().unwrap();

        // Wrong secret or Origin header falls back (None).
        assert!(wdb.answer(&Method::GET, &uri, &auth_headers("wrong")).is_none());
        let mut with_origin = auth_headers("secret123");
        with_origin.insert(header::ORIGIN, HeaderValue::from_static("http://127.0.0.1:6474"));
        assert!(wdb.answer(&Method::GET, &uri, &with_origin).is_none());
        assert!(wdb.answer(&Method::POST, &uri, &auth_headers("secret123")).is_none());

        let result = wdb.answer(&Method::GET, &uri, &auth_headers("secret123")).unwrap();
        assert_eq!(result["compiles_last_hour"], 2);
        assert_eq!(result["matches_per_hour"][23], 1);
        let agents = result["agents"].as_array().unwrap();
        assert_eq!(agents.len(), 2);
        assert_eq!(agents[0]["agent"], "alice");
        assert_eq!(agents[0]["compiles"], 2);
        assert_eq!(agents[0]["worked"], 1);
        assert_eq!(agents[0]["matched"], 1);
        assert_eq!(agents[0]["improved"], 1);
        assert_eq!(agents[0]["polished"], 0);
        assert_eq!(agents[1]["agent"], "bob");
        assert_eq!(agents[1]["polished"], 1);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn events_after_filters_kinds_and_progress_judge() {
        let dir = temp_dir("events");
        let db_path = dir.join("decomp.db");
        let conn = init_db(&db_path);
        conn.execute("INSERT INTO projects VALUES(1, 'game', 'Game')", []).unwrap();
        conn.execute("INSERT INTO agents VALUES(10, 'alice')", []).unwrap();
        conn.execute("INSERT INTO agents VALUES(11, 'progress-judge.1')", []).unwrap();
        conn.execute("INSERT INTO events VALUES(1, 1, 10, 'claim', 'f1', 100.0)", []).unwrap();
        conn.execute("INSERT INTO events VALUES(2, 1, 10, 'match', 'f1', 101.0)", []).unwrap();
        conn.execute("INSERT INTO events VALUES(3, 1, 11, 'match', 'f2', 102.0)", []).unwrap();
        drop(conn);

        let wdb = WindowDb::new(db_path.clone(), "s".into()).unwrap();
        let headers = auth_headers("s");

        // No `after` returns empty events + latest id.
        let initial = wdb.answer(&Method::GET, &"/api/events".parse().unwrap(), &headers).unwrap();
        assert_eq!(initial, json!({"last": 3, "events": []}));

        // `after=0` returns only notable non-progress-judge events.
        let feed = wdb.answer(&Method::GET, &"/api/events?after=0".parse().unwrap(), &headers).unwrap();
        assert_eq!(feed["last"], 3);
        let events = feed["events"].as_array().unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0]["id"], 2);
        assert_eq!(events[0]["agent"], "alice");
        assert_eq!(events[0]["slug"], "game");

        // `recent=1` falls back to upstream.
        assert!(wdb.answer(&Method::GET, &"/api/events?recent=1".parse().unwrap(), &headers).is_none());

        // When `activity-index-ready` is set, falls back to upstream.
        let conn = Connection::open(&db_path).unwrap();
        conn.execute("INSERT INTO settings VALUES('activity-index-ready', '1')", []).unwrap();
        drop(conn);
        assert!(wdb.answer(&Method::GET, &"/api/events?after=0".parse().unwrap(), &headers).is_none());

        let _ = std::fs::remove_dir_all(&dir);
    }
}
