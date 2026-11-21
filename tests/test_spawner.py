"""Tools started with posix_spawn on macOS behave as subprocess's own launch does (spawner.py)."""
import os
import subprocess
import sys

import pytest

from decompnet import spawner

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='posix only')


def test_a_tool_runs_in_its_folder_and_its_own_session(tmp_path):
    proc = spawner.Popen([sys.executable, '-c', 'import os; print(os.getcwd()); print(os.getpgid(0) == os.getpid())'],
                         cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0 and out.split() == [str(tmp_path.resolve()), 'True']


def test_a_tool_gets_none_of_the_apps_other_descriptors(tmp_path):
    read, write = os.pipe()
    os.set_inheritable(write, True)  # even one marked inheritable is closed, as close_fds does
    try:
        result = spawner.run([sys.executable, '-c', f'import os; os.fstat({write})'], capture_output=True)
        assert result.returncode != 0 and b'Bad file descriptor' in result.stderr
    finally:
        os.close(read)
        os.close(write)


def test_input_output_environment_and_restored_sigpipe():
    assert spawner.run(['cat'], input=b'abc', capture_output=True).stdout == b'abc'
    env = {'PATH': os.environ.get('PATH', ''), 'SPAWNED': 'yes'}
    assert spawner.run(['sh', '-c', 'echo $SPAWNED'], capture_output=True, env=env, text=True).stdout == 'yes\n'
    both = spawner.run(['sh', '-c', 'echo a; echo b >&2'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert both.stdout == b'a\nb\n'
    # The app ignores SIGPIPE; a tool must get it back, or `yes | head` would never end.
    assert spawner.run(['sh', '-c', 'yes | head -1'], capture_output=True, timeout=10).stdout == b'y\n'


def test_missing_programs_and_folders_raise_as_subprocess_does(tmp_path):
    with pytest.raises(FileNotFoundError) as missing:
        spawner.run([str(tmp_path / 'no-such-tool')])
    assert missing.value.filename == str(tmp_path / 'no-such-tool')
    with pytest.raises(FileNotFoundError):
        spawner.run(['no-such-tool-anywhere-on-path'])
    with pytest.raises(FileNotFoundError) as folder:
        spawner.run(['/bin/echo'], cwd=tmp_path / 'gone')
    assert folder.value.filename == str(tmp_path / 'gone')


def test_a_timeout_stops_the_tool():
    with pytest.raises(subprocess.TimeoutExpired):
        spawner.run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.5)


def test_the_setting_turns_the_fast_path_off(monkeypatch):
    monkeypatch.setenv('DECOMP_POSIX_SPAWN', '0')
    assert not spawner.available()
    assert spawner.run(['sh', '-c', 'echo fallback'], capture_output=True).stdout == b'fallback\n'


@pytest.mark.skipif(sys.platform != 'darwin', reason='posix_spawn is used on macOS only')
def test_macos_uses_posix_spawn_not_fork(monkeypatch):
    import subprocess as stdlib
    monkeypatch.setattr(stdlib, '_fork_exec', lambda *a, **k: pytest.fail('forked the app'), raising=False)
    monkeypatch.setattr(stdlib.Popen, '_execute_child',
                        lambda *a, **k: pytest.fail('used the standard launch'))
    assert spawner.available()
    assert spawner.run(['/bin/echo', 'spawned'], capture_output=True, cwd='/', start_new_session=True).stdout == b'spawned\n'


@pytest.mark.skipif(sys.platform != 'darwin', reason='posix_spawn is used on macOS only')
def test_installed_every_subprocess_call_spawns_without_forking(monkeypatch):
    """A git run for the repository panel used plain subprocess, forked the app and hung before exec for hours."""
    import subprocess as stdlib
    from functools import wraps

    original = spawner._standard_execute_child

    @wraps(original)
    def unexpected_fallback(*args, **kwargs):
        pytest.fail('forked the app')

    monkeypatch.setattr(stdlib.Popen, '_execute_child', stdlib.Popen._execute_child)
    with monkeypatch.context() as launch:
        launch.setattr(spawner, '_standard_execute_child', unexpected_fallback)
        assert spawner.install()
        assert stdlib.run(['git', '--version'], capture_output=True, timeout=30).stdout.startswith(b'git version')
        assert stdlib.check_output(['/bin/echo', 'spawned'], cwd='/') == b'spawned\n'
    spawner.uninstall()
    assert stdlib.Popen._execute_child is original


def test_install_is_a_no_op_with_the_setting_off(monkeypatch):
    monkeypatch.setenv('DECOMP_POSIX_SPAWN', '0')
    before = subprocess.Popen._execute_child
    assert not spawner.install()
    assert subprocess.Popen._execute_child is before


@pytest.mark.skipif(sys.platform != 'darwin', reason='the exec watch runs on macOS only')
def test_a_fork_stuck_before_exec_is_stopped_not_waited_on_forever(monkeypatch):
    """The fallback launch (here, a preexec function) that never reaches exec ends with -9 after the deadline."""
    import time
    monkeypatch.setattr(spawner, 'EXEC_DEADLINE', 1.0)
    started = time.monotonic()
    proc = spawner.Popen([sys.executable, '-c', 'pass'], preexec_fn=lambda: time.sleep(60))
    assert proc.wait(timeout=30) == -9
    assert time.monotonic() - started < 15


def test_a_normal_fork_launch_is_not_stopped(monkeypatch):
    monkeypatch.setattr(spawner, 'EXEC_DEADLINE', 1.0)
    proc = spawner.Popen([sys.executable, '-c', 'import time; time.sleep(2); print("ok")'],
                         stdout=subprocess.PIPE, preexec_fn=lambda: None)
    assert proc.communicate(timeout=30)[0] == b'ok\n' and proc.returncode == 0
