"""Interactive login uses the original account, not an isolated helper credential copy."""
import shlex

from decompnet import hostenv
from decompnet.agents.kimi import KIMI


def test_kimi_login_uses_account_home(monkeypatch, tmp_path):
    home = tmp_path / 'account with spaces'
    monkeypatch.setenv('KIMI_CODE_HOME', str(home))
    calls = []
    monkeypatch.setattr(hostenv, 'login_terminal', lambda argv, env: calls.append((argv, env)))
    KIMI.login('/tools with spaces/kimi')
    assert calls == [(['/tools with spaces/kimi', 'login'], {'KIMI_CODE_HOME': str(home)})]


def test_mac_login_command_is_quoted_and_passed_as_data(monkeypatch):
    monkeypatch.setattr(hostenv.sys, 'platform', 'darwin')
    calls = []
    monkeypatch.setattr(hostenv.subprocess, 'run', lambda argv, **kw: calls.append((argv, kw)))
    argv = ['/tools/a\'b/$(touch nope)/kimi', 'login']
    env = {'KIMI_CODE_HOME': '/account with spaces'}
    hostenv.login_terminal(argv, env)
    command, kwargs = calls[0]
    assert command[:2] == ['osascript', '-e']
    assert shlex.split(command[-1]) == ['env', 'KIMI_CODE_HOME=/account with spaces', *argv]
    assert 'touch nope' not in command[2]  # executable path is never AppleScript code
    assert kwargs['timeout'] == 10
