"""Review freshly fetched origin commits before updating a linked checkout."""
import hashlib
import json
from pathlib import Path

from ..network import NetworkError
from . import (
    repository_operations,
    repository_progress,
    repository_pull_progress,
    repository_pull_rebuild,
    repository_push,
    repository_sync,
)
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
            message = _apply(net, slug, root, head, target, ahead, name, commits, destination, selected, output, check)
        elif behind and ahead:
            local = f'{ahead} local-only commit' + ('s' if ahead != 1 else '')
            message = f'{name} has {commits} to pull and this branch has {local}. Merging keeps both histories and uses saved match and score records to resolve compatible progress changes.'
        else:
            message = f'{commits} available from {name}.' if behind else f'No incoming commits from {name}.'
        local_progress = behind and not review and _has_local_progress(net, slug, root)
        if local_progress:
            message += ' Pull will save eligible local progress in a local commit and merge it with origin.'
        output(message)
        return {'message': message, 'status': repository_push.describe(root, branch, ref),
                'pull_review': {'token': token, 'count': behind, 'merge': bool(ahead or local_progress)} if behind and not review else None}


def _dirty(root):
    return git(root, 'status', '--porcelain', '--untracked-files=no')


def _has_local_progress(net, slug, root):
    return repository_progress.linked_checkout(net, slug, root) and bool(_dirty(root))


def _checkpoint(net, slug, root, head, target, branch, output):
    for state in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer'):
        path = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-path', state))
        if path.exists():
            raise NetworkError('Finish the Git operation already in progress before pulling from origin.')
    if git(root, 'diff', '--cached', '--name-only'):
        raise NetworkError('Commit or stash tracked changes already staged in the checkout before pulling.')
    output('Checking local saved progress before merging…')
    files, paths, _ = repository_progress.snapshot(net, slug, root, output, allow_unrelated_edits=True)
    # Unrelated untracked files can stay local. Git still checks for collisions when applying the merge.
    # A tracked deletion already made upstream can also be checkpointed, including old flat save files.
    changed = set(filter(None, git(root, 'diff', '--name-only', '--no-renames', '-z', head).split('\0')))
    paths = files.keys() | {path for path in paths - files.keys() if not repository_sync.inside(root, path).exists()}
    if changed - paths:
        upstream = set(filter(None, git(root, 'ls-tree', '-r', '--name-only', '-z', target).split('\0')))
        deleted = set(filter(None, git(root, 'diff', '--name-only', '--no-renames', '--diff-filter=D', '-z',
                                       head).split('\0')))
        paths |= deleted - upstream
    if changed - paths:
        repository_sync.checkout(root, blocking=changed - paths)
        raise NetworkError('Tracked files changed while checking local progress. Review the pull again.')
    commit = repository_progress.commit_snapshot(
        root, files, paths, output, expected_head=head, expected_branch=branch,
        message='Save local decompilation progress before pulling from origin')
    if commit != head:
        output('Saved local decompilation progress in a local commit.')
    return commit


def _apply(net, slug, root, head, target, ahead, name, commits, destination, selected, output, check):
    linked = repository_progress.linked_checkout(net, slug, root)
    if not linked:
        return _merge(net, slug, root, head, target, ahead, name, commits, destination, selected, output, check)
    lock = net.progress._project_lock(slug)
    if not lock.acquire(False):
        output.progress({'phase': 'Waiting for the project to finish saving progress'})
        output('Waiting for the project to finish saving progress…')
        while not lock.acquire(timeout=.2):
            check()
    try:
        check()
        return _merge(net, slug, root, head, target, ahead, name, commits, destination, selected, output, check,
                      linked=True)
    finally:
        lock.release()


def _merge(net, slug, root, head, target, ahead, name, commits, destination, selected, output, check, *, linked=False):
    if destination() != selected or git(root, 'rev-parse', 'HEAD') != head:
        raise NetworkError('The branch or origin changed while waiting. Review the pull again.')
    checkpoint = False
    if _dirty(root):
        if not linked:
            raise NetworkError('Commit or stash tracked changes in the checkout before pulling.')
        local = _checkpoint(net, slug, root, head, target, 'refs/heads/' + selected[0], output)
        checkpoint = local != head
        head = local
        if _dirty(root) or destination() != selected:
            raise NetworkError('The checkout changed while saving progress. Review the pull again.')
    if ahead or checkpoint:
        phase = f'Checking whether {commits} from {name} can merge'
        output.progress({'phase': phase, 'queued': False})
        output(phase + '…')
        try:
            prepared = repository_pull_rebuild.prepare(net, slug, root, head, target, output, check) if linked else None
            verified = prepared is not None
            if not verified:
                preview = git(
                    root, 'merge-tree', '--write-tree', '--name-only', '-z', head, target, merge_preview=True,
                    error='Git could not check the merge. Use Git 2.38 or newer and check that both branches share a history.')
                output.progress({'phase': 'Comparing saved matches and best attempts', 'queued': False})
                prepared = repository_pull_progress.prepare(net, slug, root, head, target, preview, check)
        except repository_pull_progress.NeedsReview as exc:
            saved = ('Your progress is saved in a local commit.' if checkpoint
                     else 'Your local branch still contains your saved work.')
            raise NetworkError(f'Some changes need manual review before merging {name}. {saved}\n{exc}') from None
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
        if verified:
            message += ' The rebuilt game matched the original byte for byte.'
        if prepared and resolved:
            message += f' Chose the best saved progress for {resolved} function' + ('.' if resolved == 1 else 's.')
    else:
        output.progress({'phase': f'Applying {commits} from {name}', 'queued': False})
        output(f'Applying {commits} from {name}…')
        check()
        git(root, 'merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', target,
            error='Git could not fast-forward the checkout. Check Git status for conflicting files or an in-progress operation, then retry.')
        message = f'Pulled {commits} from {name}.'
    return message
