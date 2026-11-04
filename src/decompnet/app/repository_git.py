"""Explicit user-requested commits of the files produced by verified repository sync."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from .. import hostenv
from ..network import NetworkError


def git(root, *args):
    """Never prompt invisibly for credentials in the app's background worker."""
    try:
        result = subprocess.run([shutil.which('git') or 'git', '-C', str(root), *args],
                                capture_output=True, timeout=120,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT':'0'},
                                creationflags=hostenv.NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise NetworkError('Git timed out or could not start. Check the checkout and your Git credentials, then retry.') from None
    if result.returncode:
        # Remotes and helper output may contain credentials. Keep diagnostics local.
        raise NetworkError('Git '+args[0]+' failed. Check the checkout, upstream and Git credentials in a terminal, then retry.')
    return result.stdout.decode().strip()


def prepare(root, log):
    """Require a named branch, identity, upstream and no remote commits missing locally."""
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    git(root, 'var', 'GIT_AUTHOR_IDENT')
    git(root, 'var', 'GIT_COMMITTER_IDENT')
    remote = git(root, 'config', '--get', f'branch.{branch}.remote')
    ref = git(root, 'config', '--get', f'branch.{branch}.merge')
    if remote.startswith('-') or remote == '.' or not re.fullmatch(r'refs/heads/[^\s:]+', ref):
        raise NetworkError('Configure this branch with a remote branch upstream before committing from the app.')
    log('Checking the current branch’s upstream…')
    git(root, 'fetch', '--', remote)
    # Resolve the upstream after fetch. Ahead-only is allowed so a failed push can be retried.
    git(root, 'merge-base', '--is-ancestor', '@{upstream}', 'HEAD')
    return remote, ref


def finish(root, files, upstream, log):
    """Stage only sync-owned paths; preserve a local commit when pushing fails."""
    from .repository_sync import digest, inside
    check = getattr(log, 'check_cancel', None)
    if check:check()
    paths = sorted(files)
    before = {p:digest(inside(root, p)) for p in paths}
    if git(root, 'diff', '--cached', '--name-only'):
        raise NetworkError('The Git index changed during sync. Review the checkout before committing.')
    log('Committing verified sources and all portable progress…')
    # Chunk pathspecs to stay below Windows and POSIX command-line limits.
    for offset in range(0, len(paths), 25):
        git(root, 'add', '-A', '--', *paths[offset:offset + 25])
    staged = set(filter(None, git(root, 'diff', '--cached', '--name-only', '-z').split('\0')))
    if staged - set(paths) or any(digest(inside(root,p)) != value for p,value in before.items()):
        raise NetworkError('The checkout changed while staging. Review the staged files before committing.')
    if staged:
        git(root, 'commit', '-m', 'Save verified matches and all decompilation progress')
    commit = git(root, 'rev-parse', 'HEAD')
    remote, ref = upstream
    result = {'commit':commit, 'upstream':remote+'/'+ref.removeprefix('refs/heads/'), 'pushed':False}
    url = git(root, 'remote', 'get-url', remote)
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?', url)
    if match:result['commit_url'] = 'https://github.com/'+match[1]+'/commit/'+commit
    log('Local progress saved in commit '+commit+'. Pushing…')
    try:
        git(root, 'push', '--', remote, commit+':'+ref)
        result['pushed'] = True
        log('Progress committed and pushed successfully.')
    except NetworkError:
        result['push_error'] = ('Commit '+commit+' is saved locally, but push failed. Check your Git credentials '
                                'and upstream in a terminal, then click Verify, commit and push again. No force push was attempted.')
        log(result['push_error'])
    return result
