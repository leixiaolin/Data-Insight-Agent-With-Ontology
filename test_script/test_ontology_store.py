"""Unit tests for the governed ontology generation store (PRD FR-3..FR-8, 6.1/6.2)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.config.settings import OntologyConfig
from src.ontology.management_errors import ManagementError
from src.ontology.store import OntologyStore

OWL = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="http://example.org/{name}"/>
  <owl:Class rdf:about="http://example.org/{name}#Thing"/>
</rdf:RDF>
"""

ALWAYS_VALID = lambda documents, publication=True: {'valid': True}  # noqa: E731


def owl(name: str) -> str:
    return OWL.format(name=name.removesuffix('.owl'))


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> OntologyStore:
    seed = tmp_path / 'seed'
    seed.mkdir()
    monkeypatch.setattr(OntologyConfig, 'DIRECTORY', seed)
    managed = OntologyStore(tmp_path / 'mgmt')
    managed.initialize(ALWAYS_VALID)
    return managed


def _upload(store: OntologyStore, name: str, content: str | None = None) -> dict:
    revision = store.workspace()['revision']
    return store.mutate('upload', expected_workspace=revision, content=content or owl(name), name=name)


def test_upload_requires_workspace_revision(store: OntologyStore):
    with pytest.raises(ManagementError) as error:
        store.mutate('upload', expected_workspace=None, content=owl('a'), name='a.owl')
    assert error.value.status == 428


def test_stale_workspace_revision_reports_current(store: OntologyStore):
    result = _upload(store, 'a.owl')
    with pytest.raises(ManagementError) as error:
        store.mutate('upload', expected_workspace='stale', content=owl('b'), name='b.owl')
    assert error.value.status == 409
    assert error.value.detail['current_revision'] == result['workspace_revision']


def test_stale_file_revision_rejected(store: OntologyStore):
    created = _upload(store, 'a.owl')
    workspace = store.workspace()['revision']
    with pytest.raises(ManagementError) as error:
        store.mutate('edit', expected_workspace=workspace, file_id=created['file_id'],
                     expected_file='stale', content=owl('a2'))
    assert error.value.status == 409


def test_filename_rules_and_reserved_names(store: OntologyStore):
    for name in ('../escape.owl', 'dir/sub.owl', 'CON.owl', 'com1.owl', 'no ext', '.hidden.owl',
                 'spaces in.owl', 'x' * 130 + '.owl'):
        with pytest.raises(ManagementError) as error:
            _upload(store, name)
        assert error.value.detail['code'] == 'invalid_filename', name
    _upload(store, 'fine-name.2.owl')


def test_upload_name_conflict_is_case_insensitive(store: OntologyStore):
    _upload(store, 'Alpha.owl')
    with pytest.raises(ManagementError) as error:
        _upload(store, 'alpha.owl')
    assert error.value.status == 409
    assert error.value.detail['code'] == 'name_conflict'


def test_unknown_file_404(store: OntologyStore):
    revision = store.workspace()['revision']
    with pytest.raises(ManagementError) as error:
        store.mutate('edit', expected_workspace=revision, file_id='missing',
                     expected_file='any', content=owl('x'))
    assert error.value.status == 404


def test_overwrite_keeps_enabled_and_rotates_backup(store: OntologyStore):
    created = _upload(store, 'a.owl')
    listing = store.workspace()['files'][created['file_id']]
    revision = store.workspace()['revision']
    store.mutate('enabled', expected_workspace=revision, file_id=created['file_id'],
                 expected_file=listing['revision'], enabled=True)
    revision = store.workspace()['revision']
    store.mutate('overwrite', expected_workspace=revision, file_id=created['file_id'],
                 expected_file=store.workspace()['files'][created['file_id']]['revision'],
                 content=owl('a2'))
    item = store.workspace()['files'][created['file_id']]
    assert item['enabled'] is True, 'overwrite must preserve the enabled flag (FR-3)'
    assert item['backup']['content'] == owl('a')
    assert '#Thing' in item['content']


def test_edit_rotates_single_slot_backup(store: OntologyStore):
    created = _upload(store, 'a.owl')
    for version in ('a2', 'a3'):
        item = store.workspace()['files'][created['file_id']]
        store.mutate('edit', expected_workspace=store.workspace()['revision'],
                     file_id=created['file_id'], expected_file=item['revision'], content=owl(version))
    item = store.workspace()['files'][created['file_id']]
    assert item['backup']['content'] == owl('a2'), 'only the immediately previous version is kept'


def test_repeat_delete_conflict_preserves_backup(store: OntologyStore):
    created = _upload(store, 'a.owl')
    item = store.workspace()['files'][created['file_id']]
    store.mutate('delete', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=item['revision'])
    tombstone = store.workspace()['files'][created['file_id']]
    with pytest.raises(ManagementError) as error:
        store.mutate('delete', expected_workspace=store.workspace()['revision'],
                     file_id=created['file_id'], expected_file=tombstone['revision'])
    assert error.value.status == 409
    assert store.workspace()['files'][created['file_id']]['backup']['content'] == owl('a')


def test_restore_deleted_keeps_backup(store: OntologyStore):
    created = _upload(store, 'a.owl')
    item = store.workspace()['files'][created['file_id']]
    store.mutate('enabled', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=item['revision'], enabled=True)
    tombstone = store.workspace()['files'][created['file_id']]
    store.mutate('delete', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=tombstone['revision'])
    after_delete = store.workspace()['files'][created['file_id']]
    store.mutate('restore', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=after_delete['revision'])
    restored = store.workspace()['files'][created['file_id']]
    assert restored['deleted'] is False
    assert restored['enabled'] is True, 'restore reinstates the backed-up enabled state'
    assert restored['backup'] is not None, 'restore of a deleted file keeps the backup (FR-8)'


def test_restore_existing_swaps_backup(store: OntologyStore):
    created = _upload(store, 'a.owl')
    item = store.workspace()['files'][created['file_id']]
    store.mutate('edit', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=item['revision'], content=owl('a2'))
    edited = store.workspace()['files'][created['file_id']]
    store.mutate('restore', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=edited['revision'])
    restored = store.workspace()['files'][created['file_id']]
    assert restored['content'] == owl('a')
    assert restored['backup']['content'] == owl('a2'), 'pre-restore content becomes the new backup'


def test_restore_corrupt_backup_rejected(store: OntologyStore):
    created = _upload(store, 'a.owl')
    item = store.workspace()['files'][created['file_id']]
    store.mutate('edit', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=item['revision'], content=owl('a2'))
    store_root = store.root
    pointer = json.loads((store_root / 'workspace-current.json').read_text())
    manifest = store_root / 'workspaces' / pointer['revision'] / 'manifest.json'
    data = json.loads(manifest.read_text())
    data['files'][created['file_id']]['backup']['sha256'] = '0' * 64
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')
    manifest.write_bytes(payload)
    pointer['sha256'] = hashlib.sha256(payload).hexdigest()
    (store_root / 'workspace-current.json').write_text(json.dumps(pointer))
    corrupted = store.workspace()['files'][created['file_id']]
    with pytest.raises(ManagementError) as error:
        store.mutate('restore', expected_workspace=store.workspace()['revision'],
                     file_id=created['file_id'], expected_file=corrupted['revision'])
    assert error.value.status == 422


def test_enabled_toggle_does_not_touch_backup(store: OntologyStore):
    created = _upload(store, 'a.owl')
    item = store.workspace()['files'][created['file_id']]
    store.mutate('enabled', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=item['revision'], enabled=True)
    assert store.workspace()['files'][created['file_id']]['backup'] is None


def test_transaction_failure_leaves_pointer_unchanged(store: OntologyStore, monkeypatch):
    before = store.workspace()['revision']
    original = OntologyStore._write
    calls = {'n': 0}

    def flaky(self, path, data):
        calls['n'] += 1
        if calls['n'] >= 2:
            raise OSError('disk full')
        return original(self, path, data)

    monkeypatch.setattr(OntologyStore, '_write', flaky)
    with pytest.raises(OSError):
        _upload(store, 'a.owl')
    monkeypatch.undo()
    assert store.workspace()['revision'] == before
    assert not any(item['name'] == 'a.owl' for item in store.workspace()['files'].values())


# ─── initialize / recovery ─────────────────────────────────────────────────────

def test_initialize_imports_seeds_and_publishes(tmp_path: Path, monkeypatch):
    seed = tmp_path / 'seed'
    (seed / 'sub').mkdir(parents=True)
    (seed / 'aw_ontology.owl').write_text(owl('seed'), encoding='utf-8')
    (seed / 'sub' / 'extra.owl').write_text(owl('extra'), encoding='utf-8')
    monkeypatch.setattr(OntologyConfig, 'DIRECTORY', seed)
    store = OntologyStore(tmp_path / 'mgmt')
    store.initialize(ALWAYS_VALID)
    assert store.active() is not None
    names = {item['name'] for item in store.workspace()['files'].values()}
    assert names == {'aw_ontology.owl', 'sub/extra.owl'}
    assert store.workspace()['files'][
        next(k for k, v in store.workspace()['files'].items() if v['name'] == 'aw_ontology.owl')
    ]['protected'] is True


def test_initialize_existing_but_empty_root_is_fresh(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(OntologyConfig, 'DIRECTORY', tmp_path / 'seed')
    (tmp_path / 'seed').mkdir()
    root = tmp_path / 'mgmt'
    root.mkdir()  # exists but empty: must be treated as uninitialized, not corrupt
    store = OntologyStore(root)
    store.initialize(ALWAYS_VALID)
    assert store.workspace()['migration_error'] is None


def test_initialize_validation_failure_is_metadata_only(tmp_path: Path, monkeypatch):
    (tmp_path / 'seed').mkdir()
    (tmp_path / 'seed' / 'a.owl').write_text(owl('a'), encoding='utf-8')
    monkeypatch.setattr(OntologyConfig, 'DIRECTORY', tmp_path / 'seed')
    store = OntologyStore(tmp_path / 'mgmt')
    store.initialize(lambda documents, publication=True: {'valid': False, 'errors': [{'code': 'x'}]})
    assert store.active() is None
    assert store.workspace()['migration_error'] is not None
    assert store.workspace()['files'] == {}


def test_recover_falls_back_to_previous_release(tmp_path: Path, monkeypatch):
    (tmp_path / 'seed').mkdir()
    (tmp_path / 'seed' / 'a.owl').write_text(owl('a'), encoding='utf-8')
    monkeypatch.setattr(OntologyConfig, 'DIRECTORY', tmp_path / 'seed')
    store = OntologyStore(tmp_path / 'mgmt')
    store.initialize(ALWAYS_VALID)
    first = store.active()['revision']
    created = _upload(store, 'b.owl')
    store.mutate('enabled', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=store.workspace()['files'][created['file_id']]['revision'],
                 enabled=True)
    release, pointer = store.prepare_release(store.workspace())
    store.publish(pointer)
    second = store.active()['revision']
    # Corrupt the current release; recovery must fall back to the previous one.
    (store.root / 'releases' / second / 'manifest.json').write_text('{}')
    store.recover()
    assert store.active()['revision'] == first


def test_collect_keeps_current_and_previous(tmp_path: Path, monkeypatch):
    (tmp_path / 'seed').mkdir()
    (tmp_path / 'seed' / 'a.owl').write_text(owl('a'), encoding='utf-8')
    monkeypatch.setattr(OntologyConfig, 'DIRECTORY', tmp_path / 'seed')
    store = OntologyStore(tmp_path / 'mgmt')
    store.initialize(ALWAYS_VALID)
    _upload(store, 'b.owl')
    release, pointer = store.prepare_release(store.workspace())
    store.publish(pointer)
    store.collect()
    kept = {p.name for p in (store.root / 'workspaces').iterdir()} | {
        p.name for p in (store.root / 'releases').iterdir()}
    expected = {store.workspace()['revision'], store.active()['revision'],
                store.active_pointer()['previous']['revision']}
    assert kept == expected


def test_corrupt_workspace_pointer_is_503(store: OntologyStore):
    (store.root / 'workspace-current.json').write_text('not json')
    with pytest.raises(ManagementError) as error:
        store.workspace()
    assert error.value.status == 503


def test_read_versions_and_backup(store: OntologyStore):
    created = _upload(store, 'a.owl')
    store.mutate('enabled', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=store.workspace()['files'][created['file_id']]['revision'],
                 enabled=True)
    release, pointer = store.prepare_release(store.workspace())
    store.publish(pointer)
    item = store.workspace()['files'][created['file_id']]
    store.mutate('edit', expected_workspace=store.workspace()['revision'],
                 file_id=created['file_id'], expected_file=item['revision'], content=owl('a2'))
    assert store.read(created['file_id'])['content'] == owl('a2')
    assert store.read(created['file_id'], 'active')['content'] == owl('a')
    assert store.read(created['file_id'], 'backup')['content'] == owl('a')
    with pytest.raises(ManagementError):
        store.read('missing', 'active')
