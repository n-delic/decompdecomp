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
