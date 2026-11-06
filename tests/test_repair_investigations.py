"""Repository handoffs are private, separate from game helpers, and never bypass CLI approval."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import agents
from decompnet.app import repair_investigations as investigations
from decompnet.network import NetworkError


def test_choices_only_locate_programs_without_running_them(monkeypatch):
    looked = []
    monkeypatch.setattr(investigations, '_which', lambda name: looked.append(name) or '/agent')
    result = investigations.choices()
    assert {r['id'] for r in result} == {'codex', 'claude', 'cursor-cli'}
    assert all(r['installed'] for r in result)
    assert set(looked) == {'codex', 'claude', agents.get('cursor-cli').command}


@pytest.mark.parametrize('kind', ['codex', 'claude', 'cursor-cli'])
def test_launch_is_private_and_keeps_evidence_outside_checkout(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(investigations, '_which', lambda name: '/bin/coding-agent')
    monkeypatch.setattr(investigations.shutil, 'which', lambda name: '/bin/git')
    monkeypatch.setattr(investigations.agents, 'cli_argv', lambda argv: argv)
    monkeypatch.setattr(investigations.limits, 'capped', lambda argv: argv)
    monkeypatch.setattr(investigations, 'terminal_command', lambda folder, driver: ['terminal', str(driver)])
    calls, opened = [], []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(investigations.subprocess, 'run', run)
    monkeypatch.setattr(investigations.subprocess, 'Popen', lambda argv, **kw: opened.append(argv))
    evidence = {'evidence': {'after': {'source': 'private game code'}}}
    result = investigations.launch(tmp_path, evidence, kind, lambda text: None)
    folder, repo = Path(result['folder']), Path(result['repository'])
    assert folder.parent == tmp_path / 'repair-investigations'
    assert json.loads((folder / 'repair-example.json').read_text()) == evidence
    assert not (repo / 'repair-example.json').exists()
    data = json.loads((folder / 'launch.json').read_text())
    assert data['cwd'] == str(repo)
    assert str(folder / 'repair-example.json') in data['argv'][-1]
    assert 'do not push' in data['argv'][-1]
    assert not any('dangerously' in part or 'bypass' in part for part in data['argv'])
    assert not any('DECOMP_TOKEN' in part for part in data['argv'])
    assert calls[0][1:5] == ['clone', '--depth', '1', '--']
    assert 'RetroReversing/decompdecomp.git' in calls[0][-2]
    assert len(opened) == 1


def test_failed_clone_never_launches_an_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(investigations, '_which', lambda name: '/agent')
    monkeypatch.setattr(investigations.shutil, 'which', lambda name: '/git')
    monkeypatch.setattr(investigations, 'terminal_command', lambda *args: ['terminal'])
    monkeypatch.setattr(investigations.limits, 'capped', lambda argv: argv)
    monkeypatch.setattr(investigations.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stderr='offline'))
    monkeypatch.setattr(investigations.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must not launch'))
    with pytest.raises(NetworkError, match='Could not clone'):
        investigations.launch(tmp_path, {}, 'codex', lambda text: None)


@pytest.mark.parametrize('platform', ['darwin', 'win32', 'linux'])
def test_terminal_launch_uses_separate_argv_not_shell_interpolation(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(investigations.sys, 'platform', platform)
    monkeypatch.setattr(investigations.shutil, 'which', lambda name: '/usr/bin/xterm')
    driver = tmp_path / "a 'quoted' script.py"
    cmd = investigations.terminal_command(tmp_path, driver)
    assert isinstance(cmd, list)
    if platform == 'darwin':
        assert cmd[:3] == ['open', '-a', 'Terminal']
        assert (tmp_path / 'Investigate.command').is_file()
    else:
        assert str(driver) in cmd


def test_unsupported_or_missing_agents_fail_without_creating_checkout(tmp_path, monkeypatch):
    with pytest.raises(NetworkError, match='supports repository'):
        investigations.launch(tmp_path, {}, 'api', lambda _: None)
    monkeypatch.setattr(investigations, '_which', lambda name: None)
    with pytest.raises(NetworkError, match='Install and sign in'):
        investigations.launch(tmp_path, {}, 'codex', lambda _: None)
    assert not list(tmp_path.iterdir())
    assert agents.get('api').investigation_command('', '') is None


def test_no_linux_terminal_has_actionable_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(investigations.sys, 'platform', 'linux')
    monkeypatch.setattr(investigations.shutil, 'which', lambda name: None)
    with pytest.raises(NetworkError, match='Copy the analysis prompt'):
        investigations.terminal_command(tmp_path, tmp_path / 'launch.py')


def test_windows_shortcut_uses_console_python_for_interactive_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(investigations.sys, 'platform', 'win32')
    monkeypatch.setattr(investigations.sys, 'executable', str(tmp_path / 'pythonw.exe'))
    command = investigations.terminal_command(tmp_path, tmp_path / 'launch.py')
    assert Path(command[0]).name == 'python.exe'
