"""Explicit user-requested commits of the files produced by verified repository sync."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from .. import hostenv
from ..network import NetworkError


def git(root, *args, env=None, input=None, missing=False, error=None):
    """Never prompt invisibly for credentials in the app's background worker."""
    try:
        result = subprocess.run([shutil.which('git') or 'git', '-C', str(root), *args],
                                capture_output=True, timeout=120, input=input,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT':'0', **(env or {})},
                                creationflags=hostenv.NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise NetworkError('Git timed out or could not start. Check the checkout and your Git credentials, then retry.') from None
    if missing and result.returncode == 1 and not result.stderr.strip():
        return None
    if result.returncode:
        # Remotes and helper output may contain credentials. Keep diagnostics local.
        raise NetworkError(error or 'Git '+args[0]+' failed. Check the checkout, upstream and Git credentials in a terminal, then retry.')
    return result.stdout.decode().strip()


def prepare(root, log):
    """Check the explicit upstream, or publish this branch to its sole remote."""
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', missing=True)
    if not branch:
        raise NetworkError('This checkout has a detached HEAD. Switch to the branch you want to save, then retry.')
    identity_error = ('Git author or committer identity is missing. Set user.name and user.email in this checkout, '
                      'then retry committing progress.')
    git(root, 'var', 'GIT_AUTHOR_IDENT', error=identity_error)
    git(root, 'var', 'GIT_COMMITTER_IDENT', error=identity_error)
    remote = git(root, 'config', '--get', f'branch.{branch}.remote', missing=True)
    ref = git(root, 'config', '--get', f'branch.{branch}.merge', missing=True)
    configured = remote is not None or ref is not None
    if not configured:
        remotes = git(root, 'remote').splitlines()
        if len(remotes) != 1:
            raise NetworkError('This branch has no upstream. '+
                ('Add a Git remote for your progress repository, then retry.' if not remotes else
                 'Choose the destination remote by setting this branch’s upstream in Git, then retry.'))
        remote, ref = remotes[0], 'refs/heads/'+branch
        log('No upstream is configured. Using the current branch on the checkout’s only remote.')
    if not remote or remote.startswith('-') or remote == '.' or not ref or not re.fullmatch(r'refs/heads/[^\s:]+', ref):
        raise NetworkError('Configure this branch with a remote branch upstream before committing from the app.')
    log('Checking the current branch’s upstream…')
    git(root, 'fetch', '--', remote,
        error='Could not fetch the progress repository. Check that its remote exists and your Git credentials work, then retry.')
    behind_error = ('The remote branch is ahead or has diverged. Merge or rebase its changes in this checkout, '
                    'then retry committing progress. No force push was attempted.')
    if configured:
        git(root, 'merge-base', '--is-ancestor', '@{upstream}', 'HEAD', error=behind_error)
    else:
        # Read the actual destination, not a possibly absent/custom remote-tracking ref.
        # A concurrent remote update is still protected by Git's ordinary non-force push.
        target = git(root, 'ls-remote', '--heads', '--', remote, ref,
                     error='Could not check the progress repository branch. Check the remote and Git credentials, then retry.')
        if target:
            commit = target.split()[0]
            git(root, 'merge-base', '--is-ancestor', commit, 'HEAD', error=behind_error)
        else:
            log('The remote branch does not exist yet. It will be created when progress is pushed.')
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
    return push(root, commit, upstream, log)


def push(root, commit, upstream, log):
    """A failed network operation leaves the checkpoint available for a later retry."""
    remote, ref = upstream
    result = {'commit':commit, 'upstream':remote+'/'+ref.removeprefix('refs/heads/'), 'pushed':False}
    url = git(root, 'remote', 'get-url', remote)
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?', url)
    if match:result['commit_url'] = 'https://github.com/'+match[1]+'/commit/'+commit
    log('Local progress saved in commit '+commit+'. Pushing…')
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', missing=True)
    try:
        git(root, 'push', '--', remote, commit+':'+ref)
        result['pushed'] = True
        log('Progress committed and pushed successfully.')
    except NetworkError:
        result['push_error'] = ('Commit '+commit+' is saved locally, but push failed. Check your Git credentials '
                                'and upstream in a terminal, then click Verify, commit and push again. No force push was attempted.')
        log(result['push_error'])
    if result['pushed'] and branch and ref == 'refs/heads/'+branch:
        try:
            # Do not change an upstream the user set while the push was in flight.
            if (git(root, 'config', '--get', f'branch.{branch}.remote', missing=True) is None
                    and git(root, 'config', '--get', f'branch.{branch}.merge', missing=True) is None):
                git(root, 'branch', '--set-upstream-to', remote+'/'+branch, '--', branch)
                log('Upstream saved for future progress pushes.')
        except NetworkError:
            log('Progress was pushed, but its upstream could not be saved. Configure the branch upstream in Git.')
    return result
