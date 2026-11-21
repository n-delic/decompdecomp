"""Merge committed portable progress records and their C files as one change."""
import json
import re
import tempfile
from pathlib import Path

from ..project import compiler_for
from . import repository_progress, repository_sync, save_readme, saves
from .repository_git import git

_ABSENT = object()


class NeedsReview(Exception):
    """A change requires a decision that the saved verdicts cannot make."""

    def __init__(self, reason='The saved progress records could not be combined safely.', *, paths=()):
        paths = sorted(paths, key=lambda path: (path.count('/'), path))
        details = [reason]
        if paths:
            details.append(f'Conflicting files ({len(paths)}):')
            details.extend('  ' + json.dumps(path) for path in paths[:8])
            if len(paths) > 8:
                details.append(f'  … and {len(paths) - 8} more')
        super().__init__('\n'.join(details))


class Incomparable(NeedsReview):
    """The tips contain competing versions whose verdicts cannot be compared."""


def _three_way(base, local, remote):
    if local == remote or remote == base:
        return local
    if local == base:
        return remote
    if all(isinstance(value, dict) for value in (base, local, remote)):
        return _merge_mapping(base, local, remote)
    raise NeedsReview


def _merge_mapping(base, local, remote):
    result = {}
    for key in dict.fromkeys((*local, *remote, *base)):
        value = _three_way(*(side.get(key, _ABSENT) for side in (base, local, remote)))
        if value is not _ABSENT:
            result[key] = value
    return result


def _entries(root, revision, prefix):
    paths = ('src', 'best', 'include', 'README.md', save_readme.CARD, *saves.TOP_FILES)
    raw = git(root, '--literal-pathspecs', 'ls-tree', '-rz', revision, '--', *(prefix + name for name in paths))
    entries = {}
    for item in filter(None, raw.split('\0')):
        meta, path = item.split('\t', 1)
        mode, _, oid = meta.split()
        entries[path] = (mode, oid)
    return entries


def _candidate(manifest, entries, prefix, name):
    info = manifest['functions'].get(name, _ABSENT)
    if info is _ABSENT:
        return info
    kind = 'src' if 'matched_by' in info else 'best'
    component = saves._source_file(name)
    if '/' in component or '\\' in component or any(ord(char) < 32 for char in component):
        raise NeedsReview
    path = prefix + kind + '/' + component
    entry = entries.get(path)
    if not entry or entry[0] != '100644':
        raise NeedsReview
    return info, path, entry


def _rank(info):
    if 'matched_by' in info:
        return (0, not info.get('readable', False))
    score = info.get('best_score')
    if type(score) is not int or score < 0:
        raise NeedsReview
    return (1, score)


def _profile(manifest, name, info):
    return compiler_for(manifest, info['vram'], {'name': name, 'vram': info['vram']})


def _winner(local, remote, manifests, layouts, name):
    if local is _ABSENT or remote is _ABSENT:
        raise Incomparable(f'One branch removed or renamed {name} while the other changed its saved code.')
    left, right = local[0], remote[0]
    if (left.get('vram') != right.get('vram') or any(layouts[0].get(name) != side.get(name) for side in layouts[1:])
            or name not in layouts[0] or layouts[0][name].get('status') == 'asm'):
        raise Incomparable(f'The saved function layout for {name} changed, so its verdicts cannot be compared.')
    if all('matched_by' not in info for info in (left, right)):
        if _profile(manifests[0], name, left) != _profile(manifests[1], name, right):
            raise Incomparable(f'The drafts for {name} were scored with different compiler settings.')
    try:
        return 1 if _rank(left) <= _rank(right) else 2
    except NeedsReview:
        raise Incomparable from None


def _merge_notes(base, local, remote):
    try:
        return _three_way(base, local, remote)
    except NeedsReview:
        # Concurrent append-only notes can be combined. Edits or removals need review.
        keys = [{json.dumps(note, sort_keys=True): note for note in side} for side in (base, local, remote)]
        if all(keys[0].keys() <= side.keys() for side in keys[1:]):
            return list((keys[1] | keys[2]).values())
        raise


def _merge_proof(manifests, winners):
    proofs = [side.get('verification', {}) for side in manifests]
    matches = [dict(proof.get('matches', {})) for proof in proofs]
    for name, winner in winners.items():
        chosen = matches[winner].get(name, _ABSENT)
        for index in (1, 2):
            if chosen is _ABSENT:
                matches[index].pop(name, None)
            else:
                matches[index][name] = chosen
    matches = _merge_mapping(*matches)
    if not matches and not any(proofs):
        return {}
    return {'matches': matches, **{kind: _merge_mapping(*(proof.get(kind, {}) for proof in proofs))
                                   for kind in ('sources', 'headers')}}


_PROGRESS_BLOCK = re.compile(r'<!-- decompdecomp:progress:start -->.*?<!-- decompdecomp:progress:end -->', re.S)


def _report(manifest):
    _, platform, _, states = saves._readme_key(manifest)
    best = sum('matched_by' not in info for info in manifest['functions'].values())
    return save_readme.progress_content(states, best, platform or '')


def _report_blobs(root, prefix, entries, conflicts, manifests, merged):
    """Replace only the generated README block and an SVG we can identify as generated."""
    readme, card = prefix + 'README.md', prefix + save_readme.CARD
    blobs = {}
    if not (conflicts & {readme, card}):
        return blobs
    progress, svg = _report(merged)
    for path in conflicts & {readme, card}:
        if any(side.get(path, (None,))[0] != '100644' for side in entries):
            continue
        texts = [repository_sync.git(root, 'cat-file', 'blob', side[path][1]).decode() for side in entries]
        if path == readme:
            # Discard only the generated counts before comparing the owner's text.
            if any(text.count('<!-- decompdecomp:progress:start -->') != 1
                   or text.count('<!-- decompdecomp:progress:end -->') != 1
                   or not _PROGRESS_BLOCK.search(text) for text in texts):
                continue
            blocks = [_PROGRESS_BLOCK.sub(save_readme.block('progress', ''), text) for text in texts]
            text = save_readme._replace(_three_way(*blocks), 'progress', progress)
        else:
            if any(text != _report(manifest)[1] for text, manifest in zip(texts, manifests)):
                continue
            text = svg
        blobs[path] = text.encode()
    return blobs


def _plan(root, revisions, prefix, conflicts, check):
    entries = [_entries(root, rev, prefix) for rev in revisions]
    manifest_path = prefix + saves.MANIFEST
    if any(side.get(manifest_path, (None,))[0] != '100644' for side in entries):
        raise NeedsReview
    manifests = [json.loads(repository_progress.committed_manifest_text(root, manifest_path, rev))
                 for rev in revisions]
    base, local, remote = manifests
    game = base.get('game', {}).get('sha1')
    if not game or any(side.get('game', {}).get('sha1') != game for side in manifests):
        raise NeedsReview
    layouts = [{entry['name']: entry for entry in side.get('layout', ())} for side in manifests]
    names = dict.fromkeys(name for side in manifests for name in side['functions'])
    functions, winners, updates = {}, {}, {}
    for name in names:
        check()
        old, left, right = [_candidate(side, paths, prefix, name) for side, paths in zip(manifests, entries)]
        # Choose the record and its exact blob together. Text merging two C attempts would
        # produce a new attempt for which neither saved score is valid.
        if left == right or right == old:
            winner = 1
        elif left == old:
            winner = 2
        else:
            winner = _winner(left, right, manifests[1:], layouts, name)
        chosen = (old, left, right)[winner]
        if chosen is not _ABSENT:
            functions[name] = chosen[0]
        paths = {candidate[1] for candidate in (old, left, right) if candidate is not _ABSENT}
        if left != right and (left != old and right != old or paths & conflicts):
            for candidate, side in zip((old, left, right), entries):
                owned = {candidate[1]} if candidate is not _ABSENT else set()
                if (paths - owned) & side.keys():
                    raise Incomparable
            winners[name] = winner
            updates.update({path: None for path in paths})
            if chosen is not _ABSENT:
                updates[chosen[1]] = chosen[2]
    # Saved scores cannot be compared through a header or target layout change.
    if winners:
        context = ('platform', 'layout', 'symbols', 'headers', 'function_headers')
        headers = [{path: entry for path, entry in side.items() if path.startswith(prefix + 'include/')}
                   for side in entries]
        if (any(any(side.get(key) != base.get(key) for key in context) for side in manifests[1:])
                or any(side != headers[0] for side in headers[1:])):
            raise Incomparable('The branches changed saved headers, symbols or target layout. '
                               'Competing candidates need to be judged against the merged project.')
    try:
        blobs = _merged_metadata(root, prefix, entries, conflicts, manifests, functions, winners)
    except (NeedsReview, ValueError, KeyError, TypeError, AttributeError):
        if winners:
            raise Incomparable from None
        raise
    remaining = conflicts - updates.keys() - blobs.keys()
    if remaining:
        raise NeedsReview('Some conflicting files have no saved verdict the app can use.', paths=remaining)
    return updates, blobs, len(winners)


def _merged_metadata(root, prefix, entries, conflicts, manifests, functions, winners):
    special = {'functions', 'saved_at', 'revision', 'notes', 'verification'}
    manifest = _merge_mapping(*({key: value for key, value in side.items() if key not in special}
                                 for side in manifests))
    for name, winner in winners.items():
        if name in functions and _profile(manifest, name, functions[name]) != _profile(manifests[winner], name, functions[name]):
            raise Incomparable
    manifest.update(functions=functions, saved_at=max(side.get('saved_at', 0) for side in manifests),
                    revision=max(side.get('revision', 0) for side in manifests) + 1,
                    notes=_merge_notes(*(side.get('notes', []) for side in manifests)),
                    verification=_merge_proof(manifests, winners))
    if manifest.get('format') != saves.FORMAT:
        # Format 1 keeps notes and verification in the manifest itself.
        blobs = {prefix + saves.MANIFEST: (json.dumps(manifest, indent=1) + '\n').encode()}
    else:
        blobs = repository_progress.manifest_blobs(prefix, manifest)
    for path in blobs:
        if any(path in side and side[path][0] != '100644' for side in entries):
            raise NeedsReview
    blobs.update(_report_blobs(root, prefix, entries, conflicts, manifests, manifest))
    return blobs


def prepare(net, slug, root, head, target, preview, check):
    """Prepare a two-parent commit with a private index. The caller applies it only after rechecking HEAD."""
    clean, payload = preview
    parts = payload.split(b'\0')
    tree = parts[0].decode('ascii')
    conflicts = set()
    if not clean:
        for part in parts[1:]:
            if not part:
                break
            conflicts.add(part.decode('utf-8', errors='surrogateescape'))
        if not conflicts:
            raise NeedsReview
    if not repository_progress.linked_checkout(net, slug, root):
        if conflicts:
            raise NeedsReview('This checkout has no linked portable save to resolve conflicts from.', paths=conflicts)
        return None
    relative = saves.default_folder(net, slug).resolve().relative_to(root).as_posix()
    prefix = '' if relative == '.' else relative + '/'
    outside = {path for path in conflicts if not path.startswith(prefix)}
    if outside:
        raise NeedsReview('Both histories changed files outside the linked progress folder. Saved matches and '
                          'draft scores cannot resolve changes to the repository structure or build.',
                          paths=outside)
    bases = git(root, 'merge-base', '--all', head, target).splitlines()
    if len(bases) != 1:
        if conflicts:
            raise NeedsReview
        return None
    try:
        updates, blobs, count = _plan(root, (bases[0], head, target), prefix, conflicts, check)
    except Incomparable:
        raise
    except NeedsReview:
        if conflicts:
            raise
        return None
    except (ValueError, KeyError, TypeError, AttributeError):
        if conflicts:
            raise NeedsReview(paths=conflicts) from None
        return None
    if not conflicts and not updates:
        return None
    commit = commit_merge(root, head, target, tree, updates, blobs, check,
                          message='Merge origin and keep the best saved decompilation progress')
    return commit, count


def commit_merge(root, head, target, tree, updates, blobs, check, *, message):
    """Create the prepared two-parent commit without changing the checkout or its real index."""
    with tempfile.TemporaryDirectory(prefix='decomp-progress-merge-') as temp:
        env = {'GIT_INDEX_FILE': str(Path(temp) / 'index')}
        git(root, 'read-tree', tree, env=env)
        zero = '0' * len(head)
        records = [f'0 {zero}\t{path}\0' if entry is None else f'{entry[0]} {entry[1]}\t{path}\0'
                   for path, entry in updates.items()]
        for path, text in blobs.items():
            check()
            oid = git(root, 'hash-object', '-w', '--stdin', input=text)
            records.append(f'100644 {oid}\t{path}\0')
        git(root, 'update-index', '-z', '--index-info', env=env, input=''.join(records).encode())
        merged = git(root, 'write-tree', env=env)
        check()
        return git(root, 'commit-tree', merged, '-p', head, '-p', target, '-m', message)
