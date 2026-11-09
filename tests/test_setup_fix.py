"""Fixing a failed setup opens the user's own coding CLI in the project folder, only on a click."""

import json

import pytest

from decompnet.app import setup_fix
from decompnet.network import NetworkError


def test_launch_opens_cli_in_project_folder_with_error(tmp_path, monkeypatch):
    project = tmp_path / 'proj'
    project.mkdir()
    opened = []
    monkeypatch.setattr(setup_fix, '_which', lambda name: '/bin/coding-agent')
    monkeypatch.setattr(setup_fix.agents, 'cli_argv', lambda argv: argv)
    monkeypatch.setattr(setup_fix, 'terminal_command', lambda folder, driver: ['terminal', str(driver)])
    monkeypatch.setattr(setup_fix.subprocess, 'Popen', lambda argv, **kw: opened.append(argv))
    result = setup_fix.launch(tmp_path, project, 'ninja: build stopped', 'claude', lambda text: None)
    launch = json.loads(next((tmp_path / 'setup-fixes').iterdir()).joinpath('launch.json').read_text())
    assert launch['cwd'] == str(project)
    assert 'ninja: build stopped' in launch['argv'][1]
    assert len(opened) == 1 and 'Check again' in result['message']


def test_launch_needs_installed_cli_and_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_fix, '_which', lambda name: None)
    with pytest.raises(NetworkError):
        setup_fix.launch(tmp_path, tmp_path, '', 'claude', lambda text: None)
    monkeypatch.setattr(setup_fix, '_which', lambda name: '/bin/x')
    with pytest.raises(NetworkError):
        setup_fix.launch(tmp_path, tmp_path / 'missing', '', 'claude', lambda text: None)


def test_choices_lists_clis_without_starting_them(monkeypatch):
    from decompnet.app import repair_investigations
    monkeypatch.setattr(repair_investigations, '_which', lambda name: '/bin/agent' if name == 'claude' else None)
    monkeypatch.setattr(setup_fix.subprocess, 'Popen', lambda *a, **k: pytest.fail('discovery launched a process'))
    choices = setup_fix.choices()
    assert any(item['id'] == 'claude' and item['installed'] for item in choices)
    assert any(not item['installed'] for item in choices)
