"""Start and stop only the extracted portable app, in an isolated home on the Windows build runner."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main(folder):
    executable = Path(folder).resolve() / 'decompdecomp.exe'
    with tempfile.TemporaryDirectory(prefix='decomp-portable-smoke-') as temporary:
        home = Path(temporary)
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), DECOMP_HOME=str(home / 'data'),
                   DECOMP_WINDOW='0', DECOMP_TRAY='0')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([str(executable), 'serve', '--port', str(port)], cwd=home, env=env)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'The portable app exited before becoming ready: {process.returncode}')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(.25)
            else:
                raise RuntimeError('The portable app did not answer its health check within 90 seconds.')
            if not (home / 'data/.decomp/server.json').is_file():
                raise RuntimeError('The portable app did not use its explicitly isolated data home.')
            subprocess.run([str(executable), 'stop'], cwd=home, env=env, check=True, timeout=40)
            if process.wait(timeout=20) != 0:
                raise RuntimeError('The portable app did not exit cleanly after Stop.')
            print('Extracted portable EXE: isolated server startup, health and graceful Stop passed.')
        finally:
            if process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=15, check=False)
                process.wait(timeout=15)


if __name__ == '__main__':
    main(sys.argv[1])
