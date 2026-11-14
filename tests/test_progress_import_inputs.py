"""A slow save import must revalidate captured inputs even when it does not write them."""
import json

import pytest

from decompnet.app import saves
from decompnet.network import NetworkError
from tests import test_progress_speed as speed


@pytest.mark.parametrize('import_header,edit', [(True, 'header'), (False, 'header'), (False, 'source'), (False, 'notes')])
def test_editor_change_before_publication_rolls_back_import(tmp_path, monkeypatch, import_header, edit):
    monkeypatch.setattr(speed, 'FUNCTIONS', 20)
    net, folder, pid, judged, _ = speed.project.__wrapped__(tmp_path)
    store = net.project_store(pid)
    header = folder / 'include' / json.loads(saves.read_manifest_text(folder))['headers']['']
    def state():
        return ([tuple(r) for r in store.all('SELECT id,status,matched_source,best_source FROM functions ORDER BY id')],
                [tuple(r) for r in store.all('SELECT version,header FROM context_versions ORDER BY version,unit')],
                [tuple(r) for r in store.all('SELECT text FROM notes ORDER BY id')])
    before = state()
    if import_header:header.write_text('typedef int FirstType;\n')
    else:(folder / saves.NOTES_FILE).write_text(json.dumps([{'function': 'f0', 'text': 'first', 'by': 'synthetic'}]))
    if edit == 'header':path, text = header, 'typedef int LaterType;\n'
    elif edit == 'source':path, text = folder / 'src' / 'f0.c', 'int f0(void) { return 99; }\n'
    else:path, text = folder / saves.NOTES_FILE, json.dumps([{'function': 'f0', 'text': 'later', 'by': 'synthetic'}])
    publish = saves._publish
    calls = []
    def racing_publish(*args, **kwargs):
        if not calls:path.write_text(text)
        calls.append(True)
        return publish(*args, **kwargs)
    monkeypatch.setattr(saves, '_publish', racing_publish)
    monkeypatch.setattr(net.progress, '_judge_saved', lambda *a, **k: pytest.fail('Changed inputs must not reach judging'))
    assert net.progress.sync('game', force=True) is False
    assert calls and state() == before
    assert path.read_text() == text
    assert not judged


def test_guard_only_inputs_are_never_written_and_keep_default_publish_behavior(tmp_path):
    folder = tmp_path / 'progress'
    (folder / 'include').mkdir(parents=True)
    header = folder / 'include' / 'types.h'
    header.write_text('typedef int T;\n')
    assert saves._publish(folder, {'README.md': 'first'}, expected={'include/types.h': 'typedef int T;\n'},
                          guarded=iter(['include/types.h']), chosen=True)
    assert header.read_text() == 'typedef int T;\n'
    header.write_text('typedef long T;\n')
    assert not saves._publish(folder, {'README.md': 'second'}, expected={'include/types.h': 'typedef int T;\n'},
                              guarded={'include/types.h'}, chosen=True)
    assert (folder / 'README.md').read_text() == 'first'
    # Other publishers still guard just their own writes unless extra inputs are supplied.
    assert saves._publish(folder, {'src/out.c': 'int out(void) { return 0; }\n'},
                          expected={'include/types.h': 'old'}, chosen=True)
    assert (folder / 'src' / 'out.c').read_text() == 'int out(void) { return 0; }\n'
    assert header.read_text() == 'typedef long T;\n'


@pytest.mark.parametrize('guarded', ['../outside.h', 'include/alias.h'])
def test_guarded_inputs_keep_path_and_symlink_validation(tmp_path, guarded):
    folder = tmp_path / 'progress'
    (folder / 'include').mkdir(parents=True)
    (tmp_path / 'outside.h').write_text('private synthetic input')
    (folder / 'include' / 'alias.h').symlink_to(tmp_path / 'outside.h')
    with pytest.raises(NetworkError, match='unsafe file path|symbolic links'):
        saves._publish(folder, {'README.md': 'text'}, expected={guarded: 'private synthetic input'},
                       guarded=iter([guarded]), chosen=True)
    assert not (folder / 'README.md').exists()
