"""Migration snapshots must flush on Windows as well as Unix."""
from decompnet.project_migration import durable


def test_durable_snapshot_preserves_bytes(tmp_path):
    snapshot = tmp_path / 'snapshot.db'
    content = b'SQLite snapshot contents\x00\xff'
    snapshot.write_bytes(content)
    durable(snapshot)
    assert snapshot.read_bytes() == content
