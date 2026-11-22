"""A save folder's watch never vouches for a folder that changed, and spares a quiet one its file-by-file reading."""
import os
import sys

import pytest

from decompnet.app import folder_watch
from decompnet.app.progress import Progress

from .test_progress import project  # noqa: F401 (the fixture)


@pytest.fixture
def watched(tmp_path):
    save = tmp_path / 'save'
    (save / 'src').mkdir(parents=True)
    (save / 'src' / 'a.c').write_text('x')
    watch = folder_watch.open_watch(save, [tmp_path / 'fences'])
    if watch is None:
        pytest.skip(f'no folder watch on {sys.platform} (ROADMAP.md)')
    yield save, watch
    watch.close()


def settled(watch):
    """A mark after everything before it was taken in (the macOS fence may report the setup's writes late)."""
    watch.unchanged_since(watch.mark())
    mark = watch.mark()
    if mark is None:
        # The watch gave up: macOS's fseventsd took over FENCE_SECONDS to report the fence file (a machine under heavy
        # load, load average 40+ here). It then vouches for nothing and the app reads the save file by file, which
        # test_failed_watch_is_not_reopened covers; there is nothing here to check.
        pytest.skip('the OS did not report the fence file in time (machine too busy); the watch fell back')
    return mark


CHANGES = {
    'in place, same size, folder untouched': lambda save: (save / 'src' / 'a.c').write_text('y'),
    'touch': lambda save: os.utime(save / 'src' / 'a.c', None),
    'rename': lambda save: (save / 'src' / 'a.c').rename(save / 'src' / 'b.c'),
    'delete': lambda save: (save / 'src' / 'a.c').unlink(),
    'manifest': lambda save: (save / 'decompdecomp.json').write_text('{}'),
    'new subfolder': lambda save: (save / 'best').mkdir(),
}


@pytest.mark.parametrize('change', CHANGES, ids=list(CHANGES))
def test_every_change_is_seen_at_once(watched, change):
    save, watch = watched
    mark = settled(watch)
    if not watch.unchanged_since(mark):
        pytest.skip('the OS reported late events from the setup (machine too busy); the watch stays conservative')
    CHANGES[change](save)
    assert not watch.unchanged_since(mark)  # immediately: no latency the check could miss


def test_a_file_in_a_subfolder_made_after_the_watch_is_seen(watched):
    save, watch = watched
    (save / 'best').mkdir()
    mark = settled(watch)
    (save / 'best' / 'f.c').write_text('draft')
    assert not watch.unchanged_since(mark)


def test_many_quick_in_place_edits_are_never_missed(watched):
    """FSEvents alone missed 498 of 500 of these (the fence is what makes it strict)."""
    save, watch = watched
    path = save / 'src' / 'a.c'
    for index in range(40):
        mark = settled(watch)
        with open(path, 'r+') as handle:
            handle.write(str(index % 10))
        assert not watch.unchanged_since(mark), index


def test_a_closed_or_disabled_watch_vouches_for_nothing(watched, monkeypatch, tmp_path):
    save, watch = watched
    mark = settled(watch)
    watch.close()
    assert not watch.unchanged_since(mark)
    monkeypatch.setenv('DECOMP_FOLDER_WATCH', '0')
    assert folder_watch.open_watch(save, [tmp_path / 'fences']) is None and not folder_watch.worth(10 ** 6)


@pytest.mark.skipif(sys.platform != 'darwin', reason='uses the native macOS event queue')
def test_closing_a_watch_drains_pending_native_callbacks(tmp_path, monkeypatch):
    save = tmp_path / 'save'
    save.mkdir()
    fences = tmp_path / 'fences'
    native = folder_watch._mac_libraries()
    synchronize = native.dispatch_sync
    closed = []

    def synchronized(queue, context, marker):
        synchronize(queue, context, marker)
        closed.append(queue)

    monkeypatch.setattr(native, 'dispatch_sync', synchronized)
    for index in range(8):
        watch = folder_watch.open_watch(save, [fences])
        if index == 0 and watch is None:
            pytest.skip('macOS folder watch is unavailable')
        assert watch is not None
        (save / 'changed').write_text(str(index))
        watch.close()
        watch.close()
        assert not watch.unchanged_since(0)
    assert closed == [native.queue] * 8


def test_a_quiet_watched_save_is_not_read_file_by_file_and_an_in_place_edit_still_is(project, monkeypatch):  # noqa: F811
    net, folder, judged = project
    monkeypatch.setattr(folder_watch, 'WORTH_FILES', 0)  # (the demo save is small; a real one is thousands of files)
    if not folder_watch.worth(1):
        pytest.skip(f'no folder watch on {sys.platform} (ROADMAP.md)')
    (folder / 'src').mkdir(exist_ok=True)
    path = folder / 'src' / 'f.c'
    path.write_text('good original')
    for _ in range(3):  # import, publish, confirm: the watch vouches from here on
        net.progress.sync('game', force=True)
    if net.progress.watches.get('game') is None:
        pytest.skip('the OS did not report the fence file in time (machine too busy); the watch fell back')

    deep = []
    real = Progress._signature

    def counted(folder, *, deep_read=True, **kw):
        deep_read = kw.get('deep', deep_read)
        deep.append(deep_read)
        return real(folder, deep=deep_read)
    monkeypatch.setattr(Progress, '_signature', staticmethod(counted))
    net.progress.sync('game', force=True)
    assert True not in deep  # nothing changed: no file was stat'ed one by one

    stamp = path.parent.stat()
    path.write_text('good edited!!')  # an editor's in-place write leaves the folder's own stat alone
    os.utime(path.parent, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    net.progress.sync('game', force=True)
    assert judged[-1] == ('f', 'good edited!!')


class FailingWatch(folder_watch.FolderWatch):
    def __init__(self, folder):
        super().__init__(folder)
        self.settles = 0
        self.closed = 0

    def _settle(self):
        self.settles += 1
        self.broken = True  # a timed-out fence or failed OS notification stream

    def close(self):
        self.closed += 1
        super().close()


def test_failed_watch_is_not_reopened_and_file_edits_are_still_seen(project, monkeypatch):  # noqa: F811
    net, folder, _ = project
    opened = []
    monkeypatch.setattr(folder_watch, 'worth', lambda files: True)

    def open_failed(path, fences):
        watch = FailingWatch(path)
        opened.append(watch)
        return watch

    monkeypatch.setattr(folder_watch, 'open_watch', open_failed)
    (folder / 'src').mkdir(exist_ok=True)
    path = folder / 'src' / 'probe.c'
    path.write_text('first')
    first = net.progress._folder_signature('game', folder)
    net.progress.signatures['game'] = first
    # Every failed watch must fall back to actual file stats, including later edits.
    for text in ('later content', 'another longer edit', 'final edit with more bytes'):
        path.write_text(text)
        current = net.progress._folder_signature('game', folder)
        assert current != first
        assert current == Progress._signature(folder)
    assert len(opened) == 1
    assert opened[0].settles == 1
    assert opened[0].closed == 1
    assert 'game' not in net.progress.signature_marks


def test_failed_watch_does_not_disable_a_relocated_save(project, monkeypatch, tmp_path):  # noqa: F811
    net, folder, _ = project
    broken = FailingWatch(folder)
    broken.broken = True
    net.progress.watches['game'] = broken
    net.progress.signature_marks['game'] = 42
    monkeypatch.setattr(folder_watch, 'worth', lambda files: True)
    opened = []

    def open_healthy(path, fences):
        opened.append(path)
        return folder_watch.FolderWatch(path)

    monkeypatch.setattr(folder_watch, 'open_watch', open_healthy)
    assert net.progress._watch('game', folder, 1000) is None
    other = tmp_path / 'moved-save'
    other.mkdir()
    assert net.progress._watch('game', other, 1000).folder == other
    assert opened == [other]
    assert broken.closed == 1
    assert 'game' not in net.progress.unwatchable


def test_mark_never_waits_for_a_broken_watch(tmp_path):
    watch = FailingWatch(tmp_path)
    assert watch.mark() is None
    assert watch.mark() is None
    assert watch.settles == 1
