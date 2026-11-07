"""Explicitly create an empty GitHub origin using the user's own CLI login."""
import os
import re
import shutil
import subprocess
from pathlib import Path

from .. import hostenv
from ..network import NetworkError
from .repository_git import git


def create(root, name, visibility, log):
    if not re.fullmatch(r'(?:[A-Za-z0-9][A-Za-z0-9-]{0,38}/)?[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}', name) or name.split('/')[-1] in ('.', '..'):
        raise NetworkError('Enter a GitHub repository name, optionally preceded by owner/. Use letters, numbers, dots, underscores or hyphens.')
    if visibility not in ('private', 'public'):
        raise NetworkError('Choose Private or Public for the GitHub repository.')
    cli = shutil.which('gh')
    if not cli:
        cli = next((str(p) for p in (Path('/opt/homebrew/bin/gh'), Path('/usr/local/bin/gh'), Path.home()/'.local/bin/gh') if p.is_file()), None)
    if not cli:raise NetworkError('Install GitHub CLI from https://cli.github.com, then run gh auth login --hostname github.com in a terminal and retry.')
    def run(*args):
        try:
            return subprocess.run([cli, *args], cwd=root, capture_output=True, text=True, timeout=60,
                env={**os.environ, 'GH_HOST':'github.com', 'GH_PROMPT_DISABLED':'1', 'GIT_TERMINAL_PROMPT':'0'},
                creationflags=hostenv.NO_WINDOW if os.name == 'nt' else 0)
        except (OSError, subprocess.TimeoutExpired):
            raise NetworkError('GitHub did not finish the request. Check your repositories on github.com before retrying; if it was created, add its URL as origin.') from None
    log('Checking GitHub login…')
    if run('auth', 'status', '--hostname', 'github.com').returncode:
        raise NetworkError('Sign in with gh auth login --hostname github.com in a terminal, then retry.')
    check = getattr(log, 'check_cancel', None)
    if check:check()
    log(f'Creating {visibility} GitHub repository {name}…')
    result = run('repo', 'create', name, '--'+visibility)
    # Do not pass --source or --push: no local files are uploaded by creation.
    if result.returncode:
        raise NetworkError('GitHub could not create the repository. Check whether that name already exists and that your account can create repositories there. If it exists, add its URL as origin.')
    url = result.stdout.strip()
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', url):
        raise NetworkError('GitHub returned an unexpected repository address. Check your repositories on github.com and add the new URL as origin.')
    # Finish connecting even if Stop arrived after creation; never leave the user
    # guessing which remote was created. No subsequent upload is implicit.
    git(root, 'remote', 'add', '--', 'origin', url+'.git',
        error='Created '+url+', but origin could not be added. Add this URL as origin; do not create it again.')
    log('Connected '+url+'. Nothing was fetched or pushed.')
