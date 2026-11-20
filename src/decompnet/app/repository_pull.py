"""Review freshly fetched origin commits before fast-forwarding a linked checkout."""
import hashlib
import json

from ..network import NetworkError
from . import repository_operations, repository_push
from .repository_git import git


def run(net, slug, folder, log, *, review=''):
    root = repository_push.checkout(net, slug, folder)

    def destination():
        return (*repository_push.destination(root), git(root, 'remote', 'get-url', 'origin'))

    branch, ref, url = selected = destination()
    check = getattr(log, 'check_cancel', lambda: None)
    with repository_operations.push(root, slug, log, check, label='Pulling from origin') as output:
        if repository_push.checkout(net, slug, folder) != root or destination() != selected:
            raise NetworkError('The checkout, branch or origin changed while queued. Review the pull again.')
        head = git(root, 'rev-parse', 'HEAD')
        tracking = 'refs/remotes/origin/' + ref.removeprefix('refs/heads/')
        output.progress({'phase': 'Fetching the selected branch from origin', 'queued': False})
        output('Fetching the selected branch from origin…')
        git(root, 'fetch', '--no-tags', '--refmap=', '--', 'origin', '+' + ref + ':' + tracking,
            error='Could not fetch this branch from origin. Check that the branch exists and your Git credentials work.')
        target = git(root, 'rev-parse', tracking)
        check()
        if destination() != selected or git(root, 'rev-parse', 'HEAD') != head:
            raise NetworkError('The branch or origin changed during fetch. Review the pull again.')
        ahead, behind = map(int, git(root, 'rev-list', '--left-right', '--count', head + '...' + target).split())
        name = 'origin/' + ref.removeprefix('refs/heads/')
        token = hashlib.sha256(json.dumps([str(root), branch, ref, url, head, target]).encode()).hexdigest()
        if review and review != token:
            raise NetworkError('Local or remote commits changed since your review. Check origin again to see the new count.')
        if behind and ahead:
            raise NetworkError(f'{name} has {behind} incoming commits and your branch has {ahead} local-only commits. '
                               'Merge or rebase in Git before pulling from the app.')
        commits = f'{behind} commit' + ('s' if behind != 1 else '')
        if review and behind:
            if git(root, 'status', '--porcelain', '--untracked-files=no'):
                raise NetworkError('Commit or stash tracked changes in the checkout before pulling.')
            output.progress({'phase': f'Applying {commits} from {name}', 'queued': False})
            output(f'Applying {commits} from {name}…')
            check()
            git(root, 'merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', target,
                error='Git could not fast-forward the checkout. Check Git status for conflicting files or an in-progress operation, then retry.')
            message = f'Pulled {commits} from {name}.'
        else:
            message = f'{commits} available from {name}.' if behind else f'No incoming commits from {name}.'
        output(message)
        return {'message': message, 'status': repository_push.describe(root, branch, ref),
                'pull_review': {'token': token, 'count': behind} if behind and not review else None}
