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


def test_over_budget_scan_retains_hits_instead_of_evicting_the_entire_working_set(tmp_path,monkeypatch):
    (tmp_path/'src').mkdir()
    expected={f'src/{name}.c':'123456' for name in ('a','b','c')}
    for name,text in expected.items():(tmp_path/name).write_text(text)
    cache=FileCache(limit=12)
    original=Path.read_text;reads=[]
    def read(path,*args,**kwargs):
        reads.append(path.name)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    assert _read(tmp_path,cache=cache)==expected
    for _ in range(3):
        reads.clear()
        assert _read(tmp_path,cache=cache)==expected
        assert len(reads)==1  # Old LRU policy rereads all three files every pass.
        assert cache.bytes<=12
    # A different project can replace the prior scan's entries; they aren't pinned forever.
    other=tmp_path/'other';(other/'src').mkdir(parents=True)
    (other/'src/new.c').write_text('654321')
    assert _read(other,cache=cache)=={'src/new.c':'654321'}
    reads.clear()
    assert _read(other,cache=cache)=={'src/new.c':'654321'}
    assert reads==[] and cache.bytes<=12


def test_concurrent_scans_keep_project_text_and_budget_separate(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    folders=[]
    for label in ('one','two'):
        folder=tmp_path/label;(folder/'src').mkdir(parents=True)
        for i in range(4):(folder/'src'/f'f{i}.c').write_text(label*3)
        folders.append((folder,{f'src/f{i}.c':label*3 for i in range(4)}))
    cache=FileCache(limit=24)
    def scan(item):
        folder,expected=item
        for _ in range(5):assert _read(folder,cache=cache)==expected
    with ThreadPoolExecutor(2) as pool:list(pool.map(scan,folders))
    assert cache.bytes==sum(len(text.encode()) for text in cache.files.values())<=24
