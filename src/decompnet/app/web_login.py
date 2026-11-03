"""Opt-in localhost browser login. Desktop window and project/agent credentials remain separate."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from urllib.parse import urlsplit

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

SESSION_SECONDS = 12 * 60 * 60
MAX_SESSIONS = 16
MAX_FAILURES = 8
LOGIN_HEADERS = {'Cache-Control':'no-store', 'Referrer-Policy':'same-origin', 'X-Frame-Options':'DENY',
                 'Content-Security-Policy':"default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"}


def remote_origin(value):
    """One explicitly trusted HTTPS Tailscale origin, never a path or wildcard."""
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        url = urlsplit(value)
        port = url.port
        if (url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.ts.net')
                or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment
                or port not in (None, 443, 8443, 10000)):
            raise ValueError
        return 'https://' + url.netloc.lower()
    except ValueError:
        raise ValueError('Enter an HTTPS Tailscale address, such as https://my-computer.my-tailnet.ts.net.') from None


class WebLogin:
    def __init__(self, port, remote_url=''):
        self.code = secrets.token_urlsafe(24)
        self.cookie = f'decomp_web_{port}'
        self.origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
        if remote_url:
            self.origins.add(remote_origin(remote_url))
        self.sessions = {}
        self.oauth_states = {}
        self.failures = []
        self.lock = threading.Lock()

    def same_origin(self, request):
        origin = request.headers.get('origin')
        return origin in self.origins and urlsplit(origin).netloc == request.headers.get('host')

    def authorized(self, request):
        token = request.cookies.get(self.cookie, '')
        with self.lock:
            now = time.monotonic()
            self.sessions = {key:end for key,end in self.sessions.items() if end > now}
            return bool(token and self.sessions.get(hashlib.sha256(token.encode()).hexdigest()))

    def sign_in(self, code):
        with self.lock:
            now = time.monotonic()
            self.failures = [t for t in self.failures if t > now - 60]
            if len(self.failures) >= MAX_FAILURES:
                return None, 429
            if not isinstance(code, str) or not secrets.compare_digest(code.encode(), self.code.encode()):
                self.failures.append(now)
                return None, 401
            return self._issue_session(now), 303

    def _issue_session(self, now):
        token = secrets.token_urlsafe(32)
        self.sessions = {key:end for key,end in self.sessions.items() if end > now}
        if len(self.sessions) >= MAX_SESSIONS:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + SESSION_SECONDS
        return token

    def issue_session(self):
        with self.lock:
            return self._issue_session(time.monotonic())

    def sign_out(self, request):
        token = request.cookies.get(self.cookie, '')
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)


def login_page(error='', github=False):
    # Only fixed application strings are supplied as error; never reflect provider/query input.
    form = ("<p>Continue with the GitHub account approved by the desktop app.</p>"
            "<a class='github-login' href='/login/github'>Sign in with GitHub</a>") if github else (
            "<p>Enter the sign-in code shown in the desktop app’s Settings, or printed by <code>decomp serve</code>.</p>"
            "<form action='/login' method='post'><label>Sign-in code<input name='code' type='password' "
            "autocomplete='off' required maxlength='128' autofocus></label><button type='submit'>Sign in</button></form>")
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Sign in · decompdecomp</title><link rel='stylesheet' href='/assets/server.css'>"
            "<body class='web-login-page'><main class='web-login'><h1>decomp<span>decomp</span></h1><h2>Sign in to your server</h2>"
            f"<p role='status'>{error}</p>{form}<p>Your session lasts up to 12 hours. Restarting the server requires signing in again.</p></main></body></html>")


def register(app):
    from . import web_github
    web_github.register(app)
    def details(request):
        from . import window
        login = app.web_login
        native = secrets.compare_digest(window.request_token(request), app.secret)
        return {'enabled': login is not None, 'url': f'http://127.0.0.1:{app.port}/',
                'code': login.code if login and native and not web_github.enabled(app) else None,
                'remote_url': app.settings.data.get('web_remote_url', ''),
                'auth_method': 'github' if web_github.enabled(app) else 'code'}

    @app.mcp.custom_route('/api/web-interface', methods=['GET', 'POST'])
    async def interface(request):
        if (bad := app.guard(request, write=request.method == 'POST')):
            return bad
        if request.method == 'POST':
            data = await app.body(request)
            if type(data.get('enabled')) is not bool:
                return app.fail(ValueError('Choose whether to enable the web interface.'))
            enabled = data['enabled']
            if 'remote_url' in data:
                from . import window
                if not secrets.compare_digest(window.request_token(request), app.secret):
                    return app.fail(ValueError('Set the remote address in the desktop app.'), 403)
                try:
                    remote = remote_origin(data['remote_url'])
                except ValueError as error:
                    return app.fail(error)
                previous = app.settings.data.get('web_remote_url', '')
                if previous:
                    app.allowed_hosts.discard(urlsplit(previous).netloc)
                if remote:
                    app.allowed_hosts.add(urlsplit(remote).netloc)
                app.settings.data['web_remote_url'] = remote
                # Changing the trusted destination revokes all old browser sessions.
                if app.web_login:
                    app.web_login = app.net.web_login = WebLogin(app.port, remote)
            app.settings.data['web_interface_enabled'] = enabled
            app.settings.save()
            if enabled and app.web_login is None:
                app.web_login = app.net.web_login = WebLogin(app.port, app.settings.data.get('web_remote_url', ''))
            elif not enabled:
                app.web_login = app.net.web_login = None
        return JSONResponse(details(request), headers={'Cache-Control':'no-store'})

    @app.mcp.custom_route('/login', methods=['GET', 'POST'])
    async def sign_in(request):
        login = app.web_login
        if login is None:
            return HTMLResponse('The web interface is disabled. Enable it in the desktop app’s Settings.', status_code=404, headers=LOGIN_HEADERS)
        if request.headers.get('host') not in app.allowed_hosts:
            return HTMLResponse('Bad host', status_code=403, headers=LOGIN_HEADERS)
        if request.method == 'GET':
            if login.authorized(request):
                return RedirectResponse('/', status_code=303, headers=LOGIN_HEADERS)
            return HTMLResponse(login_page(github=web_github.enabled(app)), headers=LOGIN_HEADERS)
        if web_github.enabled(app):
            return HTMLResponse(login_page('Use GitHub to sign in.', github=True), status_code=403, headers=LOGIN_HEADERS)
        if not login.same_origin(request):
            return HTMLResponse(login_page('Sign in from this server’s own page.'), status_code=403, headers=LOGIN_HEADERS)
        # Small bounded form, independent of the much larger ROM upload allowance.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024:
                return HTMLResponse(login_page('The sign-in request is too large.'), status_code=413, headers=LOGIN_HEADERS)
        from urllib.parse import parse_qs
        code = parse_qs(raw.decode('utf-8', errors='replace')).get('code', [''])[0]
        token, status = login.sign_in(code)
        if token is None:
            message = 'Too many attempts. Wait one minute and try again.' if status == 429 else 'That code was not accepted. Check the terminal and try again.'
            return HTMLResponse(login_page(message), status_code=status, headers=LOGIN_HEADERS)
        response = RedirectResponse('/', status_code=303, headers=LOGIN_HEADERS)
        response.set_cookie(login.cookie, token, max_age=SESSION_SECONDS, httponly=True, samesite='strict', path='/', secure=request.headers.get('host') not in {f'127.0.0.1:{app.port}', f'localhost:{app.port}'})
        return response

    @app.mcp.custom_route('/logout', methods=['POST'])
    async def sign_out(request):
        login = app.web_login
        if login is None:
            return RedirectResponse('/login', status_code=303, headers=LOGIN_HEADERS)
        if request.headers.get('host') not in app.allowed_hosts or not login.same_origin(request):
            return HTMLResponse('Sign out from this server’s own page.', status_code=403, headers=LOGIN_HEADERS)
        login.sign_out(request)
        response = RedirectResponse('/login', status_code=303, headers=LOGIN_HEADERS)
        response.delete_cookie(login.cookie, path='/', httponly=True, samesite='strict')
        return response
