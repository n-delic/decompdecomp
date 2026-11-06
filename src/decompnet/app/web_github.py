"""GitHub identity for the full web UI: native-only setup, owner ID pinning, state and PKCE."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import window
from .web_login import LOGIN_HEADERS, SESSION_SECONDS, WebLogin, login_page

OAUTH_HEADERS = LOGIN_HEADERS | {'Referrer-Policy':'no-referrer'}

OAUTH_SECONDS = 600
MAX_PENDING = 32


def configuration(app):
    return app.settings.data.get('web_github') or {}


def enabled(app):
    config = configuration(app)
    return bool(app.web_login and config.get('enabled') and config.get('client_id')
                and config.get('client_secret') and config.get('user_id')
                and app.settings.data.get('web_remote_url'))


def callback_url(app):
    origin = app.settings.data.get('web_remote_url', '')
    return origin + '/login/github/callback' if origin else ''


async def github_user(login):
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        response = await client.get('https://api.github.com/users/' + login,
                                    headers={'Accept':'application/vnd.github+json','User-Agent':'decompdecomp'})
        response.raise_for_status()
        user = response.json()
        if user.get('type') != 'User' or type(user.get('id')) is not int or user['id'] <= 0:
            raise ValueError('Choose a personal GitHub account.')
        return user


async def identity(config, code, verifier, callback):
    # Tokens are used only for this identity check; never saved or sent to the browser.
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        response = await client.post('https://github.com/login/oauth/access_token',
            data={'client_id':config['client_id'],'client_secret':config['client_secret'],
                  'code':code,'code_verifier':verifier,'redirect_uri':callback},
            headers={'Accept':'application/json','User-Agent':'decompdecomp'})
        response.raise_for_status()
        token = response.json().get('access_token')
        if not isinstance(token,str) or not token or len(token)>4096:
            raise ValueError('GitHub did not accept this authorization. Try signing in again.')
        response = await client.get('https://api.github.com/user',
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'decompdecomp'})
        response.raise_for_status()
        return response.json()


def register(app):
    def native(request):
        return secrets.compare_digest(window.request_token(request), app.secret)

    def public(request):
        config = configuration(app)
        return {'enabled':enabled(app),'client_id':config.get('client_id',''),
                'secret_set':bool(config.get('client_secret')), 'login':config.get('login',''),
                'user_id':config.get('user_id'), 'callback_url':callback_url(app), 'can_configure':native(request)}

    def failure(message, status=400):
        return HTMLResponse(login_page(message, github=True), status_code=status, headers=OAUTH_HEADERS)

    @app.mcp.custom_route('/api/web-github', methods=['GET','POST'])
    async def settings(request):
        if (bad := app.guard(request, write=request.method == 'POST')):
            return bad
        if request.method == 'POST':
            if not native(request):
                return app.fail(ValueError('Configure GitHub sign-in in the native desktop app.'), 403)
            data = await app.body(request)
            if type(data.get('enabled')) is not bool:
                return app.fail(ValueError('Choose whether to enable GitHub sign-in.'))
            config = dict(configuration(app))
            if data['enabled']:
                if not app.web_login or not callback_url(app):
                    return app.fail(ValueError('Enable the web interface and save its private remote address first.'))
                client_id = str(data.get('client_id') or config.get('client_id') or '').strip()
                secret = str(data.get('client_secret') or config.get('client_secret') or '').strip()
                login = str(data.get('login') or config.get('login') or '').strip()
                if not re.fullmatch(r'[A-Za-z0-9_.-]{8,100}',client_id) or not secret or len(secret)>256 or any(c.isspace() for c in secret):
                    return app.fail(ValueError('Enter the OAuth App’s Client ID and Client secret.'))
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}',login):
                    return app.fail(ValueError('Enter the GitHub username allowed to sign in.'))
                try:
                    user = await github_user(login)
                except (httpx.HTTPError, ValueError, KeyError):
                    return app.fail(ValueError('Could not verify that GitHub account. Check its username and your connection.'))
                config.update(client_id=client_id,client_secret=secret,login=user['login'],user_id=user['id'])
            config['enabled'] = data['enabled']
            app.settings.data['web_github'] = config
            app.settings.save()
            # Revoke previous sessions and pending authorizations after an identity policy change.
            if app.web_login:
                app.web_login = app.net.web_login = WebLogin(app.port,app.settings.data.get('web_remote_url',''))
        return JSONResponse(public(request), headers={'Cache-Control':'no-store'})

    @app.mcp.custom_route('/login/github', methods=['GET'])
    async def start(request):
        if request.headers.get('host') not in app.allowed_hosts:
            return failure('Unrecognized server address.',403)
        if not enabled(app):
            return failure('GitHub sign-in is not configured.',404)
        callback = callback_url(app)
        origin = callback.removesuffix('/login/github/callback')
        # One registered callback and one browser-cookie host, including local desktop previews.
        if request.headers.get('host') != urlsplit(origin).netloc:
            return RedirectResponse(origin+'/login/github',status_code=303,headers=OAUTH_HEADERS)
        login,config = app.web_login,dict(configuration(app))
        state,binding,verifier = secrets.token_urlsafe(32),secrets.token_urlsafe(32),secrets.token_urlsafe(48)
        now = time.monotonic()
        with login.lock:
            login.oauth_states = {key:value for key,value in login.oauth_states.items() if value['expires']>now}
            if len(login.oauth_states)>=MAX_PENDING:
                return failure('Too many pending sign-ins. Wait a few minutes and try again.',429)
            login.oauth_states[state] = {'binding':hashlib.sha256(binding.encode()).hexdigest(),
                'verifier':verifier,'callback':callback,'expires':now+OAUTH_SECONDS,'config':config}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        query = urlencode({'client_id':config['client_id'],'redirect_uri':callback,'scope':'',
                           'state':state,'code_challenge':challenge,'code_challenge_method':'S256',
                           'allow_signup':'false','login':config['login']})
        response = RedirectResponse('https://github.com/login/oauth/authorize?'+query,status_code=303,headers=OAUTH_HEADERS)
        response.set_cookie(f'decomp_oauth_{app.port}',binding,max_age=OAUTH_SECONDS,httponly=True,
                            secure=True,samesite='lax',path='/login/github')
        return response

    @app.mcp.custom_route('/login/github/callback', methods=['GET'])
    async def callback(request):
        if not enabled(app) or request.headers.get('host') != urlsplit(callback_url(app)).netloc:
            return failure('GitHub sign-in is unavailable at this address.',403)
        login = app.web_login
        state = request.query_params.get('state','')
        binding = request.cookies.get(f'decomp_oauth_{app.port}','')
        with login.lock:
            pending = login.oauth_states.pop(state,None)
        if (not pending or pending['expires']<=time.monotonic() or not binding
                or not secrets.compare_digest(pending['binding'],hashlib.sha256(binding.encode()).hexdigest())
                or pending['config']!=configuration(app) or pending['callback']!=callback_url(app)):
            return failure('This sign-in expired or did not start in this browser. Start again.',403)
        code = request.query_params.get('code','')
        if request.query_params.get('error') or not code or len(code)>512:
            return failure('GitHub sign-in was cancelled or declined. Start again.')
        try:
            user = await identity(pending['config'],code,pending['verifier'],pending['callback'])
        except (httpx.HTTPError, ValueError, KeyError):
            return failure('Could not complete GitHub sign-in. Check your connection and OAuth App settings.')
        if app.web_login is not login or not enabled(app) or configuration(app)!=pending['config']:
            return failure('The login settings changed. Start again.',403)
        if not isinstance(user,dict) or type(user.get('id')) is not int or user['id']!=pending['config']['user_id']:
            return failure('This GitHub account is not permitted to access this app.',403)
        token = login.issue_session()
        response = RedirectResponse('/',status_code=303,headers=OAUTH_HEADERS)
        response.set_cookie(login.cookie,token,max_age=SESSION_SECONDS,httponly=True,secure=True,samesite='lax',path='/')
        response.delete_cookie(f'decomp_oauth_{app.port}',path='/login/github',secure=True,httponly=True,samesite='lax')
        return response
