"""Inspect and explicitly push existing commits to origin without committing or touching working files."""
from pathlib import Path

from ..network import NetworkError
from . import repository_git, repository_sync

git = repository_git.git


def checkout(net, slug, folder=''):
    proj = net.project_store(slug).one('SELECT * FROM projects WHERE slug=?', slug)
    if proj is None:raise NetworkError('Open a project before checking its commits.')
    cfg = net.cfg(proj)
    linked = repository_sync.project_checkout(proj, cfg)
    choices = [value for value in (cfg.get('auto_commit_folder'), cfg.get('repository_sync_folder'), linked) if value]
    chosen = folder or (choices[0] if choices else '')
    if not chosen:raise NetworkError('Choose and link this project’s Git checkout before pushing.')
    chosen = Path(chosen).expanduser().resolve()
    if chosen not in {Path(value).expanduser().resolve() for value in choices}:
        raise NetworkError('Use this project’s linked checkout. Verify and sync a new destination before pushing it.')
    return repository_sync.checkout(chosen, clean=False)


def destination(root):
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', missing=True)
    if not branch:raise NetworkError('Switch to a named Git branch before pushing commits.')
    if 'origin' not in git(root, 'remote').splitlines():
        raise NetworkError('Add an origin remote to this checkout before pushing commits.')
    remote = git(root, 'config', '--get', f'branch.{branch}.remote', missing=True)
    ref = git(root, 'config', '--get', f'branch.{branch}.merge', missing=True) if remote == 'origin' else None
    ref = ref or 'refs/heads/' + branch
    if not ref.startswith('refs/heads/'):
        raise NetworkError('Configure origin with a branch destination before pushing.')
    git(root, 'check-ref-format', ref, error='Configure a valid origin branch before pushing.')
    return branch, ref


def describe(root, branch, ref):
    head = git(root, 'rev-parse', 'HEAD')
    tracking = 'refs/remotes/origin/' + ref.removeprefix('refs/heads/')
    known = git(root, 'rev-parse', '--verify', '--quiet', tracking, missing=True)
    revisions = [known+'..'+head] if known else [head, '--not', '--remotes=origin']
    ahead = int(git(root, 'rev-list', '--count', *revisions))
    behind = int(git(root, 'rev-list', '--count', head+'..'+known)) if known else 0
    raw = git(root, 'log', '--max-count=20', '--format=%H%x00%s%x00%b%x00', *revisions)
    parts = raw.split('\0')
    commits = [{'sha':parts[index].strip(), 'subject':parts[index+1].strip(), 'body':parts[index+2].strip()[:5000]}
               for index in range(0, len(parts)-2, 3)]
    return {'folder':str(root), 'branch':branch, 'destination':'origin/'+ref.removeprefix('refs/heads/'),
            'head':head, 'unpushed':ahead, 'behind':behind, 'new_branch':not bool(known), 'commits':commits,
            'can_push':behind == 0 and (ahead > 0 or not known),
            'basis':'Based on the last fetch from origin; Push checks origin again.'}


def status(net, slug, folder=''):
    root = checkout(net, slug, folder)
    return describe(root, *destination(root))


def run(net, slug, folder, log, *, validate=None):
    from .repository_push_queue import turn
    root = checkout(net, slug, folder)
    branch, ref = destination(root)
    url = git(root, 'remote', 'get-url', 'origin')
    def check():
        getattr(log, 'check_cancel', lambda: None)()
        if validate:validate(root, branch, ref, url)
    progress = getattr(log, 'progress', lambda value: None)
    with turn(root, slug, log, check) as log:
        if checkout(net, slug, folder) != root or destination(root) != (branch, ref) or git(root, 'remote', 'get-url', 'origin') != url:
            raise NetworkError('The checkout, branch or origin changed while this push was queued. Review the destination and push again.')
        head = git(root, 'rev-parse', 'HEAD')
        url = git(root, 'remote', 'get-url', 'origin')
        if validate:validate(root, branch, ref, url)
        check()
        progress({'phase':'Checking origin before pushing existing commits', 'queued':False})
        log('Checking origin before pushing existing commits…')
        git(root, 'fetch', '--no-tags', '--', 'origin',
            error='Could not fetch origin. Check the remote and your Git credentials, then retry.')
        target = git(root, 'ls-remote', '--heads', '--', 'origin', ref,
                     error='Could not check origin. Check its remote and your Git credentials, then retry.')
        if target:
            remote_head = target.split()[0]
            git(root, 'merge-base', '--is-ancestor', remote_head, head,
                error='Origin is ahead or has diverged. Pull or merge its changes in this checkout, then retry. No force push was attempted.')
        check()
        if destination(root) != (branch, ref) or git(root, 'remote', 'get-url', 'origin') != url:
            raise NetworkError('The branch or origin changed while checking it. Review the checkout before pushing again.')
        git(root, 'merge-base', '--is-ancestor', head, git(root, 'rev-parse', 'HEAD'),
            error='Branch history changed while checking origin. Review the checkout before pushing again.')
        progress({'phase':'Pushing existing commits to origin', 'queued':False})
        log('Pushing commit '+head[:12]+' to origin/'+ref.removeprefix('refs/heads/')+'…')
        if validate:validate(root, branch, ref, url)
        check()
        git(root, 'push', '--', 'origin', head+':'+ref,
            error='Push to origin failed. Your commits remain local. Check origin, permissions and Git credentials, then retry. No force push was attempted.')
        # Refresh the exact destination even with a custom fetch refspec. Never change an upstream.
        git(root, 'update-ref', 'refs/remotes/origin/'+ref.removeprefix('refs/heads/'), head)
        log('Pushed existing commits to origin. Uncommitted files were left untouched.')
        return {'pushed':True, 'commit':head, 'destination':'origin/'+ref.removeprefix('refs/heads/'),
                'status':describe(root, branch, ref)}
