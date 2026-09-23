"""Real-ASGI HTTP contract tests for the ontology management API (PRD 6.3).

Each test boots the full application lifespan against temporary seed and
management directories, so the bootstrap/migration path is exercised on every
case. Fault injection monkeypatches router-level collaborators.
"""
from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from src.config.settings import OntologyConfig, OntologyManagementConfig
from src.api import main
from src.api import ontology_management as om
from src.ontology.management_errors import ManagementError

SEED_IRI = 'http://example.org/seed'
SECOND_IRI = 'http://example.org/second'


def owl(iri: str, extra: str = '') -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{iri}"/>
  <owl:Class rdf:about="{iri}#Customer"/>
  {extra}
</rdf:RDF>"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    seed = tmp_path / 'seed'
    seed.mkdir()
    (seed / 'aw_ontology.owl').write_text(owl(SEED_IRI), encoding='utf-8')
    monkeypatch.setattr(OntologyConfig, 'DIRECTORY', seed)
    monkeypatch.setattr(OntologyManagementConfig, 'DIRECTORY', tmp_path / 'mgmt')
    with TestClient(main.app) as active:
        yield active
    main.state.threads.clear()
    main.state.thread_history.clear()
    main.state.active_runs.clear()
    main.state.invalidated_threads.clear()
    main.state.ontology_service = None
    main.state.ontology_management = None
    main.state.master_agent = None
    main.state.ontology_error = None
    main.state.initialized = False
    main.state.init_error = None


def listing(client: TestClient) -> dict:
    return client.get('/ontology/files').json()


def file_entry(client: TestClient, file_id: str) -> dict:
    return next(item for item in listing(client)['files'] if item['id'] == file_id)


def seed_entry(client: TestClient) -> dict:
    return next(item for item in listing(client)['files'] if item['name'] == 'aw_ontology.owl')

def upload(client: TestClient, name: str, content: str | None = None, **form) -> dict:
    response = client.post(
        '/ontology/files',
        files={'file': (name, (content or owl(SECOND_IRI)).encode('utf-8'), 'application/rdf+xml')},
        data=form,
    )
    assert response.status_code == 200, response.text
    return response.json()


def activate(client: TestClient) -> dict:
    current = listing(client)
    response = client.post('/ontology/activate', json={
        'expected_workspace': current['workspace_revision'],
        'expected_active': current['active_revision'],
    })
    assert response.status_code == 200, response.text
    return response.json()


# ─── upload / listing / download ───────────────────────────────────────────────

def test_multipart_upload_lists_draft_and_diff(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    after = listing(client)
    entry = file_entry(client, result['file_id'])
    assert entry['draft_only'] is True and entry['enabled'] is False
    assert after['has_changes'] is False, 'a disabled draft is not a pending publication change'
    assert after['active_revision'] == current['active_revision'], 'upload must not publish'
    assert after['health']['available'] is True
    client.put(f"/ontology/files/{result['file_id']}/enabled", json={
        'enabled': True, 'expected_workspace': after['workspace_revision'],
        'expected_file': entry['revision']})
    assert listing(client)['has_changes'] is True, 'enabling the draft creates a real diff'


def test_upload_failures_leave_committed_state_unchanged(client: TestClient, monkeypatch):
    before = listing(client)['workspace_revision']
    oversize = b'x' * (OntologyManagementConfig.MAX_FILE_BYTES + 1)
    response = client.post('/ontology/files', files={'file': ('big.owl', oversize, 'application/rdf+xml')},
                           data={'name': 'big.owl', 'expected_workspace': before})
    assert response.status_code == 413
    response = client.post('/ontology/files', files={'file': ('bad.owl', b'\xff\xfe', 'application/rdf+xml')},
                           data={'name': 'bad.owl', 'expected_workspace': before})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'invalid_encoding'
    response = client.post('/ontology/files', files={'file': ('bad.owl', b'not xml', 'application/rdf+xml')},
                           data={'name': 'bad.owl', 'expected_workspace': before})
    assert response.status_code == 422
    assert 'issues' in response.json()['detail']
    assert listing(client)['workspace_revision'] == before
    assert all(item['name'] != 'bad.owl' for item in listing(client)['files'])


def test_name_conflict_409_then_explicit_overwrite_keeps_enabled(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    file_id = result['file_id']
    entry = file_entry(client, file_id)
    client.put(f'/ontology/files/{file_id}/enabled', json={
        'enabled': True, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': entry['revision']})
    conflict = client.post('/ontology/files', files={
        'file': ('second.owl', owl('http://example.org/other').encode(), 'application/rdf+xml')},
        data={'name': 'second.owl', 'expected_workspace': listing(client)['workspace_revision']})
    assert conflict.status_code == 409
    assert conflict.json()['detail']['code'] == 'name_conflict'
    entry = file_entry(client, file_id)
    overwritten = client.post('/ontology/files', files={
        'file': ('second.owl', owl('http://example.org/rewritten').encode(), 'application/rdf+xml')},
        data={'name': 'second.owl', 'overwrite': 'true', 'file_id': file_id,
              'expected_workspace': listing(client)['workspace_revision'],
              'expected_file': entry['revision']})
    assert overwritten.status_code == 200, overwritten.text
    assert file_entry(client, file_id)['enabled'] is True, 'overwrite keeps the enabled state'
    assert client.get(f'/ontology/files/{file_id}').json()['content'].count('owl:Class') == 1


def test_download_returns_attachment_and_content(client: TestClient):
    seed_id = seed_entry(client)['id']
    response = client.get(f'/ontology/files/{seed_id}?download=1')
    assert response.status_code == 200
    assert "filename*=UTF-8''aw_ontology.owl" in response.headers['content-disposition']
    assert response.headers['content-type'].startswith('application/rdf+xml')
    assert 'owl:Ontology' in response.text


def test_unknown_file_and_versions_404(client: TestClient):
    assert client.get('/ontology/files/missing').status_code == 404
    seed_id = seed_entry(client)['id']
    assert client.get(f'/ontology/files/{seed_id}?version=active&download=1').status_code == 200


def test_protected_file_edit_403_but_view_and_toggle_allowed(client: TestClient):
    current = listing(client)
    seed_id = seed_entry(client)['id']
    entry = file_entry(client, seed_id)
    assert entry['protected'] is True
    denied = client.put(f'/ontology/files/{seed_id}', json={
        'content': owl('http://example.org/hack'), 'expected_workspace': current['workspace_revision'],
        'expected_file': entry['revision']})
    assert denied.status_code == 403
    assert denied.json()['detail']['code'] == 'protected_file'
    assert client.delete(f'/ontology/files/{seed_id}',
                         headers={'X-Expected-Workspace': current['workspace_revision'],
                                  'X-Expected-File': entry['revision']}).status_code == 403
    overwrite = client.post('/ontology/files', files={
        'file': ('aw_ontology.owl', owl('http://example.org/evil').encode(), 'application/rdf+xml')},
        data={'name': 'AW_ONTOLOGY.owl', 'expected_workspace': current['workspace_revision']})
    assert overwrite.status_code == 409, 'case variants must not bypass the conflict check'
    assert client.get(f'/ontology/files/{seed_id}').status_code == 200
    toggled = client.put(f'/ontology/files/{seed_id}/enabled', json={
        'enabled': False, 'expected_workspace': current['workspace_revision'],
        'expected_file': entry['revision']})
    assert toggled.status_code == 200
    client.put(f'/ontology/files/{seed_id}/enabled', json={
        'enabled': True, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': file_entry(client, seed_id)['revision']})


def test_put_invalid_content_422_with_layered_report(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    entry = file_entry(client, result['file_id'])
    response = client.put(f"/ontology/files/{result['file_id']}", json={
        'content': '<?xml version="1.0"?><oops/>',
        'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': entry['revision']})
    assert response.status_code == 422
    detail = response.json()['detail']
    assert detail['code'] == 'invalid_ontology'
    assert detail['issues']['valid'] is False
    assert any(check['name'] == 'rdf_xml_and_dependencies' for check in detail['issues']['checks'])


def test_delete_uses_header_preconditions(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    file_id = result['file_id']
    missing = client.delete(f'/ontology/files/{file_id}')
    assert missing.status_code == 428
    stale = client.delete(f'/ontology/files/{file_id}', headers={
        'X-Expected-Workspace': 'stale', 'X-Expected-File': 'any'})
    assert stale.status_code == 409
    entry = file_entry(client, file_id)
    ok = client.delete(f'/ontology/files/{file_id}', headers={
        'X-Expected-Workspace': listing(client)['workspace_revision'],
        'X-Expected-File': entry['revision']})
    assert ok.status_code == 200


def test_backup_fetch_and_restore_roundtrip(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    file_id = result['file_id']
    entry = file_entry(client, file_id)
    edited = owl(SECOND_IRI, extra='<owl:Class rdf:about="%s#Extra"/>' % SECOND_IRI)
    saved = client.put(f'/ontology/files/{file_id}', json={
        'content': edited, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': entry['revision']})
    assert saved.status_code == 200
    backup = client.get(f'/ontology/files/{file_id}/backup')
    assert backup.status_code == 200
    assert 'Extra' not in backup.json()['content']
    assert client.get(f'/ontology/files/{file_id}/backup?download=1').status_code == 200
    entry = file_entry(client, file_id)
    restored = client.post(f'/ontology/files/{file_id}/restore', json={
        'expected_workspace': listing(client)['workspace_revision'], 'expected_file': entry['revision']})
    assert restored.status_code == 200
    assert 'Extra' not in client.get(f'/ontology/files/{file_id}').json()['content']
    seed_backup = client.get(f"/ontology/files/{seed_entry(client)['id']}/backup")
    assert seed_backup.status_code == 404, 'protected seed has no mutable-content backup'


def test_deleted_filter_and_restore_keeps_unpublished(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    file_id = result['file_id']
    entry = file_entry(client, file_id)
    client.delete(f'/ontology/files/{file_id}', headers={
        'X-Expected-Workspace': listing(client)['workspace_revision'],
        'X-Expected-File': entry['revision']})
    after_delete = listing(client)
    tombstone = next(item for item in after_delete['files'] if item['id'] == file_id)
    assert tombstone['deleted'] is True and tombstone['has_backup'] is True
    restored = client.post(f'/ontology/files/{file_id}/restore', json={
        'expected_workspace': after_delete['workspace_revision'],
        'expected_file': tombstone['revision']})
    assert restored.status_code == 200
    after = listing(client)
    assert after['active_revision'] == current['active_revision'], 'restore never publishes (FR-8)'


# ─── validation endpoint ───────────────────────────────────────────────────────

def test_validate_file_scope_and_empty_publication(client: TestClient):
    single = client.post('/ontology/validate', json={'content': owl('http://example.org/x'), 'name': 'x.owl'})
    assert single.status_code == 200
    assert single.json()['valid'] is True
    broken = client.post('/ontology/validate', json={'content': 'nope', 'name': 'x.owl'})
    assert broken.status_code == 200 and broken.json()['valid'] is False
    seed = seed_entry(client)
    client.put(f"/ontology/files/{seed['id']}/enabled", json={
        'enabled': False, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': seed['revision']})
    empty = client.post('/ontology/validate', json={'scope': 'publication'})
    assert empty.status_code == 200, 'independent validation returns a report, not an error'
    assert empty.json()['valid'] is False
    assert empty.json()['errors'][0]['code'] == 'empty_publication'


def test_validate_publication_scope_with_substitution(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    client.put(f"/ontology/files/{result['file_id']}/enabled", json={
        'enabled': True, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': file_entry(client, result['file_id'])['revision']})
    substituted = client.post('/ontology/validate', json={
        'scope': 'publication', 'file_id': result['file_id'],
        'content': owl('http://example.org/substituted')})
    assert substituted.status_code == 200
    assert substituted.json()['valid'] is True
    assert any(check['name'] == 'runtime_load' for check in substituted.json()['checks'])


# ─── activation ────────────────────────────────────────────────────────────────

def test_activate_happy_path_resets_sessions_and_reports_revisions(client: TestClient):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    client.put(f"/ontology/files/{result['file_id']}/enabled", json={
        'enabled': True, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': file_entry(client, result['file_id'])['revision']})
    main.state.threads['legacy'] = object()
    main.state.thread_history['legacy'] = []
    outcome = activate(client)
    assert outcome['changed'] is True and outcome['sessions_reset'] is True
    assert outcome['active_revision'] != current['active_revision']
    assert 'legacy' not in main.state.threads
    assert main.state.invalidated_threads.get('legacy') == outcome['active_revision']
    after = listing(client)
    assert after['has_changes'] is False
    assert after['health']['available'] is True
    # A stale tab now receives a typed, consumable SSE error instead of a silent new thread.
    stream = client.post('/chat/stream', json={'message': 'hi', 'thread_id': 'legacy'})
    body = ''.join(stream.iter_text())
    assert 'session_invalidated' in body
    # Retrying with no difference must not clear sessions again.
    main.state.threads['fresh'] = object()
    outcome = activate(client)
    assert outcome['changed'] is False and outcome['sessions_reset'] is False
    assert 'fresh' in main.state.threads


def test_activate_preconditions(client: TestClient):
    current = listing(client)
    missing = client.post('/ontology/activate', json={'expected_workspace': current['workspace_revision']})
    assert missing.status_code == 428
    stale = client.post('/ontology/activate', json={
        'expected_workspace': current['workspace_revision'], 'expected_active': 'stale'})
    assert stale.status_code == 409
    stale_ws = client.post('/ontology/activate', json={
        'expected_workspace': 'stale', 'expected_active': current['active_revision']})
    assert stale_ws.status_code == 409


def test_activate_empty_enabled_set_409(client: TestClient):
    current = listing(client)
    seed = seed_entry(client)
    client.put(f"/ontology/files/{seed['id']}/enabled", json={
        'enabled': False, 'expected_workspace': current['workspace_revision'],
        'expected_file': seed['revision']})
    response = client.post('/ontology/activate', json={
        'expected_workspace': listing(client)['workspace_revision'],
        'expected_active': current['active_revision']})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'empty_publication'
    assert listing(client)['health']['available'] is True, 'the old publication keeps serving'


def test_activate_candidate_agent_failure_preserves_runtime(client: TestClient, monkeypatch):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    client.put(f"/ontology/files/{result['file_id']}/enabled", json={
        'enabled': True, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': file_entry(client, result['file_id'])['revision']})
    master_before = main.state.master_agent
    service_before = main.state.ontology_service
    main.state.threads['keep'] = object()

    def broken(_runtime):
        raise RuntimeError('agent construction exploded')

    monkeypatch.setattr(om, 'build_candidate_agents', broken)
    response = client.post('/ontology/activate', json={
        'expected_workspace': listing(client)['workspace_revision'],
        'expected_active': current['active_revision']})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'activation_failed'
    assert main.state.master_agent is master_before
    assert main.state.ontology_service is service_before
    assert 'keep' in main.state.threads, 'sessions survive a failed activation (AC-3)'
    monkeypatch.undo()

    def unavailable(documents, publication=True, **kwargs):
        raise ManagementError('load_failed', 'Candidate ontology failed strict loading', 422)

    monkeypatch.setattr(om, 'ManagedOntologyRuntime', unavailable)
    response = client.post('/ontology/activate', json={
        'expected_workspace': listing(client)['workspace_revision'],
        'expected_active': current['active_revision']})
    assert response.status_code == 422
    assert main.state.ontology_service is service_before


def test_writes_rejected_while_queries_active(client: TestClient):
    current = listing(client)
    main.state.active_runs['busy'] = main.ActiveRun(run_id='busy', cancel_event=Event())
    try:
        response = client.post('/ontology/files', files={
            'file': ('x.owl', owl('http://example.org/x').encode(), 'application/rdf+xml')},
            data={'name': 'x.owl', 'expected_workspace': current['workspace_revision']})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'active_queries'
        denied = client.post('/ontology/activate', json={
            'expected_workspace': current['workspace_revision'],
            'expected_active': current['active_revision']})
        assert denied.status_code == 409
    finally:
        main.state.active_runs.pop('busy', None)


def test_restart_loads_published_not_workspace(client: TestClient, tmp_path: Path, monkeypatch):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    file_id = result['file_id']
    client.put(f"/ontology/files/{file_id}/enabled", json={
        'enabled': True, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': file_entry(client, file_id)['revision']})
    edited = owl('http://example.org/edited')
    response = client.put(f'/ontology/files/{file_id}', json={
        'content': edited, 'expected_workspace': listing(client)['workspace_revision'],
        'expected_file': file_entry(client, file_id)['revision']})
    assert response.status_code == 200
    with TestClient(main.app) as restarted:
        after = restarted.get('/ontology/files').json()
        assert after['active_revision'] == current['active_revision'], 'restart ignores workspace edits (AC-2)'
        assert after['has_changes'] is True, 'the enabled workspace edit is still pending'
        stored = restarted.get(f'/ontology/files/{file_id}').json()
        assert 'edited' in stored['content'], 'workspace copy keeps the edit'
        assert after['health']['available'] is True


def test_missing_json_preconditions_and_operation_status(client):
    seed = seed_entry(client)
    for method, path, body in (
        ('put', f"/ontology/files/{seed['id']}", {'content': owl(SEED_IRI)}),
        ('put', f"/ontology/files/{seed['id']}/enabled", {'enabled': False}),
        ('post', '/ontology/activate', {}),
    ):
        response = getattr(client, method)(path, json=body)
        assert response.status_code == 428, response.text
        operation = client.get('/ontology/operations/' + response.headers['x-operation-id']).json()
        assert operation['status'] == 'failed'
        assert operation['http_status'] == 428
    current = listing(client)
    operation_id = 'a' * 32
    from src.api.ontology_operations import operations
    operations.pop(operation_id, None)
    response = client.post('/ontology/activate', headers={'X-Operation-Id': operation_id}, json={
        'expected_workspace': current['workspace_revision'], 'expected_active': current['active_revision']})
    assert response.status_code == 200
    operation = client.get('/ontology/operations/' + operation_id).json()
    assert operation['status'] == 'succeeded'
    assert operation['result']['changed'] is False
    assert client.post('/ontology/activate', headers={'X-Operation-Id': operation_id}, json={}).status_code == 409


def test_draft_save_rejects_changed_source(client):
    before = listing(client)
    response = client.post('/ontology/files', files={'file': ('draft.owl', owl(SECOND_IRI).encode())},
                           data={'expected_workspace': before['workspace_revision'], 'source_revision': 'obsolete'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'source_changed'
    assert listing(client)['workspace_revision'] == before['workspace_revision']


def test_install_failure_restores_sessions_and_failed_rollback_blocks_queries(client, monkeypatch):
    current = listing(client)
    result = upload(client, 'second.owl', expected_workspace=current['workspace_revision'])
    client.put(f"/ontology/files/{result['file_id']}/enabled", json={
        'enabled': True, 'expected_workspace': result['workspace_revision'], 'expected_file': result['revision']})
    old_runtime, old_master = main.state.ontology_service, main.state.master_agent
    main.state.threads['keep'] = object()
    old_threads = main.state.threads

    def fail_install(values):
        main.state.__dict__.update(values)
        raise RuntimeError('injected installation failure')

    monkeypatch.setattr(om, '_install_state', fail_install)
    payload = {'expected_workspace': listing(client)['workspace_revision'], 'expected_active': current['active_revision']}
    response = client.post('/ontology/activate', json=payload)
    assert response.status_code == 503
    assert main.state.threads is old_threads and 'keep' in old_threads
    assert main.state.master_agent is old_master
    assert main.state.ontology_service is old_runtime
    assert listing(client)['active_revision'] == current['active_revision']

    def fail_pointer(_pointer):
        raise OSError('injected rollback failure')

    monkeypatch.setattr(main.state.ontology_management, 'restore_pointer', fail_pointer)
    response = client.post('/ontology/activate', json=payload)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'recovery_required'
    assert main.state.initialized is False
    assert 'Agent not initialised' in client.post('/chat/stream', json={'message': 'hi'}).text
    old_runtime.close()


def test_stale_chat_revision_rejected_even_without_tombstone(client):
    response = client.post('/chat/stream', json={
        'message': 'hi', 'thread_id': 'evicted', 'ontology_revision': 'old-release'})
    assert 'session_invalidated' in response.text
    assert 'evicted' not in main.state.threads
