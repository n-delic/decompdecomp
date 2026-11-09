"""Download an app source checkout on explicit request, without installing or launching it."""
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from decompnet.hostenv import NO_WINDOW


def shell_quote(value):
    return "'" + value.replace("'", "''") + "'" if os.name == 'nt' else shlex.quote(value)


class Checkout:
    def __init__(self):
        self.job = {'state': 'idle'}
        self.lock = threading.Lock()

    def status(self):
        from .updates import REPO, installed, repository
        cur = installed()
        repo = repository(cur.get('repo') or REPO)
        branch = cur.get('branch') if cur.get('channel') == 'branch' else 'main'
        return {'repo': repo, 'branch': branch or 'main',
                'destination': str(Path.home() / 'Documents' / 'decompdecomp-source'),
                'git_available': bool(shutil.which('git')), 'job': dict(self.job),
                'clone_command': 'git clone --depth 1 --single-branch --branch '+shell_quote(branch or 'main')+
                                 ' -- '+shell_quote(f'https://github.com/{repo}.git')+' NEW_FOLDER'}

    def start(self, destination):
        from .updates import BRANCH_NAME
        with self.lock:
            if self.job['state'] == 'cloning':
                raise ValueError('A source download is already running. Wait for it to finish.')
            info = self.status()
            branch = info['branch']
            if not BRANCH_NAME.fullmatch(branch) or '..' in branch or branch.startswith(('-', '/')):
                raise ValueError('Choose a valid update branch in Settings first.')
            git = shutil.which('git')
            if not git:
                raise ValueError('Git is not installed or could not be found. Install Git, restart the app, then try again.')
            if not destination.strip():
                raise ValueError('Enter a full path for the new checkout folder.')
            path = Path(destination.strip()).expanduser()
            if not path.is_absolute():
                raise ValueError('Use a full path, for example the suggested Documents folder.')
            if path.exists() or path.is_symlink():
                raise ValueError('That folder already exists. Choose a new folder so existing files stay safe.')
            if not path.parent.is_dir():
                raise ValueError('The parent folder does not exist. Create it or choose an existing parent folder.')
            try:
                path.mkdir()  # reserve only a new folder; concurrent requests cannot overwrite it
            except OSError as e:
                raise ValueError(f'Could not create the checkout folder: {e}') from None
            self.job = {'state': 'cloning', 'path': str(path), 'repo': info['repo'], 'branch': branch}

        def run():
            try:
                result = subprocess.run([git, 'clone', '--depth', '1', '--single-branch', '--branch', branch,
                                         '--', f'https://github.com/{info["repo"]}.git', str(path)],
                                        capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip()[-1500:] or 'Git could not download this repository.')
                command = ('Set-Location -LiteralPath ' if os.name == 'nt' else 'cd ') + shell_quote(str(path))
                self.job = {**self.job, 'state': 'done', 'commands': command+'\nuv sync --python 3.12\nuv run decomp'}
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                try:
                    path.rmdir()  # remove only our empty reservation; preserve any downloaded files
                except OSError:
                    pass
                self.job = {**self.job, 'state': 'error', 'error': f'{e}\nIf the checkout folder contains partial files, choose a new destination or remove it manually before retrying.'}

        threading.Thread(target=run, daemon=True, name='source-checkout').start()
        return self.status()
