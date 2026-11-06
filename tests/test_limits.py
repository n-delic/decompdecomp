"""Limits on the tools decompdecomp runs (limits.py)."""

import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="no core dumps on Windows")
def test_the_tools_the_app_starts_dump_no_core():
    """A compiler aborting under wibo put up the desktop's crash dialog: what the app starts inherits no core dumps."""
    code = "from decompnet import limits; import subprocess; limits.no_core_dumps(); " \
           "print(subprocess.run(['sh', '-c', 'ulimit -c'], capture_output=True, text=True).stdout.strip())"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "0"


def test_open_file_limit_is_raised_without_changing_hard_limit(monkeypatch):
    resource=pytest.importorskip('resource')
    from decompnet import limits
    current=[256,65536];seen=[]
    monkeypatch.setattr(resource,'getrlimit',lambda kind:tuple(current))
    def setlimit(kind,value):seen.append(value);current[:]=value
    monkeypatch.setattr(resource,'setrlimit',setlimit)
    assert limits.raise_open_file_limit()==8192
    assert seen==[(8192,65536)]
    assert limits.raise_open_file_limit()==8192 and len(seen)==1
    current[:]=[256,1024]
    assert limits.raise_open_file_limit()==1024 and seen[-1]==(1024,1024)


def test_open_file_limit_failure_is_nonfatal_and_high_limits_are_preserved(monkeypatch):
    resource=pytest.importorskip('resource')
    from decompnet import limits
    monkeypatch.setattr(resource,'getrlimit',lambda kind:(256,resource.RLIM_INFINITY))
    def denied(*args):raise OSError('not permitted')
    monkeypatch.setattr(resource,'setrlimit',denied)
    assert limits.raise_open_file_limit() is None
    monkeypatch.setattr(resource,'getrlimit',lambda kind:(65536,65536))
    assert limits.raise_open_file_limit()==65536


@pytest.mark.skipif(sys.platform == 'win32',reason='POSIX descriptor limits')
def test_raised_limit_is_inherited_by_child_tools():
    code='''import resource,subprocess,sys
from decompnet import limits
soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
if hard!=resource.RLIM_INFINITY and hard<8192:sys.exit(77)
resource.setrlimit(resource.RLIMIT_NOFILE,(256,hard))
assert limits.raise_open_file_limit()==8192
print(subprocess.check_output([sys.executable,'-c','import resource;print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])'],text=True).strip())
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    if result.returncode==77:pytest.skip('host hard limit below 8192')
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='8192'
