"""Review freshly fetched origin commits before updating a linked checkout."""
import hashlib
import json

from ..network import NetworkError
from . import repository_operations, repository_pull_progress, repository_push
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
        commits = f'{behind} commit' + ('s' if behind != 1 else '')
        if review and behind:
            if git(root, 'status', '--porcelain', '--untracked-files=no'):
                raise NetworkError('Commit or stash tracked changes in the checkout before pulling.')
            if ahead:
                phase = f'Checking whether {commits} from {name} can merge'
                output.progress({'phase': phase, 'queued': False})
                output(phase + '…')
                preview = git(root, 'merge-tree', '--write-tree', '--name-only', '-z', head, target,
                              merge_preview=True,
                              error='Git could not check the merge. Use Git 2.38 or newer and check that both branches share a history.')
                phase = 'Comparing saved matches and best attempts'
                output.progress({'phase': phase, 'queued': False})
                try:
                    prepared = repository_pull_progress.prepare(net, slug, root, head, target, preview, check)
                except repository_pull_progress.NeedsReview:
                    raise NetworkError('Some changes need manual review. Open this checkout in your Git client, '
                                       f'merge {name}, and resolve the conflicts there. The checkout is unchanged.') from None
                check()
                if (destination() != selected or git(root, 'rev-parse', 'HEAD') != head
                        or git(root, 'status', '--porcelain', '--untracked-files=no')):
                    raise NetworkError('The branch, origin or tracked files changed during the merge check. Review the pull again.')
                output.progress({'phase': f'Merging {commits} from {name}', 'queued': False})
                check()
                merge_error = 'Git could not finish the merge. Check Git status in the checkout before retrying.'
                if prepared:
                    commit, resolved = prepared
                    git(root, 'merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', commit, error=merge_error)
                else:
                    git(root, 'merge', '--no-ff', '--no-edit', '--no-autostash', '--no-overwrite-ignore',
                        '-s', 'ort', '-m', f'Merge {name}', target, error=merge_error)
                message = f'Merged {commits} from {name}. Both histories are saved in a local merge commit.'
                if prepared and resolved:
                    message += f' Chose the best saved progress for {resolved} function' + ('.' if resolved == 1 else 's.')
            else:
                output.progress({'phase': f'Applying {commits} from {name}', 'queued': False})
                output(f'Applying {commits} from {name}…')
                check()
                git(root, 'merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', target,
                    error='Git could not fast-forward the checkout. Check Git status for conflicting files or an in-progress operation, then retry.')
                message = f'Pulled {commits} from {name}.'
        elif behind and ahead:
            local = f'{ahead} local-only commit' + ('s' if ahead != 1 else '')
            message = f'{name} has {commits} to pull and this branch has {local}. Merging keeps both histories and uses saved match and score records to resolve compatible progress changes.'
        else:
            message = f'{commits} available from {name}.' if behind else f'No incoming commits from {name}.'
        output(message)
        return {'message': message, 'status': repository_push.describe(root, branch, ref),
                'pull_review': {'token': token, 'count': behind, 'merge': bool(ahead)} if behind and not review else None}
