"""A checkout whose code on disk moved on (git pull, uv sync) offers a restart, and restarts by itself only when asked."""
import subprocess
import threading

from decompnet.app import code_watch, updates
from decompnet.network import NetworkError


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _watch(facts, clock, enabled=True):
    return code_watch.Watch('.', read_commit=lambda root: facts['commit'], versions=lambda: facts['libraries'].items(),
                            enabled=enabled, clock=clock)


def test_nothing_changed_offers_nothing():
    facts = {'commit': 'a' * 40, 'libraries': {'decomprepair': '0.5.1'}}
    watch = _watch(facts, Clock())
    assert watch.check() == {'enabled': True, 'changed': False, 'what': [], 'settled': False}


def test_a_pull_and_a_library_upgrade_are_named():
    facts = {'commit': 'a' * 40, 'libraries': {'decomprepair': '0.5.1', 'decompassets': '0.1.0'}}
    watch = _watch(facts, Clock())
    facts['commit'] = 'b' * 40
    facts['libraries'] = {'decomprepair': '0.5.2', 'decompassets': '0.1.0'}
    status = watch.check()
    assert status['changed'] and status['what'] == ['the checkout moved from aaaaaaa to bbbbbbb', 'decomprepair 0.5.1 → 0.5.2']


def test_a_restart_waits_for_the_pull_and_its_uv_sync_to_finish():
    """git pull moves the commit first and uv sync the libraries later: restarting in between would run mixed code."""
    clock = Clock()
    facts = {'commit': 'a' * 40, 'libraries': {'decomprepair': '0.5.1'}}
    watch = _watch(facts, clock)
    facts['commit'] = 'b' * 40
    assert not watch.check()['settled']
    clock.now += code_watch.SETTLE_SECONDS - 1
    facts['libraries'] = {'decomprepair': '0.5.2'}  # the sync lands: the quiet period starts over
    assert not watch.check()['settled']
    clock.now += code_watch.SETTLE_SECONDS - 1
    assert not watch.check()['settled']
    clock.now += 2
    assert watch.check()['settled']


def test_going_back_to_the_running_code_withdraws_the_offer():
    facts = {'commit': 'a' * 40, 'libraries': {}}
    watch = _watch(facts, Clock())
    facts['commit'] = 'b' * 40
    assert watch.check()['changed']
    facts['commit'] = 'a' * 40
    assert not watch.check()['changed']


def test_an_installed_copy_is_not_watched():
    facts = {'commit': 'a' * 40, 'libraries': {}}
    watch = _watch(facts, Clock(), enabled=False)
    facts['commit'] = 'b' * 40
    assert watch.check() == {'enabled': False, 'changed': False, 'what': [], 'settled': False}


def test_the_commit_is_read_from_git(tmp_path):
    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
                       check=True, capture_output=True)
    git('init', '-q')
    (tmp_path / 'f').write_text('1')
    git('add', 'f')
    git('commit', '-qm', 'one')
    first = code_watch.commit(tmp_path)
    assert len(first) == 40
    (tmp_path / 'f').write_text('2')
    git('commit', '-qam', 'two')
    assert code_watch.commit(tmp_path) != first
    assert code_watch.commit(tmp_path / 'missing') == ''


def _updater(monkeypatch, before=lambda: None):
    monkeypatch.setattr(updates.time, 'sleep', lambda seconds: None)
    done = threading.Event()
    calls = []
    monkeypatch.setattr(updates.window, 'is_open', lambda: True)
    monkeypatch.setattr(updates, 'restart', lambda port, secret, window: (calls.append((port, secret, window)), done.set()))
    return updates.Updater(before, 6474, 'secret'), calls, done


def test_restart_now_prepares_then_replaces_the_process_with_the_same_port_and_window(monkeypatch):
    order = []
    up, calls, done = _updater(monkeypatch, before=lambda: order.append('prepared'))
    assert up.restart_now() is True
    assert done.wait(5)
    assert order == ['prepared'] and calls == [(6474, 'secret', True)]
    assert up.job['state'] == 'restarting'
    assert up.restart_now() is False  # a second click while it restarts does nothing


def test_a_restart_that_cannot_prepare_says_so_and_can_be_tried_again(monkeypatch):
    def refuse():
        raise NetworkError('decompdecomp is already shutting down.')
    up, calls, _ = _updater(monkeypatch, before=refuse)
    assert up.restart_now() is True
    for thread in threading.enumerate():
        if thread.name == 'restart':
            thread.join(5)
    assert up.job['state'] == 'error' and 'restarting failed' in up.job['error'] and 'update is installed' not in up.job['error']
    assert calls == []
    assert up.restart_now() is True
    for thread in threading.enumerate():
        if thread.name == 'restart':
            thread.join(5)
