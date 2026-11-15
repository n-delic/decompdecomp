"""Game copies the window sent are cleared at startup when an import was cut short (app/cleanup.py)."""

from decompnet.app import cleanup


def test_stale_uploads_removed(tmp_path):
    uploads = tmp_path / "uploads"
    (uploads / "d43c1e18").mkdir(parents=True)
    (uploads / "d43c1e18" / "game.iso").write_bytes(b"x" * 10)
    (uploads / "loose.bin").write_bytes(b"y")
    assert len(cleanup.stale_uploads(tmp_path)) == 2
    assert list(uploads.iterdir()) == []
    assert cleanup.stale_uploads(tmp_path / "missing") == []
