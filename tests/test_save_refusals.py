"""A save whose write keeps being refused says which files stop it.

An N64 game's saves rolled back silently for days: each sync found a file it did not expect (two functions' files
were one file on a disk that ignores capitals), rolled back and returned without a word."""
import json

from decompnet.app import progress as progress_module
from decompnet.app import saves

from . import test_repository_progress

progress = test_repository_progress.progress


def edit_instructions(folder, text):
    path = folder/saves.MANIFEST
    manifest = json.loads(path.read_text())
    manifest['instructions'] = text
    path.write_text(json.dumps(manifest, indent=1) + '\n')


def test_a_save_refused_again_and_again_names_the_file(progress, monkeypatch):
    net, root, _ = progress
    folder = saves.default_folder(net, 'game')
    original, edits = saves._publish, []
    def editor_writes_meanwhile(target, files, previous=None, expected=None, *, chosen=False):
        if expected is not None:
            edits.append(sorted(files))
            edit_instructions(target, f'An editor saved meanwhile ({len(edits)})')
        return original(target, files, previous, expected, chosen=chosen)
    monkeypatch.setattr(saves, '_publish', editor_writes_meanwhile)
    edit_instructions(folder, 'Edited by hand')
    assert net.progress.sync('game', force=True) is False
    assert edits and net.progress.refusals['game'] == 1  # (the rollback is counted, not just dropped)
    assert 'game' not in net.progress.errors  # (one refusal: an editor saving meanwhile, read next time)
    net.progress.refusals['game'] = progress_module.REFUSALS_BEFORE_ERROR - 1
    edit_instructions(folder, 'Edited by hand again')
    net.progress.sync('game', force=True)
    message = net.progress.errors['game']
    assert message.startswith(f'Saving keeps stopping: {saves.MANIFEST} changed on disk')
    assert 'Check what writes there' in message
    monkeypatch.setattr(saves, '_publish', original)
    edit_instructions(folder, 'Edited by hand, finally saved')
    assert net.progress.sync('game', force=True) is True
    assert 'game' not in net.progress.errors
    assert 'game' not in net.progress.refusals


def test_one_refusal_is_not_an_error(progress):
    net, _, _ = progress
    folder = saves.default_folder(net, 'game')
    disk = progress_module._read(folder)
    changed = dict(disk, **{saves.MANIFEST: '{}'})  # (what the sync had read; the disk now differs)
    for attempt in range(progress_module.REFUSALS_BEFORE_ERROR - 1):
        assert net.progress._refused('game', folder, {saves.MANIFEST}, changed, 'token', 'key') is False
        assert 'game' not in net.progress.errors
    net.progress._refused('game', folder, {saves.MANIFEST}, changed, 'token', 'key')
    assert saves.MANIFEST in net.progress.errors['game']


def test_a_refusal_whose_files_match_again_stays_quiet(progress):
    net, _, _ = progress
    folder = saves.default_folder(net, 'game')
    disk = progress_module._read(folder)
    for _ in range(progress_module.REFUSALS_BEFORE_ERROR + 1):
        net.progress._refused('game', folder, set(disk), disk, 'token', 'key')
    assert 'game' not in net.progress.errors  # (nothing on disk differs: there is nothing to name)


def test_unexpected_files_lists_every_differing_file(tmp_path):
    (tmp_path/'src').mkdir()
    (tmp_path/'src/a.c').write_text('a')
    (tmp_path/'src/b.c').write_text('changed')
    names = {'src/a.c', 'src/b.c', 'src/new.c', 'README.md'}
    expected = {'src/a.c': 'a', 'src/b.c': 'b', 'src/new.c': 'x'}
    assert saves.unexpected_files(tmp_path, names, expected) == ['src/b.c', 'src/new.c']
    assert saves.unexpected_files(tmp_path, names, expected, first=True) == ['src/b.c']
