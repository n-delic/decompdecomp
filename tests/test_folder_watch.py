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
    return watch.mark()


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
    assert watch.unchanged_since(mark)
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
    assert net.progress.watches.get('game') is not None

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
