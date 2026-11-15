"""Explicitly connect a writable GitHub fork without pushing local work."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from ..network import NetworkError
from . import repository_github, repository_operations, repository_push
from .repository_git import git


def github_name(url):
    """Only github.com origins; never return embedded credentials to the UI."""
    if url.startswith('git@github.com:'):
        path = url[len('git@github.com:'):]
    else:
        try:parsed = urlsplit(url)
        except ValueError:return None
        if parsed.scheme not in ('https', 'ssh') or parsed.hostname != 'github.com' or parsed.query or parsed.fragment:return None
        path = parsed.path.lstrip('/')
    path = path.removesuffix('.git').rstrip('/')
    return path if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}', path) else None


def origin(root):
    url = git(root, 'remote', 'get-url', 'origin')
    source = github_name(url)
    if not source:raise NetworkError('Fork setup supports a github.com origin. Review this checkout’s origin URL first.')
    if git(root, 'config', '--get-all', 'remote.origin.pushurl', missing=True):
        raise NetworkError('Origin has a separate push URL. Review and remove that override in Git before setting up a fork.')
    if len(git(root, 'remote', 'get-url', '--all', 'origin').splitlines()) != 1:
        raise NetworkError('Origin has multiple URLs. Choose one destination in Git before setting up a fork.')
    return url, source, hashlib.sha256(url.encode()).hexdigest()


def request(run, endpoint, *args, optional=False):
    result = run('api', '--hostname', 'github.com', endpoint, *args)
    if result.returncode:
        if optional:return None
        raise NetworkError('GitHub could not complete fork setup. Check your GitHub CLI login and permission to fork into that owner, then retry.')
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):raise ValueError()
        return value
    except (TypeError, ValueError):
        raise NetworkError('GitHub returned an unexpected response. Check your repositories on github.com before retrying.') from None


def info(net, slug, folder=''):
    root = repository_push.checkout(net, slug, folder)
    _, source, revision = origin(root)
    choices = repository_github.owners(root)
    return {**choices, 'source':source, 'origin_revision':revision}


def run(net, slug, folder, owner, revision, log):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner):
        raise NetworkError('Choose the GitHub account or organization that will own the fork.')
    root = repository_push.checkout(net, slug, folder)
    original, source, current = origin(root)
    if current != revision:raise NetworkError('Origin changed. Open fork setup again and review the repository.')
    def check():
        getattr(log, 'check_cancel', lambda:None)()
        if repository_push.checkout(net, slug, folder) != root or origin(root)[2] != revision:
            raise NetworkError('The checkout or origin changed. Open fork setup again before continuing.')
    # Reuse the same fair, cancellable repository turn as an explicit push.
    with repository_operations.push(root, slug, log, check, label='Setting up GitHub fork') as output:
        log = output
        check()
        log('Checking GitHub account and source repository…')
        cli = repository_github.runner(root)
        account = request(cli, 'user').get('login')
        source_info = request(cli, 'repos/'+source)
        network = (source_info.get('source') or source_info).get('id')
        if not network:raise NetworkError('GitHub did not identify the source repository. Review origin before retrying.')
        check()
        log('Creating or reusing '+owner+'/'+source.split('/')[1]+'…')
        args = ['--method', 'POST']
        if str(account).lower() != owner.lower():args += ['-f', 'organization='+owner]
        fork = request(cli, 'repos/'+owner+'/'+source.split('/')[1], optional=True)
        if fork is None:fork = request(cli, 'repos/'+source+'/forks', *args)
        full_name = fork.get('full_name', '')
        if not isinstance(full_name, str) or github_name('https://github.com/'+full_name) != full_name or full_name.split('/')[0].lower() != owner.lower():
            raise NetworkError('GitHub did not return a fork under the selected owner. Origin was not changed.')
        log('Checking fork '+full_name+' before connecting it…')
        target = None
        for attempt in range(10):
            check()
            target = request(cli, 'repos/'+full_name, optional=True)
            if target is not None:break
            log('Waiting for GitHub to make the new fork available…')
            time.sleep(1)
        if target is None:raise NetworkError('GitHub is still preparing the fork. Origin was not changed; retry setup shortly to connect the existing fork.')
        target_network = (target.get('source') or target.get('parent') or {}).get('id')
        if not target.get('fork') or target_network != network or not (target.get('permissions') or {}).get('push'):
            raise NetworkError('Could not verify a writable fork of this repository. The fork may exist on GitHub; origin was not changed. Check its permissions and retry.')
        check()
        remotes = git(root, 'remote').splitlines()
        upstream, index = 'upstream', 2
        while upstream in remotes:
            old = git(root, 'remote', 'get-url', upstream)
            if (github_name(old) or '').lower() == source.lower():break
            upstream, index = 'upstream-'+str(index), index+1
        if upstream not in remotes:
            git(root, 'remote', 'add', '--', upstream, original)
        # Keep the existing transport and credentials mechanism; do not rewrite
        # global authentication or push a single local commit during setup.
        new_url = ('https://github.com/'+full_name+'.git' if original.startswith('https://') else 'git@github.com:'+full_name+'.git')
        git(root, 'remote', 'set-url', 'origin', new_url,
            error='The fork exists, but origin could not be updated. The original remote is preserved; review Git remotes before retrying.')
        log('Origin now points to '+full_name+'. Original repository preserved as '+upstream+'.')
        warning = ''
        try:
            git(root, 'fetch', '--prune', '--no-tags', '--', 'origin',
                error='Fork connected, but fetching it failed. Check Git credentials before pushing (gh auth setup-git --hostname github.com can configure HTTPS authentication).')
        except NetworkError as error:
            warning = str(error)
            log(warning)
        log('Fork setup complete. No commits were pushed. Use Push commits to origin when ready.')
        return {'status':repository_push.status(net, slug, folder), 'fork':full_name, 'upstream':upstream, 'warning':warning}
