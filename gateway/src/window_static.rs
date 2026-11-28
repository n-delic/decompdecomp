//! Direct static web UI file serving for the app window listener.
//!
//! Serves static assets from `src/decompnet/app/static/` directly from Rust without touching Python.
//! Any auth mismatch, `Origin` header, path traversal attempt, missing file, or unsupported path
//! returns `None` so `window.rs` falls back to the upstream app unchanged.

use std::path::{Component, Path, PathBuf};

use axum::body::Body;
use axum::http::{header, HeaderMap, HeaderValue, Method, StatusCode, Uri};
use axum::response::Response;

const TOKEN_AGENT: &str = "decompdecomp-window/";

#[derive(Clone, Debug)]
pub struct WindowStatic {
    root: PathBuf,
    window_secret: String,
}

impl WindowStatic {
    pub fn new(root: PathBuf, window_secret: String) -> Option<WindowStatic> {
        if window_secret.is_empty() {
            return None;
        }
        let canonical = std::fs::canonicalize(&root).ok()?;
        if !canonical.is_dir() {
            return None;
        }
        Some(WindowStatic { root: canonical, window_secret })
    }

    pub fn from_config(window_static: Option<&str>, window_secret: Option<String>) -> Option<WindowStatic> {
        let secret = window_secret.filter(|s| !s.is_empty())?;
        let dir = window_static
            .map(String::from)
            .or_else(|| std::env::var("DECOMP_GATEWAY_STATIC_DIR").ok())
            .filter(|s| !s.is_empty())?;
        Self::new(PathBuf::from(dir), secret)
    }

    /// Match `window_db.rs`'s guard for direct window reads: no `Origin` header, and a `User-Agent`
    /// carrying the per-launch window secret (`decompdecomp-window/<secret>`).
    fn authorized(&self, headers: &HeaderMap) -> bool {
        if headers.contains_key(header::ORIGIN) {
            return false;
        }
        let ua = headers.get(header::USER_AGENT).and_then(|v| v.to_str().ok()).unwrap_or("");
        let token = token_of(ua);
        constant_time_eq(token.as_bytes(), self.window_secret.as_bytes())
    }

    /// Resolve a request URI path to a canonical file inside `self.root`, rejecting traversal or
    /// dynamic/API routes.
    fn resolve(&self, path: &str) -> Option<PathBuf> {
        if path == "/"
            || path == "/sw.js"
            || path == "/api"
            || path.starts_with("/api/")
            || path == "/health"
            || path == "/mcp"
            || path.starts_with("/mcp/")
        {
            return None;
        }
        let raw_rel = path
            .strip_prefix("/assets/")
            .or_else(|| path.strip_prefix('/'))?;
        if raw_rel.is_empty() || raw_rel.ends_with('/') {
            return None;
        }
        let decoded = percent_decode(raw_rel)?;
        if decoded.is_empty() || decoded.contains('\0') || decoded.contains('\\') || decoded.starts_with('/') {
            return None;
        }
        let rel_path = Path::new(&decoded);
        let mut clean = PathBuf::new();
        for component in rel_path.components() {
            match component {
                Component::Normal(part) => clean.push(part),
                _ => return None,
            }
        }
        if clean.as_os_str().is_empty() {
            return None;
        }
        let candidate = self.root.join(&clean);
        let canonical = std::fs::canonicalize(&candidate).ok()?;
        if !canonical.starts_with(&self.root) || !canonical.is_file() {
            return None;
        }
        Some(canonical)
    }

    /// Serve a static web UI file directly, or return `None` to fall back to the upstream app.
    pub fn answer(&self, method: &Method, uri: &Uri, headers: &HeaderMap) -> Option<Response> {
        if method != Method::GET || !self.authorized(headers) {
            return None;
        }
        let file = self.resolve(uri.path())?;
        let bytes = std::fs::read(&file).ok()?;
        let content_type = content_type_for(&file);
        let mut response = Response::new(Body::from(bytes));
        *response.status_mut() = StatusCode::OK;
        response.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
        response.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
        Some(response)
    }
}

fn content_type_for(path: &Path) -> &'static str {
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase();
    match ext.as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "json" => "application/json",
        "webmanifest" => "application/manifest+json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff2" => "font/woff2",
        "woff" => "font/woff",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "txt" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

fn percent_decode(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 2 >= bytes.len() {
                return None;
            }
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).ok()?;
            let byte = u8::from_str_radix(hex, 16).ok()?;
            out.push(byte);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

fn token_of(user_agent: &str) -> &str {
    match user_agent.find(TOKEN_AGENT) {
        Some(idx) => {
            let rest = &user_agent[idx + TOKEN_AGENT.len()..];
            rest.split_whitespace().next().unwrap_or("")
        }
        None => "",
    }
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
    use http_body_util::BodyExt;

    fn make_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "decomp-window-static-test-{}-{}-{name}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(dir.join("js")).unwrap();
        std::fs::write(dir.join("app.css"), b"body { color: #fff; }").unwrap();
        std::fs::write(dir.join("js/base.js"), b"console.log('ok');").unwrap();
        std::fs::write(dir.join("icon.svg"), b"<svg></svg>").unwrap();
        std::fs::write(dir.join("index.html"), b"<!doctype html>").unwrap();
        dir
    }

    fn auth_headers(secret: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert(
            header::USER_AGENT,
            HeaderValue::from_str(&format!("Mozilla/5.0 decompdecomp-window/{secret}")).unwrap(),
        );
        h
    }

    #[tokio::test]
    async fn serves_assets_and_direct_files_with_content_type() {
        let dir = make_dir("serve");
        let ws = WindowStatic::new(dir.clone(), "s3cr3t".into()).unwrap();
        let headers = auth_headers("s3cr3t");

        let resp = ws
            .answer(&Method::GET, &"/assets/app.css".parse().unwrap(), &headers)
            .expect("should serve /assets/app.css");
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(resp.headers().get(header::CONTENT_TYPE).unwrap(), "text/css; charset=utf-8");
        assert_eq!(resp.headers().get(header::CACHE_CONTROL).unwrap(), "no-cache");
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(&body[..], b"body { color: #fff; }");

        let js = ws
            .answer(&Method::GET, &"/assets/js/base.js".parse().unwrap(), &headers)
            .expect("should serve /assets/js/base.js");
        assert_eq!(js.headers().get(header::CONTENT_TYPE).unwrap(), "text/javascript; charset=utf-8");

        let svg = ws
            .answer(&Method::GET, &"/icon.svg".parse().unwrap(), &headers)
            .expect("should serve /icon.svg");
        assert_eq!(svg.headers().get(header::CONTENT_TYPE).unwrap(), "image/svg+xml");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_path_traversal_and_dynamic_routes() {
        let dir = make_dir("traversal");
        let ws = WindowStatic::new(dir.clone(), "s3cr3t".into()).unwrap();
        let headers = auth_headers("s3cr3t");

        for bad in [
            "/",
            "/sw.js",
            "/api/stats?slug=game",
            "/assets/../secret.txt",
            "/assets/%2e%2e/secret.txt",
            "/assets/js/../../secret.txt",
            "/assets/..\\secret.txt",
            "/assets/missing.js",
            "/assets/js/",
        ] {
            assert!(
                ws.answer(&Method::GET, &bad.parse().unwrap(), &headers).is_none(),
                "expected fallback for {bad}"
            );
        }

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn falls_back_on_auth_origin_or_method_mismatch() {
        let dir = make_dir("auth");
        let ws = WindowStatic::new(dir.clone(), "s3cr3t".into()).unwrap();
        let uri: Uri = "/assets/app.css".parse().unwrap();

        // Missing User-Agent
        assert!(ws.answer(&Method::GET, &uri, &HeaderMap::new()).is_none());
        // Wrong secret
        assert!(ws.answer(&Method::GET, &uri, &auth_headers("wrong")).is_none());
        // Origin header present -> falls back to upstream
        let mut with_origin = auth_headers("s3cr3t");
        with_origin.insert(header::ORIGIN, HeaderValue::from_static("http://127.0.0.1:9000"));
        assert!(ws.answer(&Method::GET, &uri, &with_origin).is_none());
        // Non-GET method
        assert!(ws.answer(&Method::POST, &uri, &auth_headers("s3cr3t")).is_none());

        let _ = std::fs::remove_dir_all(&dir);
    }
}
