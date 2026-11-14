"""Direct service checks for bounded batch admission and durable checkpoints."""

import hashlib
import json
import time

import pytest

from decompnet.network import NetworkError
from decompnet.reference_batches import ReferenceBatches
from decompnet.reference_metadata import ReferenceMetadata

from .test_reference_metadata import document
from .test_reference_trial_app_http import http_trial, prepare, submit


def setup_batch(tmp_path, monkeypatch):
    client, net, core, body, calls = http_trial(tmp_path, monkeypatch, "rom")
    plan = prepare(client, body)
    assert submit(client, body, plan).json()["outcome"] == "nonmatch"
    service = ReferenceBatches(net)
    preview = service.preview(body["slug"])
    assert len(preview["candidates"]) == 1
    return service, net, body, preview, calls


def admit(service, body, preview, ids=None):
    ids = ids or [candidate["id"] for candidate in preview["candidates"]]
    return service.admit(body["slug"], preview["plan"], ids, True, True)


def test_preview_rejects_bool_and_out_of_bounds_limits(tmp_path, monkeypatch):
    service, _, body, _, _ = setup_batch(tmp_path, monkeypatch)
    for trials, seconds in ((True, 900), (21, 900), (1, False), (1, 3601)):
        with pytest.raises(ValueError):
            service.preview(body["slug"], trials, seconds)


def test_admission_requires_strict_consent_and_exact_subset(tmp_path, monkeypatch):
    service, _, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    ids = [candidate["id"] for candidate in preview["candidates"]]
    with pytest.raises(NetworkError):
        service.admit(body["slug"], preview["plan"], ids, 1, True)
    with pytest.raises((ValueError, NetworkError)):
        service.admit(body["slug"], preview["plan"], ids + ["trial:999999"], True, True)
    with pytest.raises((ValueError, NetworkError)):
        service.admit(body["slug"], preview["plan"], ids + ids, True, True)


def test_admission_is_idempotent_and_rejects_changed_subset_replay(tmp_path, monkeypatch):
    service, _, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    first = admit(service, body, preview)
    replay = admit(service, body, preview)
    assert first["created"] and not replay["created"]
    assert first["batch"]["id"] == replay["batch"]["id"]


def test_manifest_is_bound_to_service_instance_and_project(tmp_path, monkeypatch):
    service, _, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    other_service = ReferenceBatches(service.net)
    with pytest.raises(NetworkError):
        other_service.admit(body["slug"], preview["plan"], [preview["candidates"][0]["id"]], True, True)
    with service.net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,root,config) VALUES('other','.',?)", (json.dumps({"kind": "rom"}),))
    with pytest.raises(NetworkError):
        service.admit("other", preview["plan"], [preview["candidates"][0]["id"]], True, True)


def test_expired_manifest_cannot_be_admitted(tmp_path, monkeypatch):
    service, _, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    manifest = service._decode(preview["plan"])
    manifest["expires"] = int(time.time()) - 1
    expired = service._sign(manifest)
    with pytest.raises(NetworkError, match="expired"):
        service.admit(body["slug"], expired, [preview["candidates"][0]["id"]], True, True)


def test_queued_stop_skips_item_and_has_terminal_counts_without_judging(tmp_path, monkeypatch):
    service, net, body, preview, calls = setup_batch(tmp_path, monkeypatch)
    calls_before = len(calls)
    reservation = admit(service, body, preview)
    stopped = service.stop(body["slug"], reservation["batch"]["id"])
    assert stopped["batch"]["state"] == "stopped"
    assert stopped["batch"]["completed"] == 1
    assert stopped["items"][0]["state"] == "skipped"
    assert len(calls) == calls_before
    assert net.store.one("SELECT 1 FROM claims") is None


def test_deadline_before_first_trial_marks_remaining_skipped(tmp_path, monkeypatch):
    service, net, body, preview, calls = setup_batch(tmp_path, monkeypatch)
    calls_before = len(calls)
    reservation = admit(service, body, preview)
    batch_id = reservation["batch"]["id"]
    with net.store.tx() as db:
        db.execute("UPDATE reference_batches SET created_at=? WHERE id=?", (time.time() - 1000, batch_id))
    detail = service.execute(body["slug"], batch_id, lambda _message: None)
    assert detail["batch"]["state"] == "budget_exhausted"
    assert detail["batch"]["completed"] == 1
    assert detail["items"][0]["state"] == "skipped"
    assert len(calls) == calls_before


def test_cached_miss_identity_ignores_newer_trial_lineage(tmp_path, monkeypatch):
    service, _, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    result = admit(service, body, preview)
    detail = service.execute(body["slug"], result["batch"]["id"], lambda _message: None)
    assert detail["items"][0]["state"] == "nonmatch"
    assert service.preview(body["slug"])["candidates"] == []


def test_changed_target_bytes_invalidate_miss_cache(tmp_path, monkeypatch):
    service, net, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    result = admit(service, body, preview)
    service.execute(body["slug"], result["batch"]["id"], lambda _message: None)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=? WHERE id=?", (b"changed target bytes", body["target_id"]))
    assert service.preview(body["slug"])["candidates"]


def test_source_change_after_approval_is_stale_not_rebased(tmp_path, monkeypatch):
    service, net, body, preview, calls = setup_batch(tmp_path, monkeypatch)
    calls_before = len(calls)
    reservation = admit(service, body, preview)
    with net.store.tx() as db:
        db.execute(
            "UPDATE reference_files SET text=?,content_hash=? WHERE source_id=?",
            ("int trial_fn(void) { return 2; }", "f" * 64, body["origin"]["source_id"]),
        )
    detail = service.execute(body["slug"], reservation["batch"]["id"], lambda _message: None)
    assert detail["items"][0]["state"] == "stale"
    assert len(calls) == calls_before
    assert net.store.one("SELECT 1 FROM claims") is None


def test_one_active_batch_per_project_and_private_history_bound(tmp_path, monkeypatch):
    service, _, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    admit(service, body, preview)
    second = service.preview(body["slug"])
    with pytest.raises(NetworkError, match="already active"):
        admit(service, body, second)
    assert len(service.history(body["slug"])["batches"]) == 1


def test_tampered_signed_manifest_is_rejected(tmp_path, monkeypatch):
    service, _, body, preview, _ = setup_batch(tmp_path, monkeypatch)
    tampered = preview["plan"][:-1] + ("0" if preview["plan"][-1] != "0" else "1")
    with pytest.raises(NetworkError):
        service.admit(body["slug"], tampered, [preview["candidates"][0]["id"]], True, True)


def test_only_current_applied_metadata_is_an_eligible_recipe(tmp_path, monkeypatch):
    client, net, _core, body, calls = http_trial(tmp_path, monkeypatch, 'rom')
    with net.store.tx() as db:
        project_id = db.execute('SELECT project_id FROM functions WHERE id=?', (body['target_id'],)).fetchone()[0]
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                   (project_id, 'trial_fn', 4096, 4, 'function'))
    source = tmp_path / 'symbols.json'
    source.write_text(json.dumps(document(records=[{'id': 'reviewed', 'kind': 'function', 'name': 'mapped_fn',
        'pseudocode': 'int mapped_fn(void) { return 1; }'}])))
    metadata = ReferenceMetadata(net.store, net)
    imported = metadata.import_file(body['slug'], str(source), hashlib.sha256(source.read_bytes()).hexdigest())
    record = metadata.records(body['slug'], imported['import']['id'])['records'][0]
    record_id = record['record_id']
    metadata.map_record(body['slug'], record_id, {'kind': 'function', 'id': body['target_id']})
    service = ReferenceBatches(net)
    assert not service.preview(body['slug'])['candidates']  # pending is not applied review
    metadata.apply(body['slug'], record_id)
    before = list(net.store.conn.iterdump())
    preview = service.preview(body['slug'])
    assert len(preview['candidates']) == 1, preview
    assert preview['candidates'][0]['id'] == f'metadata:{record_id}'
    assert list(net.store.conn.iterdump()) == before and not calls
    with net.store.tx() as db:
        db.execute('UPDATE functions SET size=size+4 WHERE id=?', (body['target_id'],))
    assert not service.preview(body['slug'])['candidates']


def test_restart_reports_interruption_and_never_replays_old_recipe(tmp_path, monkeypatch):
    import decompnet.reference_batches as module
    service, net, body, preview, calls = setup_batch(tmp_path, monkeypatch)
    first = admit(service, body, preview)
    before = len(calls)
    monkeypatch.setattr(module, 'OWNER', 'new-synthetic-app-instance')
    restarted = ReferenceBatches(net)
    assert restarted.history(body['slug'])['batches'][0]['state'] == 'interrupted'
    assert restarted.detail(body['slug'], first['batch']['id'])['batch']['state'] == 'interrupted'
    result = restarted.execute(body['slug'], first['batch']['id'], lambda _line: None)
    assert result['batch']['state'] == 'interrupted' and len(calls) == before
    current = restarted.preview(body['slug'])
    next_batch = admit(restarted, body, current)
    assert next_batch['created'] and next_batch['batch']['id'] != first['batch']['id']
    old = restarted.detail(body['slug'], first['batch']['id'])
    assert old['items'][0]['state'] == 'interrupted' and len(calls) == before
