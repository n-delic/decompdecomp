"""Reconcile independent upgrades of the same portable save against the local build."""
import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path

from .. import compiler, hostenv
from ..network import NetworkError
from . import rebuildable_repository, repository_progress, repository_sync, save_readme, saves
from . import repository_pull_progress as portable
from .repository_git import git

PREFIX = 'progress/'


def _tree(root, revision):
    entries = {}
    for record in filter(None, git(root, 'ls-tree', '-rz', revision).split('\0')):
        meta, path = record.split('\t', 1)
        mode, _, oid = meta.split()
        entries[path] = (mode, oid)
    return entries


def _blob(root, entry):
    if entry[0] != '100644':
        raise portable.NeedsReview
    return repository_sync.git(root, 'cat-file', 'blob', entry[1])


def _generated(root, tree, changed, retired, check):
    """A marker can authorize replacing generated files, but never unrelated committed edits."""
    record = json.loads(_blob(root, tree[rebuildable_repository.MARKER]))
    if record.get('format') != 1:
        raise portable.NeedsReview
    hashes = record['hashes']
    deleted = changed - tree.keys()
    if deleted - retired:
        raise portable.NeedsReview('One branch removed a file outside the original portable save.',
                                   paths=deleted - retired)
    for path in (changed & tree.keys()) - {rebuildable_repository.MARKER}:
        check()
        if (path in rebuildable_repository.KEPT_DOCS or not rebuildable_repository.public(path)
                or hashlib.sha256(_blob(root, tree[path])).hexdigest() != hashes.get(path)):
            raise portable.NeedsReview('A differing build file has committed edits outside the generated upgrade.',
                                       paths=[path])


def _manifest(root, revision, prefix):
    return json.loads(repository_progress.committed_manifest_text(root, prefix + saves.MANIFEST, revision))


def _layout(local, incoming):
    """Every incoming boundary must exist in the local layout. Ambiguous overlays need a different adapter."""
    def by_address(manifest):
        result = {entry['vram']: entry for entry in manifest['layout']}
        if len(result) != len(manifest['layout']):
            raise portable.NeedsReview('The saved layouts contain overlapping function addresses.')
        return result

    mine, theirs = map(by_address, (local, incoming))
    if any(address not in mine or mine[address]['size'] != row['size'] for address, row in theirs.items()):
        raise portable.NeedsReview('The local layout does not contain all incoming function boundaries.')
    return {row['name']: mine[address]['name'] for address, row in theirs.items()}


def _rejudge(net, proj, fn, source):
    header = net._context(proj['id'], fn['unit'])['header']
    return net._saved_match_verdict(proj, header, fn, source)[:2]


def _import_candidates(net, proj, root, trees, manifests, names, report, check):
    local, incoming = manifests
    functions = local['functions']
    candidates = [(old, names[old]) for old in incoming['functions'] if old in names
                  and 'matched_by' not in functions.get(names[old], {})]
    counts, updates, matches = Counter(), {}, []
    store = net.project_store(proj['id'])
    for index, (old, name) in enumerate(candidates):
        check()
        report.progress({'phase': 'Rejudging incoming candidates', 'finished': index, 'total': len(candidates)})
        left = portable._candidate(local, trees[0], PREFIX, name)
        right = portable._candidate(incoming, trees[1], PREFIX, old)
        if left is not portable._ABSENT and left[2] == right[2] and name == old:
            continue
        row = store.one('SELECT * FROM functions WHERE project_id=? AND name=?', proj['id'], name)
        if row is None or row['status'] not in ('open', 'matched') or row['vram'] != right[0]['vram']:
            counts['unavailable'] += 1
            continue
        source = repository_sync.source_text(_blob(root, right[2]).decode(), old, name)
        matched, score = _rejudge(net, proj, row, source)
        if score is None:
            counts['failed'] += 1
            continue
        if not matched and left is not portable._ABSENT:
            _, previous = _rejudge(net, proj, row, _blob(root, left[2]).decode())
            if previous is not None and previous <= score:
                continue
        author = right[0].get('matched_by') or right[0].get('best_by') or 'restored'
        if matched:
            info = {'matched_by': author, 'readable': bool(right[0].get('readable'))}
            matches.append(dict(row, matched_source=source))
        else:
            info = {'best_by': author, 'best_score': score}
        functions[name] = {'vram': row['vram'], **info}
        path = PREFIX + ('src/' if matched else 'best/') + saves._source_file(name)
        if left is not portable._ABSENT and left[1] != path:
            updates[left[1]] = None
        updates[path] = saves._source_text(source).encode()
        local.get('verification', {}).get('matches', {}).pop(name, None)
        counts['matches' if matched else 'drafts'] += 1
    report(f"Rejudging kept {counts['matches']} incoming matches and {counts['drafts']} improved drafts. "
           f"{counts['failed']} did not compile and {counts['unavailable']} had no eligible local target.")
    return updates, matches, counts


def _notes(base, local, incoming, names):
    def key(note):
        return json.dumps(note, sort_keys=True)
    seen = {key(note) for side in (base, local) for note in side.get('notes', [])}
    for note in incoming.get('notes', []):
        if key(note) in seen:
            continue
        if name := note.get('function'):
            if name not in names:
                continue
            note = dict(note, function=names[name])
        signature = key(note)
        if signature not in seen:
            local.setdefault('notes', []).append(note)
            seen.add(signature)


def _verify(root, stage, proj, net, rows, report):
    from . import repository_sync_build
    repository_sync.copy_checkout(root, stage)
    manifest, owned, files, accepted, skipped, _ = repository_sync.integrate(net, proj, root, stage, report, rows=rows)
    if set(accepted) != {row['name'] for row in rows}:
        reasons = '\n'.join(item['function'] + ': ' + item['reason'] for item in skipped)
        raise NetworkError('An incoming match could not be installed in the local build. The merge was not applied.\n'
                           + reasons[-3000:])
    output_path = repository_sync.inside(stage, manifest['output'])
    if output_path.exists():
        raise NetworkError('The staged checkout already contains the rebuild output. Remove it from Git before retrying.')
    report.progress({'phase': 'Verifying the complete rebuilt game'})
    report('Rebuilding with the local layout and checking every byte…')
    code, output = repository_sync.command(stage, [hostenv.console_python(), 'build.py'], report)
    if code:
        raise NetworkError('The merged repository could not rebuild. The merge was not applied.\n' + output)
    cartridge = manifest.get('cartridge') or {}
    original = repository_sync.inside(stage, cartridge.get('image', manifest['image'])).read_bytes()
    if not output_path.is_file():
        raise NetworkError('The build produced no game image. The merge was not applied.')
    built = output_path.read_bytes()
    if built != original or hashlib.sha1(original).hexdigest() != cartridge.get('sha1', manifest['sha1']):
        raise NetworkError('The merged build differs from the original game. The merge was not applied.')
    if accepted:
        repository_sync_build.remove_unused_headers(stage, files)
        files.add('build.json')
        repository_sync.update_objdiff(stage, manifest)
        if (stage/'objdiff.json').is_file():
            files.add('objdiff.json')
        state = stage/repository_sync.STATE
        receipt = json.loads(state.read_text())
        owned.update({name: repository_sync.digest(stage/name) for name in files if name.startswith('src/')})
        receipt.update(owned=owned, verified_build=True,
                       accepted=sorted(set(receipt.get('accepted', [])) | set(accepted)))
        state.write_text(json.dumps(receipt, indent=2) + '\n')
        files.add(repository_sync.STATE)
    return {name: (stage/name).read_bytes() for name in files}


def prepare(net, slug, root, head, target, report, check):
    """Return None for ordinary conflicts. An eligible upgrade is prepared without changing the checkout."""
    marker = rebuildable_repository.MARKER
    if saves.default_folder(net, slug).resolve() != root/PREFIX.rstrip('/'):
        return None
    bases = git(root, 'merge-base', '--all', head, target).splitlines()
    if len(bases) != 1:
        return None
    if any(not git(root, 'ls-tree', '--name-only', rev, '--', marker) for rev in (head, target)):
        return None
    base, local, incoming = [_tree(root, revision) for revision in (bases[0], head, target)]
    if (marker in base or saves.MANIFEST not in base or
            any(marker not in side or PREFIX + saves.MANIFEST not in side for side in (local, incoming))):
        return None
    ancestor, mine, theirs = [_manifest(root, revision, prefix)
                              for revision, prefix in ((bases[0], ''), (head, PREFIX), (target, PREFIX))]
    game = mine.get('game', {}).get('sha1')
    if not game or any(item.get('game', {}).get('sha1') != game for item in (ancestor, theirs)):
        return None
    changed = {path for path in local.keys() | incoming.keys()
               if local.get(path) != incoming.get(path) and not path.startswith(PREFIX)}
    # Upgrade commits can still have identical copies of old portable files at the root.
    retired = repository_progress.owned(ancestor)
    for tree in (local, incoming):
        _generated(root, tree, {path for path in changed if tree.get(path) != base.get(path)}, retired, check)
    extra = {path for path in local.keys() | incoming.keys()
             if path.startswith(PREFIX) and local.get(path) != incoming.get(path)
             and (not rebuildable_repository.public(path) or path.startswith(PREFIX + 'data'))}
    if extra:
        raise portable.NeedsReview('Some incoming saved files need a separate merge and verification.', paths=extra)
    names = _layout(mine, theirs)
    proj = net.project_store(slug).one('SELECT * FROM projects WHERE slug=?', slug)
    if (not net.is_rom(proj) or net.cfg(proj).get('rom', {}).get('sha1') != game
            or not saves._layout_matches_db(net, proj['id'], mine['layout'])):
        raise portable.NeedsReview('The open project does not match the committed local layout.')
    report('Both branches upgraded the same save. Keeping the local layout and rejudging incoming code.')
    with compiler.cancellable(check), tempfile.TemporaryDirectory(prefix='decomp-verified-pull-') as directory:
        stage = Path(directory)
        updates, matches, counts = _import_candidates(net, proj, root, (local, incoming), (mine, theirs),
                                                      names, report, check)
        _notes(ancestor, mine, theirs, names)
        mine['revision'] = max(mine.get('revision', 0), theirs.get('revision', 0)) + 1
        updates.update(repository_progress.manifest_blobs(PREFIX, mine))
        progress, svg = portable._report(mine)
        if readme := local.get(PREFIX + 'README.md'):
            text = _blob(root, readme).decode()
            updates[PREFIX + 'README.md'] = save_readme._replace(text, 'progress', progress).encode()
        updates[PREFIX + save_readme.CARD] = svg.encode()
        updates.update(_verify(root, stage, proj, net, matches, report))
        deleted = dict.fromkeys(path for path, data in updates.items() if data is None)
        blobs = {path: data for path, data in updates.items() if data is not None}
        commit = portable.commit_merge(root, head, target, head, deleted, blobs, check,
                                       message='Merge origin progress and verify the rebuilt game byte for byte')
        return commit, counts['matches'] + counts['drafts']
