//! The app window's address: a reverse proxy in front of the app's own server, with a few read-only pages answered
//! by the reader process (src/decompnet/app/tool_reader.py) first.
//!
//! The app is one Python process, and its threads share one interpreter lock with the event loop that answers the
//! window. Busy background work (compiles, saves, sweeps) made the window's pages slow. The window now loads its
//! pages here instead (src/decompnet/app/window.py): every request goes on to the app unchanged, except the GETs on
//! the allow-list (`--window-reads`, from src/decompnet/app/window_reads.py), which the reader process answers with
//! the app's own handlers when it can. When it declines, isn't running, fails or takes longer than
//! `--window-reader-limit`, the app answers: a read changes nothing, so asking the app after is safe. Only GETs are
//! ever sent to the reader, and nothing is ever sent to the app twice.
//!
//! What the app checks stays the app's to check, and the reader checks it the same way:
//! - Host: requests must name this listener (127.0.0.1 or localhost, its port), as the app requires of its own
//!   (DNS rebinding); the gateway then names the app's address instead, so the app's check still passes.
//! - The window's per-launch secret in its user agent, cookies and every other header pass through unchanged.
//! - An `Origin` naming this listener is rewritten to the app's origin, the same page seen from the app.
//!
//! Bodies stream both ways (uploads of whole game discs, `/api/stream`'s server-sent events). The app has no
//! websockets. Every wait has a deadline: the reader's answer within `--window-reader-limit`, the app's response
//! head within `--upstream-limit`; a response body then streams for as long as the app sends it, and ends when
//! either side closes.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, HeaderValue, Method, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use http_body_util::BodyExt;
use hyper_util::client::legacy::connect::HttpConnector;
use hyper_util::client::legacy::Client;
use hyper_util::rt::TokioExecutor;
use serde_json::{json, Value};

/// The reader's header on a response it answered. Anything else (a decline, an error) goes to the app.
pub const ANSWERED: &str = "x-decomp-reader";

/// One allow-listed read: a path, and query parameters it must carry (`/api/projects?brief=1`).
#[derive(Clone, Debug, PartialEq)]
pub struct Read {
    path: String,
    query: Vec<(String, String)>,
}

impl Read {
    pub fn parse(entry: &str) -> Option<Read> {
        let (path, query) = entry.split_once('?').unwrap_or((entry, ""));
        if !path.starts_with('/') {
            return None;
        }
        Some(Read { path: path.to_string(), query: pairs(query) })
    }

    fn matches(&self, path: &str, query: &[(String, String)]) -> bool {
        self.path == path && self.query.iter().all(|wanted| query.contains(wanted))
    }
}

/// A query string's pairs, percent-decoded the way the app reads them.
fn pairs(query: &str) -> Vec<(String, String)> {
    query
        .split('&')
        .filter(|p| !p.is_empty())
        .map(|p| {
            let (k, v) = p.split_once('=').unwrap_or((p, ""));
            (decode(k), decode(v))
        })
        .collect()
}

fn decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => out.push(b' '),
            b'%' if i + 2 < bytes.len() => {
                match u8::from_str_radix(std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("zz"), 16) {
                    Ok(b) => {
                        out.push(b);
                        i += 2;
                    }
                    Err(_) => out.push(b'%'),
                }
            }
            b => out.push(b),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[derive(Clone, Debug)]
pub struct Config {
    /// The app's address, `http://127.0.0.1:<port>`.
    pub upstream: String,
    /// The reader process's address, `http://127.0.0.1:<port>`.
    pub reader: Option<String>,
    pub reads: Vec<Read>,
    pub reader_limit: Duration,
    /// How long the app may take to start its response.
    pub head_limit: Duration,
}

pub struct Window {
    config: Config,
    client: Client<HttpConnector, Body>,
    /// The hosts requests must name: this listener's address (set once it is bound).
    port: u16,
    upstream_port: u16,
    pub proxied: AtomicU64,
    pub read: AtomicU64,
    pub passed: AtomicU64,
}

/// Connection-level headers a proxy must not forward (RFC 9110 7.6.1).
const HOP: [&str; 9] = [
    "connection", "keep-alive", "proxy-connection", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade",
];

fn port_of(url: &str) -> Option<u16> {
    url.trim_end_matches('/').rsplit(':').next()?.parse().ok()
}

impl Window {
    pub fn new(config: Config, port: u16) -> Result<Window, String> {
        let upstream_port = port_of(&config.upstream).ok_or_else(|| format!("no port in {}", config.upstream))?;
        let mut connector = HttpConnector::new();
        connector.set_connect_timeout(Some(Duration::from_secs(2)));
        connector.set_nodelay(true);
        let client = Client::builder(TokioExecutor::new())
            .pool_idle_timeout(Duration::from_secs(30))
            .build(connector);
        Ok(Window {
            config,
            client,
            port,
            upstream_port,
            proxied: AtomicU64::new(0),
            read: AtomicU64::new(0),
            passed: AtomicU64::new(0),
        })
    }

    pub fn health(&self) -> Value {
        json!({
            "port": self.port,
            "reads": self.config.reads.iter().map(|r| r.path.clone()).collect::<Vec<_>>(),
            "proxied": self.proxied.load(Ordering::Relaxed),
            "read_by_reader": self.read.load(Ordering::Relaxed),
            "passed_to_app": self.passed.load(Ordering::Relaxed),
        })
    }

    /// The app's host for a request naming this listener as `host` ("127.0.0.1:<port>" or "localhost:<port>"), or
    /// None when it names anything else (a web page reaching us through DNS rebinding).
    fn app_host(&self, host: &str) -> Option<String> {
        let (name, port) = host.rsplit_once(':')?;
        if port.parse::<u16>().ok()? != self.port || !(name == "127.0.0.1" || name == "localhost") {
            return None;
        }
        Some(format!("{name}:{}", self.upstream_port))
    }

    /// The request's headers as the app should see them.
    fn forwarded(&self, headers: &HeaderMap, host: &str, app_host: &str) -> HeaderMap {
        let mut out = HeaderMap::with_capacity(headers.len());
        for (name, value) in headers {
            if name == header::HOST || HOP.contains(&name.as_str()) {
                continue;
            }
            out.append(name.clone(), value.clone());
        }
        if let Ok(value) = HeaderValue::from_str(app_host) {
            out.insert(header::HOST, value);
        }
        if headers.get(header::ORIGIN).and_then(|v| v.to_str().ok()) == Some(&format!("http://{host}")) {
            if let Ok(value) = HeaderValue::from_str(&format!("http://{app_host}")) {
                out.insert(header::ORIGIN, value);
            }
        }
        out
    }

    fn allowed(&self, method: &Method, uri: &Uri) -> bool {
        if method != Method::GET || self.config.reader.is_none() {
            return false;
        }
        let query = pairs(uri.query().unwrap_or(""));
        self.config.reads.iter().any(|r| r.matches(uri.path(), &query))
    }

    /// The reader's whole answer, or None: the app answers instead.
    async fn from_reader(&self, uri: &Uri, headers: &HeaderMap, hosts: (&str, &str)) -> Option<Response> {
        let base = self.config.reader.as_ref()?;
        let target: Uri = format!("{base}{}", uri.path_and_query().map(|p| p.as_str()).unwrap_or("/")).parse().ok()?;
        let mut request = axum::http::Request::builder().method(Method::GET).uri(target).body(Body::empty()).ok()?;
        *request.headers_mut() = headers.clone();
        let work = async {
            let response = self.client.request(request).await.ok()?;
            if response.status() != StatusCode::OK || response.headers().get(ANSWERED).is_none() {
                return None;
            }
            let (parts, body) = response.into_parts();
            let bytes = body.collect().await.ok()?.to_bytes();
            Some((parts, bytes))
        };
        let (parts, bytes) = tokio::time::timeout(self.config.reader_limit, work).await.ok()??;
        let mut response = Response::new(Body::from(bytes));
        *response.status_mut() = parts.status;
        *response.headers_mut() = response_headers(&parts.headers, hosts);
        response.headers_mut().remove(ANSWERED);
        response.headers_mut().remove(header::CONTENT_LENGTH); // (set again for the body sent)
        Some(response)
    }

    async fn to_app(&self, method: Method, uri: &Uri, headers: HeaderMap, body: Body, hosts: (&str, &str)) -> Response {
        let target = format!("{}{}", self.config.upstream, uri.path_and_query().map(|p| p.as_str()).unwrap_or("/"));
        let Ok(target) = target.parse::<Uri>() else {
            return error(StatusCode::BAD_REQUEST, "not a request this server answers");
        };
        let Ok(mut request) = axum::http::Request::builder().method(method).uri(target).body(body) else {
            return error(StatusCode::BAD_REQUEST, "not a request this server answers");
        };
        *request.headers_mut() = headers;
        match tokio::time::timeout(self.config.head_limit, self.client.request(request)).await {
            Ok(Ok(response)) => {
                let (parts, body) = response.into_parts();
                let mut out = Response::new(Body::new(body));
                *out.status_mut() = parts.status;
                *out.headers_mut() = response_headers(&parts.headers, hosts);
                out
            }
            Ok(Err(why)) => error(StatusCode::BAD_GATEWAY, &format!(
                "decompdecomp isn't answering ({why}). It may be restarting: try again in a moment.")),
            Err(_) => error(StatusCode::GATEWAY_TIMEOUT, "decompdecomp took too long to answer. Try again."),
        }
    }
}

/// The app's response headers for the window: without connection-level ones, and a redirect to the app's own
/// address (Starlette's are absolute when it adds a slash) sent here instead, so the window stays on this origin.
/// `hosts`: (this listener's host as the window named it, the app's).
fn response_headers(headers: &HeaderMap, hosts: (&str, &str)) -> HeaderMap {
    let (here, app) = hosts;
    let mut out = HeaderMap::with_capacity(headers.len());
    for (name, value) in headers {
        if HOP.contains(&name.as_str()) {
            continue;
        }
        if name == header::LOCATION {
            if let Some(rest) = value.to_str().ok().and_then(|v| v.strip_prefix(&format!("http://{app}"))) {
                if rest.is_empty() || rest.starts_with('/') || rest.starts_with('?') {
                    if let Ok(moved) = HeaderValue::from_str(&format!("http://{here}{rest}")) {
                        out.append(name.clone(), moved);
                        continue;
                    }
                }
            }
        }
        out.append(name.clone(), value.clone());
    }
    out
}

fn error(status: StatusCode, message: &str) -> Response {
    let mut response = (status, json!({"error": message}).to_string()).into_response();
    response.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/json"));
    response
}

pub async fn handle(State(window): State<Arc<Window>>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let host = parts.headers.get(header::HOST).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    let Some(app_host) = window.app_host(&host) else {
        return error(StatusCode::FORBIDDEN, "bad host");
    };
    let headers = window.forwarded(&parts.headers, &host, &app_host);
    window.proxied.fetch_add(1, Ordering::Relaxed);
    if window.allowed(&parts.method, &parts.uri) {
        if let Some(answer) = window.from_reader(&parts.uri, &headers, (&host, &app_host)).await {
            window.read.fetch_add(1, Ordering::Relaxed);
            return answer;
        }
        window.passed.fetch_add(1, Ordering::Relaxed);
    }
    window.to_app(parts.method, &parts.uri, headers, body, (&host, &app_host)).await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn window(reads: &[&str]) -> Window {
        Window::new(
            Config {
                upstream: "http://127.0.0.1:6474".into(),
                reader: Some("http://127.0.0.1:7000".into()),
                reads: reads.iter().filter_map(|r| Read::parse(r)).collect(),
                reader_limit: Duration::from_secs(1),
                head_limit: Duration::from_secs(1),
            },
            9000,
        )
        .unwrap()
    }

    #[test]
    fn only_allow_listed_gets_go_to_the_reader() {
        let w = window(&["/api/stats", "/api/projects?brief=1"]);
        let uri = |u: &str| u.parse::<Uri>().unwrap();
        assert!(w.allowed(&Method::GET, &uri("/api/stats?slug=game")));
        assert!(!w.allowed(&Method::POST, &uri("/api/stats?slug=game")));
        assert!(!w.allowed(&Method::GET, &uri("/api/statsx?slug=game")));
        assert!(w.allowed(&Method::GET, &uri("/api/projects?brief=1")));
        assert!(w.allowed(&Method::GET, &uri("/api/projects?x=2&brief=1")));
        assert!(!w.allowed(&Method::GET, &uri("/api/projects?since=abc")));
        assert!(!w.allowed(&Method::GET, &uri("/api/projects?brief=10")));
    }

    #[test]
    fn checks_the_host_and_names_the_apps() {
        let w = window(&[]);
        assert_eq!(w.app_host("127.0.0.1:9000").as_deref(), Some("127.0.0.1:6474"));
        assert_eq!(w.app_host("localhost:9000").as_deref(), Some("localhost:6474"));
        assert_eq!(w.app_host("evil.example:9000"), None);
        assert_eq!(w.app_host("127.0.0.1:9001"), None);
        assert_eq!(w.app_host(""), None);
        let mut headers = HeaderMap::new();
        headers.insert(header::HOST, HeaderValue::from_static("127.0.0.1:9000"));
        headers.insert(header::ORIGIN, HeaderValue::from_static("http://127.0.0.1:9000"));
        headers.insert(header::USER_AGENT, HeaderValue::from_static("x decompdecomp-window/secret"));
        headers.insert(header::CONNECTION, HeaderValue::from_static("keep-alive"));
        let out = w.forwarded(&headers, "127.0.0.1:9000", "127.0.0.1:6474");
        assert_eq!(out.get(header::HOST).unwrap(), "127.0.0.1:6474");
        assert_eq!(out.get(header::ORIGIN).unwrap(), "http://127.0.0.1:6474");
        assert_eq!(out.get(header::USER_AGENT).unwrap(), "x decompdecomp-window/secret");
        assert!(out.get(header::CONNECTION).is_none());
        headers.insert(header::ORIGIN, HeaderValue::from_static("http://evil.example"));
        let out = w.forwarded(&headers, "127.0.0.1:9000", "127.0.0.1:6474");
        assert_eq!(out.get(header::ORIGIN).unwrap(), "http://evil.example");
    }

    #[test]
    fn redirects_to_the_app_stay_on_this_origin() {
        let mut headers = HeaderMap::new();
        headers.insert(header::LOCATION, HeaderValue::from_static("http://127.0.0.1:6474/api/x/?a=1"));
        headers.insert(header::TRANSFER_ENCODING, HeaderValue::from_static("chunked"));
        headers.insert(header::SET_COOKIE, HeaderValue::from_static("s=1; HttpOnly"));
        let out = response_headers(&headers, ("127.0.0.1:9000", "127.0.0.1:6474"));
        assert_eq!(out.get(header::LOCATION).unwrap(), "http://127.0.0.1:9000/api/x/?a=1");
        assert!(out.get(header::TRANSFER_ENCODING).is_none());
        assert_eq!(out.get(header::SET_COOKIE).unwrap(), "s=1; HttpOnly");
        for kept in ["/login", "http://127.0.0.1:64740/", "https://example.com/"] {
            headers.insert(header::LOCATION, HeaderValue::from_static(kept));
            let out = response_headers(&headers, ("127.0.0.1:9000", "127.0.0.1:6474"));
            assert_eq!(out.get(header::LOCATION).unwrap(), kept);
        }
    }

    #[test]
    fn decodes_queries_as_the_app_does() {
        assert_eq!(pairs("slug=a%20b&brief=1&x"), vec![
            ("slug".to_string(), "a b".to_string()),
            ("brief".to_string(), "1".to_string()),
            ("x".to_string(), String::new()),
        ]);
        assert_eq!(decode("a+b%2"), "a b%2");
        assert_eq!(Read::parse("nope"), None);
    }
}
