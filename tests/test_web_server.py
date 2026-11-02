"""Headless browser login uses the same guarded API without relaxing desktop or agent auth."""
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from decompnet.app import cli, window
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build
from decompnet.app.web_login import MAX_FAILURES, WebLogin

PORT=6492
ORIGIN=f'http://127.0.0.1:{PORT}'


@pytest.fixture
def web(tmp_path):
    internal=tmp_path/'.decomp';internal.mkdir()
    net=RomNetwork(internal/'decomp.db',tmp_path)
    mcp,manager,security,_=build(net,internal,PORT,'native-secret',headless=True)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=ORIGIN)
    yield client,net,net.web_login
    manager.shutdown()
    net.deep_sweeps.shutdown()


def sign_in(client,login):
    return client.post('/login',data={'code':login.code},headers={'Origin':ORIGIN},follow_redirects=False)


def test_browser_login_cookie_api_logout_and_revocation(web):
    client,net,login=web
    assert client.get('/',follow_redirects=False).headers['location']=='/login'
    assert client.get('/api/state').status_code==401
    login_page=client.get('/login')
    assert login_page.headers['referrer-policy']=='same-origin'
    assert login.code not in login_page.text
    assert client.post('/login',data={'code':login.code}).status_code==403
    assert client.post('/login',data={'code':'wrong'},headers={'Origin':ORIGIN}).status_code==401
    answer=sign_in(client,login)
    assert answer.status_code==303
    cookie=answer.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Max-Age=43200' in cookie
    token=client.cookies.get(login.cookie)
    assert token!=login.code and token!='native-secret'
    page=client.get('/')
    assert page.status_code==200 and 'data-server-mode="1"' in page.text and 'Sign out' in page.text
    assert login.code not in page.text and 'native-secret' not in page.text
    assert client.get('/api/state').status_code==200
    assert client.post('/api/settings',json={'budget_usd':3}).status_code==403
    headers={'X-Decomp':'1','Origin':ORIGIN}
    assert client.post('/api/settings',json={'budget_usd':3},headers=headers).status_code==200
    assert client.post('/api/settings',json={},headers={**headers,'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/state',headers={'Host':'evil.example'}).status_code==403
    assert client.post('/logout',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/logout',headers={'Origin':ORIGIN},follow_redirects=False).status_code==303
    client.cookies.set(login.cookie,token)
    assert client.get('/api/state').status_code==401


def test_server_disables_native_pickers_and_expires_browser_sessions(web,monkeypatch):
    client,net,login=web
    sign_in(client,login)
    monkeypatch.setattr('decompnet.app.picker.choose',lambda *a:pytest.fail('server opened a desktop picker'))
    answer=client.post('/api/pick',json={'kind':'folder'},headers={'X-Decomp':'1','Origin':ORIGIN})
    assert answer.status_code==400 and 'server computer' in answer.json()['error']
    login.sessions={key:0 for key in login.sessions}
    assert client.get('/',follow_redirects=False).headers['location']=='/login'
    assert client.get('/api/state').status_code==401


def test_login_is_bounded_and_rate_limited(web):
    client,_,login=web
    assert client.post('/login',content='x'*2048,headers={'Origin':ORIGIN}).status_code==413
    for _ in range(MAX_FAILURES):
        assert client.post('/login',data={'code':'no'},headers={'Origin':ORIGIN}).status_code==401
    assert sign_in(client,login).status_code==429


def test_browser_credentials_are_separate_for_each_launch_and_port():
    login=WebLogin(PORT);token,_=login.sign_in(login.code)
    request=Request({'type':'http','headers':[(b'cookie',f'{login.cookie}={token}'.encode())]})
    assert login.authorized(request)
    assert not WebLogin(PORT).authorized(request)
    assert not WebLogin(PORT+1).authorized(request)


def test_serve_parses_port_and_preserves_legacy_cli(monkeypatch):
    calls=[]
    monkeypatch.setattr(cli,'run',lambda args:calls.append(args) or 0)
    assert cli.main(['serve','--port','6492'])==0
    assert calls[-1].cmd=='serve' and calls[-1].port==6492
    assert cli.main(['--no-browser','--port','6493'])==0
    assert calls[-1].cmd is None and calls[-1].port==6493


def test_serve_refuses_to_replace_an_existing_home_or_open_its_window(tmp_path,monkeypatch,capsys):
    for key,value in [('DECOMP_SERVE',''),('DECOMP_WINDOW','1'),('DECOMP_TRAY','1')]:monkeypatch.setenv(key,value)
    monkeypatch.setattr(cli,'home_dir',lambda:tmp_path)
    monkeypatch.setattr(cli,'use_own_wine_prefix',lambda:None)
    monkeypatch.setattr(cli,'_restart_state',lambda:None)
    monkeypatch.setattr(cli,'_state',lambda path:{'port':6464})
    monkeypatch.setattr(cli,'replace_older',lambda *a:pytest.fail('serve replaced running app'))
    monkeypatch.setattr(window,'missing',lambda:pytest.fail('serve checked desktop dependencies'))
    assert cli.run(SimpleNamespace(cmd='serve',port=PORT))==1
    assert 'already has a running app' in capsys.readouterr().err
    import os
    assert os.environ['DECOMP_WINDOW']=='0' and os.environ['DECOMP_TRAY']=='0'
    assert not (tmp_path/'.decomp/server.json').exists()


def test_desktop_mode_does_not_accept_browser_cookie_or_offer_login(tmp_path):
    internal=tmp_path/'.decomp';internal.mkdir()
    net=RomNetwork(internal/'decomp.db',tmp_path)
    mcp,manager,security,_=build(net,internal,PORT,'native-secret')
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=ORIGIN)
    assert client.get('/api/state',headers={'Cookie':f'decomp_web_{PORT}=native-secret'}).status_code==401
    assert client.get('/login').status_code==404
    assert client.get('/api/state',headers={'User-Agent':window.TOKEN_AGENT+'native-secret'}).status_code==200
    manager.shutdown();net.deep_sweeps.shutdown()


def test_serve_startup_never_calls_native_window_or_tray(tmp_path,monkeypatch,capsys):
    import uvicorn

    from decompnet.app import engine, server, tray
    for key,value in [('DECOMP_SERVE',''),('DECOMP_WINDOW','1'),('DECOMP_TRAY','1')]:monkeypatch.setenv(key,value)
    calls=[]
    monkeypatch.setattr(cli,'home_dir',lambda:tmp_path)
    monkeypatch.setattr(cli,'use_own_wine_prefix',lambda:None)
    monkeypatch.setattr(cli,'_restart_state',lambda:None)
    monkeypatch.setattr(cli,'_state',lambda path:None)
    monkeypatch.setattr(cli,'replace_older',lambda path:None)
    monkeypatch.setattr(window,'missing',lambda:pytest.fail('checked native dependencies'))
    monkeypatch.setattr(tray,'start',lambda *a:pytest.fail('started tray'))
    monkeypatch.setattr(cli,'_open_when_ready',lambda *a:pytest.fail('opened window'))
    socket=SimpleNamespace(getsockname=lambda:('127.0.0.1',PORT))
    monkeypatch.setattr(cli,'_listen',lambda port,exact:calls.append(('listen',port,exact)) or socket)
    net=SimpleNamespace(web_login=SimpleNamespace(code='test-code'))
    monkeypatch.setattr(engine,'RomNetwork',lambda *a:net)
    mcp=SimpleNamespace(streamable_http_app=lambda **kwargs:object())
    def build_server(*args,**kwargs):
        assert kwargs=={'headless':True}
        return mcp,object(),object(),lambda:calls.append('quit')
    monkeypatch.setattr(server,'build',build_server)
    monkeypatch.setattr(uvicorn,'Server',lambda cfg:SimpleNamespace(run=lambda sockets:calls.append(('run',sockets))))
    assert cli.run(SimpleNamespace(cmd='serve',port=PORT))==0
    assert calls==[('listen',PORT,True),('run',[socket]),'quit']
    import json
    state=json.loads((tmp_path/'.decomp/server.json').read_text())
    assert state['mode']=='serve'
    assert 'Web sign-in code: test-code' in capsys.readouterr().out



def test_desktop_toggle_enables_revokes_and_persists_browser_access(tmp_path):
    internal=tmp_path/'.decomp';internal.mkdir()
    net=RomNetwork(internal/'decomp.db',tmp_path)
    mcp,manager,security,_=build(net,internal,PORT,'native-secret')
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=ORIGIN)
    native={'User-Agent':window.TOKEN_AGENT+'native-secret','X-Decomp':'1'}
    try:
        assert client.post('/api/web-interface',json={'enabled':True}).status_code==401
        answer=client.post('/api/web-interface',json={'enabled':True},headers=native)
        assert answer.status_code==200 and answer.json()['enabled']
        login=net.web_login
        assert answer.json()['code']==login.code
        assert client.get('/',headers=native).status_code==200
        assert 'data-server-mode' not in client.get('/',headers=native).text
        assert sign_in(client,login).status_code==303
        assert client.get('/api/state').status_code==200
        assert client.get('/api/web-interface').json()['code'] is None
        assert client.post('/api/web-interface',json={'enabled':False},headers=native).status_code==200
        assert client.get('/api/state').status_code==401
        assert client.get('/login').status_code==404
        assert client.get('/api/state',headers=native).status_code==200
        client.post('/api/web-interface',json={'enabled':True},headers=native)
        assert net.web_login is not login
        assert client.get('/api/state').status_code==401
        import json
        assert json.loads((internal/'settings.json').read_text())['web_interface_enabled'] is True
    finally:
        manager.shutdown();net.deep_sweeps.shutdown()


def test_private_remote_origin_login_and_revocation(web):
    client,net,login=web
    native={'User-Agent':window.TOKEN_AGENT+'native-secret','X-Decomp':'1'}
    remote='https://computer.example.ts.net'
    answer=client.post('/api/web-interface',json={'enabled':True,'remote_url':remote},headers=native)
    assert answer.status_code==200 and answer.json()['remote_url']==remote
    headers={'Host':'computer.example.ts.net','Origin':remote}
    assert client.get('/login',headers={'Host':'evil.example'}).status_code==403
    response=client.post('/login',data={'code':net.web_login.code},headers=headers,follow_redirects=False)
    assert response.status_code==303 and 'Secure' in response.headers['set-cookie']
    token=response.cookies.get(net.web_login.cookie)
    remote_headers={**headers,'Cookie':f'{net.web_login.cookie}={token}'}
    assert client.get('/api/state',headers=remote_headers).status_code==200
    assert client.get('/api/web-interface',headers=remote_headers).json()['code'] is None
    assert client.post('/api/settings',json={},headers={**remote_headers,'X-Decomp':'1','Origin':ORIGIN}).status_code==403
    assert client.post('/api/web-interface',json={'enabled':True,'remote_url':remote},headers={**remote_headers,'X-Decomp':'1'}).status_code==403
    client.post('/api/web-interface',json={'enabled':False},headers=native)
    assert client.get('/api/state',headers=remote_headers).status_code==401


@pytest.mark.parametrize('url',['http://computer.example.ts.net','https://evil.example','https://computer.example.ts.net/path','https://user@computer.example.ts.net','https://computer.example.ts.net?code=x'])
def test_remote_origin_rejects_unsafe_addresses(url):
    from decompnet.app.web_login import remote_origin
    with pytest.raises(ValueError):
        remote_origin(url)
