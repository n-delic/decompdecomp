from pathlib import Path

import pytest

from decompnet.app import metrics_export


def test_saves_exact_csv_in_downloads_without_overwriting_or_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    csv = '"schema_version","family"\r\n1,"quoted, value"\r\n'
    first = Path(metrics_export.save('../../outside', csv)['path'])
    second = Path(metrics_export.save('../../outside', csv)['path'])
    assert first != second and first.parent == tmp_path / 'Downloads'
    assert first.read_bytes() == csv.encode()
    with pytest.raises(ValueError):
        metrics_export.save('game', 'not CSV')


def test_export_reports_write_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    (tmp_path / 'Downloads').write_text('not a directory')
    with pytest.raises(ValueError, match='Downloads is writable'):
        metrics_export.save('game', '"schema_version",\r\n')
