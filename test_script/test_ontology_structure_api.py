"""HTTP contract tests for the structured view/edit endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config.settings import OntologyConfig, OntologyManagementConfig
from src.api import main

SEED_IRI = 'http://example.org/seed'
SECOND_IRI = 'http://example.org/second'


def owl(iri: str, extra: str = '') -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Ontology rdf:about="{iri}"/>
  <owl:Class rdf:about="{iri}#Customer">
    <rdfs:label xml:lang="en">Customer</rdfs:label>
  </owl:Class>
  {extra}
</rdf:RDF>
"""


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


def seed_entry(client: TestClient) -> dict:
    return next(item for item in client.get('/ontology/files').json()['files']
                if item['name'] == 'aw_ontology.owl')


def upload_editable(client: TestClient) -> str:
    listing = client.get('/ontology/files').json()
    response = client.post(
        '/ontology/files',
        files={'file': ('editable.owl', owl(SECOND_IRI).encode(), 'application/rdf+xml')},
        data={'name': 'editable.owl', 'expected_workspace': listing['workspace_revision']})
    assert response.status_code == 200, response.text
    return response.json()['file_id']


def test_get_structure_returns_model(client: TestClient):
    entry = seed_entry(client)
    response = client.get(f"/ontology/files/{entry['id']}/structure")
    assert response.status_code == 200
    payload = response.json()
    assert payload['model']['counts']['classes'] == 1
    assert payload['model']['entities'][0]['labels'] == [{'lang': 'en', 'text': 'Customer'}]


def test_get_structure_missing_file_404(client: TestClient):
    assert client.get('/ontology/files/missing/structure').status_code == 404


def test_put_structure_edits_label_and_returns_diff(client: TestClient):
    file_id = upload_editable(client)
    model = client.get(f'/ontology/files/{file_id}/structure').json()['model']
    model['entities'][0]['labels'] = [{'lang': 'zh', 'text': '顾客'}]
    listing = client.get('/ontology/files').json()
    entry = next(item for item in listing['files'] if item['id'] == file_id)
    response = client.put(f'/ontology/files/{file_id}/structure', json={
        'model': model, 'expected_workspace': listing['workspace_revision'],
        'expected_file': entry['revision']})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['changed'] is True
    assert payload['diff']['changed'][0]['fields'] == ['labels']
    after = client.get(f'/ontology/files/{file_id}/structure').json()['model']
    assert after['entities'][0]['labels'] == [{'lang': 'zh', 'text': '顾客'}]
    # The raw file content carries the edit.
    content = client.get(f'/ontology/files/{file_id}').json()['content']
    assert '顾客' in content


def test_put_structure_noop_returns_changed_false(client: TestClient):
    file_id = upload_editable(client)
    model = client.get(f'/ontology/files/{file_id}/structure').json()['model']
    listing = client.get('/ontology/files').json()
    response = client.put(f'/ontology/files/{file_id}/structure', json={
        'model': model, 'expected_workspace': listing['workspace_revision'],
        'expected_file': next(f for f in listing['files'] if f['id'] == file_id)['revision']})
    assert response.status_code == 200
    assert response.json()['changed'] is False
    assert client.get('/ontology/files').json()['workspace_revision'] == listing['workspace_revision']


def test_put_structure_version_conflict_409(client: TestClient):
    file_id = upload_editable(client)
    model = client.get(f'/ontology/files/{file_id}/structure').json()['model']
    model['entities'][0]['labels'] = [{'lang': 'en', 'text': 'Shopper'}]
    response = client.put(f'/ontology/files/{file_id}/structure', json={
        'model': model, 'expected_workspace': 'stale', 'expected_file': 'stale'})
    assert response.status_code == 409


def test_put_structure_protected_403(client: TestClient):
    entry = seed_entry(client)
    listing = client.get('/ontology/files').json()
    response = client.put(f"/ontology/files/{entry['id']}/structure", json={
        'model': {'entities': []}, 'expected_workspace': listing['workspace_revision'],
        'expected_file': entry['revision']})
    assert response.status_code == 403
    assert response.json()['detail']['code'] == 'protected_file'


def test_put_structure_invalid_model_422(client: TestClient):
    file_id = upload_editable(client)
    listing = client.get('/ontology/files').json()
    model = client.get(f'/ontology/files/{file_id}/structure').json()['model']
    model['entities'].append({'iri': 'http://example.org/second#X', 'kind': 'mystery'})
    response = client.put(f'/ontology/files/{file_id}/structure', json={
        'model': model, 'expected_workspace': listing['workspace_revision'],
        'expected_file': next(f for f in listing['files'] if f['id'] == file_id)['revision']})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'invalid_structure'


def test_parse_endpoint_and_malformed_content(client: TestClient):
    response = client.post('/ontology/structure/parse', json={'content': owl('http://example.org/x')})
    assert response.status_code == 200
    assert response.json()['model']['counts']['classes'] == 1
    broken = client.post('/ontology/structure/parse', json={'content': 'not xml'})
    assert broken.status_code == 422


def test_serialize_endpoint_roundtrip(client: TestClient):
    content = owl('http://example.org/x')
    parsed = client.post('/ontology/structure/parse', json={'content': content}).json()['model']
    parsed['entities'][0]['comments'] = [{'lang': 'en', 'text': 'A buyer.'}]
    response = client.post('/ontology/structure/serialize', json={'content': content, 'model': parsed})
    assert response.status_code == 200
    payload = response.json()
    assert payload['diff']['changed'][0]['fields'] == ['comments']
    assert 'A buyer.' in payload['content']
    reparsed = client.post('/ontology/structure/parse', json={'content': payload['content']}).json()
    assert reparsed['model']['entities'][0]['comments'] == [{'lang': 'en', 'text': 'A buyer.'}]
