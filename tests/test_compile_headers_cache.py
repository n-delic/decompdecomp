"""Header reads reuse bytes only while a file is provably unchanged, and staging keeps its exact result."""
import os

from decompnet import compile_headers


def trusting(monkeypatch):
    """A file touched in the last two seconds is never kept, and a change time can't be backdated: let a test's
    fresh files count as old, since only the stamp comparison is under test."""
    monkeypatch.setattr(compile_headers, '_FRESH_NS', -1)
    compile_headers._KEPT.clear()


def test_unchanged_headers_are_read_once_and_normalized(tmp_path, monkeypatch):
    trusting(monkeypatch)
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'a.h').write_bytes(b'#define A 1\r\n')
    (tmp_path / 'sub' / 'b.inc').write_bytes(b'x\r\ny\r\n')
    (tmp_path / 'c.txt').write_bytes(b'keep\r\n')
    files = compile_headers.read([tmp_path])
    assert files == {'a.h': b'#define A 1\n', f'sub{os.sep}b.inc': b'x\ny\n', 'c.txt': b'keep\r\n'}
    opened = []
    real = open
    monkeypatch.setattr('builtins.open', lambda *args, **kwargs: opened.append(args[0]) or real(*args, **kwargs))
    assert compile_headers.read([tmp_path]) == files
    assert not opened


def test_a_same_size_edit_with_the_modification_time_restored_is_seen(tmp_path, monkeypatch):
    trusting(monkeypatch)
    path = tmp_path / 'a.h'
    path.write_bytes(b'#define A 1\n')
    assert compile_headers.read([tmp_path]) == {'a.h': b'#define A 1\n'}
    before = path.stat().st_mtime_ns
    path.write_bytes(b'#define A 2\n')
    os.utime(path, ns=(before, before))
    assert compile_headers.read([tmp_path]) == {'a.h': b'#define A 2\n'}


def test_a_just_written_file_is_never_kept(tmp_path):
    compile_headers._KEPT.clear()
    path = tmp_path / 'a.h'
    path.write_bytes(b'one\n')
    compile_headers.read([tmp_path])
    assert not compile_headers._KEPT
    path.write_bytes(b'two\n')
    assert compile_headers.read([tmp_path]) == {'a.h': b'two\n'}


def test_stage_makes_the_folder_hold_exactly_the_files(tmp_path):
    inc = tmp_path / 'inc'
    (inc / 'gone').mkdir(parents=True)
    (inc / 'gone' / 'x.h').write_text('old')
    (inc / 'stale.h').write_text('old')
    (inc / 'a.h').write_text('wrong')
    files = {'a.h': b'right', f'd{os.sep}e{os.sep}f.h': b'deep'}
    written = compile_headers.stage(inc, files)
    assert sorted(p.relative_to(inc).as_posix() for p in inc.rglob('*') if p.is_file()) == ['a.h', 'd/e/f.h']
    assert (inc / 'a.h').read_bytes() == b'right'
    assert set(written) == set(files)
    again = compile_headers.stage(inc, files, written)
    assert again == written
    files['a.h'] = b'newer!'
    assert compile_headers.stage(inc, files, written)['a.h'][1] == b'newer!'
    assert (inc / 'a.h').read_bytes() == b'newer!'
