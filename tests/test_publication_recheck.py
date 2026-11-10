"""Only the app's proved file changes may advance a saved-recheck signature."""
import errno
import json
from pathlib import Path

import pytest

from decompnet.app import saves
from decompnet.app.progress import ProgressMoved, _Recheck

from .test_progress import project  # noqa: F401


@pytest.mark.parametrize('change', ['none', 'before', 'untouched', 'written', 'manifest',
                                    'created', 'removed', 'directory', 'written-symlink', 'deleted-recreated', 'failure'])
def test_publication_does_not_hide_external_edits_during_save(project, monkeypatch, change):  # noqa: F811
    net, folder, _ = project
    worker = net.progress
    (folder/'best').mkdir(exist_ok=True)
    (folder/'best/held.c').write_text('saved input')
    (folder/'best/retired.c').write_text('retired input')
    active = _Recheck(folder, worker._signature(folder), ready=True)
    worker.rechecking['game'] = active
    before = active.signature
    if change == 'before':(folder/'best/held.c').write_text('external before save')
    original = Path.replace
    def replace(path, target):
        result = original(path, target)
        if target == folder/'best/own.c':
            if change == 'untouched':(folder/'best/held.c').write_text('external while saving')
            elif change == 'written':target.write_text('external after app write')
            elif change == 'manifest':
                data = json.loads((folder/saves.MANIFEST).read_text())
                data['instructions'] = 'external instructions'
                (folder/saves.MANIFEST).write_text(json.dumps(data))
            elif change == 'created':(folder/'best/new.c').write_text('external addition')
            elif change == 'removed':(folder/'best/held.c').unlink()
            elif change == 'directory':
                (folder/'include').rename(folder.parent/'moved-include')
                (folder/'include').symlink_to(folder.parent/'moved-include', target_is_directory=True)
            elif change == 'written-symlink':
                (folder.parent/'outside.c').write_text('app output')
                target.unlink()
                target.symlink_to(folder.parent/'outside.c')
            elif change == 'deleted-recreated':(folder/'best/retired.c').write_text('external recreation')
            elif change == 'failure':raise OSError(errno.EIO, 'Synthetic write failure')
        return result
    monkeypatch.setattr(Path, 'replace', replace)
    try:
        if change == 'failure':
            with pytest.raises(OSError, match='Synthetic write failure'):
                saves._publish(folder, {'best/own.c':'app output'}, {'best/retired.c':'retired input'})
        else:assert saves._publish(folder, {'best/own.c':'app output'}, {'best/retired.c':'retired input'})
        assert not worker.publishing
        if change == 'none':
            worker._check_rechecking_files('game')
            assert active.signature != before
        else:
            assert active.signature == before, 'An external edit or failed save was acknowledged unseen'
            with pytest.raises(ProgressMoved):worker._check_rechecking_files('game')
    finally:worker.rechecking.clear()


@pytest.mark.parametrize('elsewhere', [False, True])
def test_publication_without_a_recheck_does_not_scan_saved_files(project, monkeypatch, elsewhere):  # noqa: F811
    net, folder, _ = project
    if elsewhere:net.progress.rechecking['elsewhere'] = _Recheck(folder.parent/'other', (), ready=True)
    monkeypatch.setattr(net.progress, '_signature', lambda *a, **kw: pytest.fail('Inactive watcher scanned files'))
    assert saves._publish(folder, {'best/new.c':'app output'})
    assert not net.progress.publishing


def test_overlapping_publications_leave_recheck_guard_invalid(project, monkeypatch):  # noqa: F811
    net, folder, _ = project
    worker = net.progress
    active = _Recheck(folder, worker._signature(folder), ready=True)
    worker.rechecking['game'] = active
    before = active.signature
    original = Path.replace
    def replace(path, target):
        result = original(path, target)
        if target == folder/'best/outer.c':
            assert saves._publish(folder, {'best/inner.c':'nested app output'})
        return result
    monkeypatch.setattr(Path, 'replace', replace)
    try:
        assert saves._publish(folder, {'best/outer.c':'outer app output'})
        assert active.signature == before
        with pytest.raises(ProgressMoved):worker._check_rechecking_files('game')
        assert not worker.publishing
    finally:worker.rechecking.clear()


def test_later_observer_failure_cleans_up_prior_receipts(project, monkeypatch):  # noqa: F811
    net, folder, _ = project
    worker = net.progress
    worker.rechecking['game'] = _Recheck(folder, worker._signature(folder), ready=True)
    class FailedWatcher:
        def publish_begin(self, folder):raise RuntimeError('Synthetic observer failure')
    # Keep the order deterministic: the actual receipt exists before failure.
    monkeypatch.setattr(saves, 'publish_watchers', [worker, FailedWatcher()])
    try:
        with pytest.raises(RuntimeError, match='Synthetic observer failure'):
            saves._publish(folder, {'best/new.c':'app output'})
        worker._check_rechecking_files('game')  # no file writes took place
        assert not worker.publishing
        assert not (folder/'best/new.c').exists()
    finally:worker.rechecking.clear()
