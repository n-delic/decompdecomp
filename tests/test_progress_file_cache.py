import os
from pathlib import Path

import pytest

from decompnet.app.progress import _read
from decompnet.app.progress_file_cache import FileCache
from decompnet.network import NetworkError


def test_cached_read_only_reads_changed_files_and_detects_restored_mtime(tmp_path,monkeypatch):
    (tmp_path/'src').mkdir()
    first=tmp_path/'src/a.c';second=tmp_path/'src/b.c'
    first.write_text('int a;');second.write_text('int b;')
    cache=FileCache()
    original=Path.read_text;reads=[]
    def read(path,*args,**kwargs):
        reads.append(path.name)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    assert _read(tmp_path,cache=cache)=={'src/a.c':'int a;','src/b.c':'int b;'}
    reads.clear()
    before=first.stat()
    first.write_text('int c;');os.utime(first,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert _read(tmp_path,cache=cache)['src/a.c']=='int c;'
    assert reads==['a.c']
    second.unlink()
    assert 'src/b.c' not in _read(tmp_path,cache=cache)
    first.unlink();first.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(NetworkError,match='symbolic link'):_read(tmp_path,cache=cache)


def test_file_cache_memory_is_bounded_and_conflicts_not_cached(tmp_path):
    path=tmp_path/'decompdecomp.json';path.write_text('=======')
    cache=FileCache(limit=10)
    with pytest.raises(NetworkError,match='conflicts'):_read(tmp_path,cache=cache)
    assert not cache.files
    for i in range(10):
        path=tmp_path/str(i);path.write_text('123456')
        cache.put(path,path.stat(),'123456')
    assert cache.bytes<=10 and len(cache.files)==1
