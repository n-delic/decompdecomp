from decompnet.app import frontend_reload


def test_frontend_snapshot_tracks_css_separately_and_added_removed_assets(tmp_path):
    css = tmp_path / 'app.css'
    script = tmp_path / 'app.js'
    css.write_text('body{}')
    script.write_text('first()')
    first = frontend_reload.snapshot(tmp_path)
    css.write_text('body{color:red}')
    changed = frontend_reload.snapshot(tmp_path)
    assert first['css'] != changed['css'] and first['page'] == changed['page']
    script.write_text('second()')
    next_version = frontend_reload.snapshot(tmp_path)
    assert next_version['css'] == changed['css'] and next_version['page'] != changed['page']
    script.unlink()
    assert frontend_reload.snapshot(tmp_path)['page'] != next_version['page']
    (tmp_path / 'new.html').write_text('<main/>')
    assert frontend_reload.snapshot(tmp_path)['page'] != next_version['page']


def test_live_reload_only_in_checkout_and_can_be_disabled(monkeypatch):
    monkeypatch.setattr(frontend_reload.edition, 'DEV', True)
    monkeypatch.delenv('DECOMP_LIVE_RELOAD', raising=False)
    assert frontend_reload.enabled()
    monkeypatch.setenv('DECOMP_LIVE_RELOAD', '0')
    assert not frontend_reload.enabled()
    monkeypatch.setenv('DECOMP_LIVE_RELOAD', '1')
    monkeypatch.setattr(frontend_reload.edition, 'DEV', False)
    assert not frontend_reload.enabled()
