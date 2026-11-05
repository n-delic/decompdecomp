"""Old compiler drivers must run with their own unquoted internal paths intact."""
import concurrent.futures
import os

import pytest

from decompnet import toolchains


def pack(root, content='original'):
    root.mkdir(parents=True)
    (root / toolchains.MANIFEST).write_text(content)
    (root / 'cc.exe').write_text(content)
    return root


def test_plain_compiler_paths_need_no_runtime_copy(tmp_path, monkeypatch):
    root = pack(tmp_path / 'compiler')
    monkeypatch.setattr(toolchains.tempfile, 'TemporaryDirectory', lambda **kw: pytest.fail('unnecessary copy'))
    assert toolchains._space_free_runtime(root) == root.resolve()


@pytest.mark.skipif(os.name == 'nt', reason='needs POSIX symlinks and private-directory permissions')
def test_whitespace_and_symlinks_materialize_one_private_snapshot_per_install(tmp_path):
    root = pack(tmp_path / 'Application Support' / 'compiler')
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    (root / 'helper.exe').symlink_to(root / 'cc.exe')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        copies = list(pool.map(toolchains._space_free_runtime, [root, alias, root, alias]))
    copied = copies[0]
    assert all(path == copied for path in copies)
    assert not any(c.isspace() for c in str(copied.resolve()))
    assert copied != root and copied.is_dir()
    assert (copied / 'cc.exe').read_text() == 'original'
    assert not (copied / 'helper.exe').is_symlink()
    assert (copied / 'helper.exe').read_text() == 'original'
    assert copied.parent.stat().st_mode & 0o077 == 0
    # A reinstall gets a new snapshot; a compile using the old one remains valid.
    (root / 'cc.exe').write_text('reinstalled')
    manifest = root / toolchains.MANIFEST
    stamp = manifest.stat().st_mtime_ns
    os.utime(manifest, ns=(stamp, stamp + 1_000_000))
    replaced = toolchains._space_free_runtime(root)
    assert replaced != copied and (replaced / 'cc.exe').read_text() == 'reinstalled'
    assert (copied / 'cc.exe').read_text() == 'original'


def test_failed_copy_is_removed_and_can_be_retried(tmp_path, monkeypatch):
    root = pack(tmp_path / 'compiler files')
    original = toolchains.shutil.copytree
    partial = []
    def fail(source, target):
        target.mkdir()
        partial.append(target)
        raise OSError('disk full')
    monkeypatch.setattr(toolchains.shutil, 'copytree', fail)
    with pytest.raises(toolchains.ToolchainError, match='Check free space'):
        toolchains._space_free_runtime(root)
    assert partial and not partial[0].exists()
    monkeypatch.setattr(toolchains.shutil, 'copytree', original)
    assert (toolchains._space_free_runtime(root) / 'cc.exe').read_text() == 'original'


def test_only_marked_native_packs_receive_the_copy(tmp_path, monkeypatch):
    primary, runner = tmp_path / 'compiler', tmp_path / 'runner'
    monkeypatch.setattr(toolchains, 'require', lambda tid, *args, **kw: runner if tid == 'wibo' else primary)
    copied = tmp_path / 'copied'
    seen = []
    monkeypatch.setattr(toolchains, '_space_free_runtime', lambda directory: seen.append(directory) or copied)
    monkeypatch.setattr(toolchains, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(toolchains.wsl, 'wanted', lambda: False)
    for tid in ('ee-gcc-2.95.2', 'ee-gcc-2.95.3'):
        assert toolchains.runtime_dirs(tid) == (copied, runner)
    assert seen == [primary, primary]
    assert toolchains.runtime_dirs('mwcps2-2.3.3') == (primary, runner)
    assert len(seen) == 2
