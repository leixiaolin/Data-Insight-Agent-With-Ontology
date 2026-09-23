"""Immutable generations with a single atomic, checksummed commit pointer.

All mutations are serialized by the API configuration lock. Published data is
never inferred from a working generation, including after a process restart.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import OntologyConfig, OntologyManagementConfig as Config
from .management_errors import ManagementError
from .validation import decode_content


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


def check_path(path: Path) -> Path:
    """Reject symlinks and Windows reparse points before resolving any path."""
    path = path.absolute()
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ManagementError('unsafe_path', 'Linked paths are not supported', 422)
    return path.resolve()


class OntologyStore:
    def __init__(self, root: Path | None = None):
        self.root = check_path(root or Config.DIRECTORY)
        seed = check_path(OntologyConfig.DIRECTORY)
        if self.root == seed or seed in self.root.parents:
            raise ManagementError('unsafe_path', 'Management storage must be outside the seed directory')

    def _path(self, relative: str) -> Path:
        path = check_path(self.root / relative)
        if self.root not in path.parents:
            raise ManagementError('unsafe_path', 'Invalid managed path')
        return path

    def _write(self, path: Path, data: bytes):
        check_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise ManagementError('storage_unavailable', 'Unable to persist ontology data', 503) from exc

    def _atomic(self, name: str, value: dict):
        destination = self._path(name)
        temporary = self._path(f'.commit-{uuid.uuid4().hex}')
        try:
            self._write(temporary, encoded(value))
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _quota(self, required: int):
        used = 0
        if self.root.exists():
            for path in self.root.rglob('*'):
                check_path(path)
                if path.is_file():
                    used += path.stat().st_size
        if used + required + 16384 > Config.DISK_BYTES:
            raise ManagementError('disk_quota', 'Managed storage quota exceeded', 413)

    def _read_json(self, relative: str) -> dict:
        try:
            result = json.loads(self._path(relative).read_text(encoding='utf-8'))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (OSError, ValueError) as exc:
            raise ManagementError('storage_corrupt', 'Managed metadata is missing or corrupt', 503) from exc

    def _generation(self, pointer: dict, collection: str) -> dict:
        revision = pointer.get('revision', '')
        if not re.fullmatch(r'[a-f0-9]{32}', revision):
            raise ManagementError('storage_corrupt', 'Invalid generation pointer', 503)
        path = self._path(f'{collection}/{revision}/manifest.json')
        try:
            data = path.read_bytes()
            if digest(data) != pointer['sha256']:
                raise ValueError()
            result = json.loads(data)
            if result['revision'] != revision or not isinstance(result['files'], dict):
                raise ValueError()
            return result
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ManagementError('storage_corrupt', 'Generation checksum verification failed', 503) from exc

    def workspace(self) -> dict:
        return self._generation(self._read_json('workspace-current.json'), 'workspaces')

    def active_pointer(self) -> dict:
        return self._read_json('active.json')

    def active(self) -> dict | None:
        pointer = self.active_pointer().get('current')
        return self._generation(pointer, 'releases') if pointer else None

    def recover(self):
        """Never repair a corrupt workspace by loading it as a publication."""
        pointer = self.active_pointer()
        try:
            if pointer.get('current'):
                self._generation(pointer['current'], 'releases')
        except ManagementError:
            previous = pointer.get('previous')
            if not previous:
                raise
            self._generation(previous, 'releases')
            self._atomic('active.json', {'current': previous, 'previous': None})
        # A damaged workspace must not prevent recovery of a complete release.
        try:
            self.workspace()
        except ManagementError:
            return
        self.collect()

    def _uninitialized(self) -> bool:
        """An existing but empty root is uninitialized; partial files are corruption."""
        return not any((self.root / name).exists() for name in
                       ('workspace-current.json', 'active.json', 'workspaces', 'releases'))

    def initialize(self, validator):
        if self.root.exists() and not self._uninitialized():
            # Existing incomplete/corrupt stores must not silently reimport seeds.
            self.recover()
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._atomic('active.json', {'current': None, 'previous': None})
        files: dict = {}
        migration_error = None
        seed = check_path(OntologyConfig.DIRECTORY)
        try:
            for path in sorted(seed.glob(OntologyConfig.FILE_GLOB)):
                check_path(path)
                if not path.is_file():
                    continue
                if path.stat().st_size > Config.MAX_FILE_BYTES:
                    raise ManagementError('size_limit', 'Seed file exceeds configured limit', 413)
                name = path.relative_to(seed).as_posix()
                if any(item['name'].casefold() == name.casefold() for item in files.values()):
                    raise ManagementError('name_conflict', 'Seed paths collide after normalization')
                file_id = uuid.uuid4().hex
                files[file_id] = self._entry(name, decode_content(path.read_bytes()), True,
                                            name == 'aw_ontology.owl')
            self._limits(files)
            if files:
                report = validator({key: item['content'] for key, item in files.items()}, publication=True)
                if not report['valid']:
                    raise ManagementError('migration_failed', 'Seed validation failed', issues=report)
        except ManagementError as exc:
            migration_error = exc.detail
            files = {}
        workspace = {'revision': uuid.uuid4().hex, 'files': files, 'migration_error': migration_error}
        self._commit_workspace(workspace)
        if files:
            release, pointer = self.prepare_release(workspace)
            self.publish(pointer)

    @staticmethod
    def _entry(name: str, content: str, enabled: bool, protected: bool = False) -> dict:
        return {'name': name, 'content': content, 'enabled': enabled, 'protected': protected,
                'revision': uuid.uuid4().hex, 'modified_at': now(), 'deleted': False, 'backup': None}

    @staticmethod
    def _limits(files: dict):
        live = [item for item in files.values() if not item['deleted']]
        if len(live) > Config.MAX_FILES or sum(len(item['content'].encode('utf-8')) for item in live) > Config.MAX_TOTAL_BYTES:
            raise ManagementError('size_limit', 'Working collection exceeds configured limits', 413)

    def _save_generation(self, collection: str, value: dict) -> dict:
        # Reclaim stale generations before reserving candidate capacity.
        if (self.root / 'workspace-current.json').exists():
            self.collect()
        data = encoded(value)
        self._quota(len(data))
        self._write(self._path(f'{collection}/{value["revision"]}/manifest.json'), data)
        return {'revision': value['revision'], 'sha256': digest(data)}

    def _commit_workspace(self, value: dict):
        self._limits(value['files'])
        pointer = self._save_generation('workspaces', value)
        self._atomic('workspace-current.json', pointer)

    @staticmethod
    def require_revision(actual: str | None, expected: str | None):
        if expected is None:
            raise ManagementError('precondition_required', 'Supply the version you reviewed', 428)
        if (actual or '') != expected:
            raise ManagementError('revision_conflict', 'The reviewed version has changed; refresh before saving', 409,
                                  current_revision=actual)

    @staticmethod
    def filename(name: str):
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.owl', name)
                or '..' in name or name.split('.')[0].upper() in
                {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
            raise ManagementError('invalid_filename', 'Use a safe .owl filename without paths or reserved names')

    def mutate(self, action: str, *, expected_workspace: str | None, file_id: str | None = None,
               expected_file: str | None = None, content: str = '', name: str = '', enabled: bool = False) -> dict:
        workspace = self.workspace()
        self.require_revision(workspace['revision'], expected_workspace)
        files = workspace['files']
        if action == 'upload':
            self.filename(name)
            if any(item['name'].casefold() == name.casefold() for item in files.values()):
                raise ManagementError('name_conflict', 'Name already exists; select its file to overwrite or restore', 409)
            file_id = uuid.uuid4().hex
            files[file_id] = self._entry(name, content, False)
        else:
            if file_id not in files:
                raise ManagementError('not_found', 'Ontology file not found', 404)
            item = files[file_id]
            self.require_revision(item['revision'], expected_file)
            if item['protected'] and action != 'enabled':
                raise ManagementError('protected_file', 'Built-in ontology content is protected', 403)
            if item['deleted'] and action != 'restore':
                raise ManagementError('deleted_file', 'Restore this file before editing it', 409)
            old = {'content': item['content'], 'enabled': item['enabled'], 'created_at': now(),
                   'sha256': digest(item['content'].encode('utf-8'))}
            if action in {'edit', 'delete', 'overwrite'}:
                item['backup'] = old
                # Overwrite replaces content only: enabled state is preserved (FR-3).
                item['content'] = content if action != 'delete' else ''
                item['deleted'] = action == 'delete'
            elif action == 'enabled':
                item['enabled'] = enabled
            elif action == 'restore':
                backup = item['backup']
                if not backup or digest(backup['content'].encode('utf-8')) != backup['sha256']:
                    raise ManagementError('invalid_backup', 'Backup is missing or damaged', 422)
                item.update(content=backup['content'], enabled=backup['enabled'])
                if not item['deleted']:
                    item['backup'] = old
                item['deleted'] = False
            else:
                raise ValueError('Unsupported mutation')
            item.update(revision=uuid.uuid4().hex, modified_at=now())
        workspace['revision'] = uuid.uuid4().hex
        self._commit_workspace(workspace)
        return {'file_id': file_id, 'workspace_revision': workspace['revision'], 'revision': files[file_id]['revision']}

    @staticmethod
    def documents(workspace: dict) -> dict[str, str]:
        return {key: item['content'] for key, item in workspace['files'].items()
                if item['enabled'] and not item['deleted']}

    @staticmethod
    def signature(files: dict) -> str:
        return digest(encoded({key: {'content': item['content'], 'name': item['name']}
                               for key, item in files.items() if item['enabled'] and not item['deleted']}))

    def prepare_release(self, workspace: dict) -> tuple[dict, dict]:
        if not self.documents(workspace):
            raise ManagementError('empty_publication', 'Enable at least one ontology', 409)
        release = {'revision': uuid.uuid4().hex, 'workspace_revision': workspace['revision'],
                   'activated_at': now(), 'files': {key: copy.deepcopy(item) for key, item in workspace['files'].items()
                                                  if item['enabled'] and not item['deleted']}}
        for item in release['files'].values():
            item['backup'] = None
        return release, self._save_generation('releases', release)

    def publish(self, pointer: dict):
        old = self.active_pointer()
        self._atomic('active.json', {'current': pointer, 'previous': old.get('current')})

    def _publication_path(self, token: str) -> Path:
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ManagementError('unsafe_path', 'Invalid publication token')
        return self._path(f'.publication-{token}')

    def stage_publication(self, pointer: dict, token: str) -> None:
        """Persist the pointer off the event loop without making it visible."""
        old = self.active_pointer()
        self._write(self._publication_path(token), encoded(
            {'current': pointer, 'previous': old.get('current')}))

    def commit_publication(self, token: str, deadline: float) -> None:
        """Only the atomic rename remains at the synchronous commit boundary."""
        source, destination = self._publication_path(token), self._path('active.json')
        if time.perf_counter() >= deadline:
            raise ManagementError('timeout', 'Activation exceeded its time budget', 504)
        try:
            os.replace(source, destination)
        except OSError as exc:
            raise ManagementError('storage_unavailable', 'Unable to commit ontology publication', 503) from exc

    def discard_publication(self, token: str) -> None:
        self._publication_path(token).unlink(missing_ok=True)

    def restore_pointer(self, pointer: dict):
        self._atomic('active.json', pointer)

    def listing(self) -> dict:
        workspace, active = self.workspace(), self.active()
        active_files = active['files'] if active else {}
        files = []
        for key, item in workspace['files'].items():
            published = active_files.get(key)
            present = item['enabled'] and not item['deleted']
            change = ('added' if present else None) if not published else (
                'removed' if not present else 'modified' if published['content'] != item['content'] else None)
            files.append({field: item[field] for field in ('name', 'enabled', 'protected', 'revision', 'modified_at', 'deleted')} | {
                'id': key, 'size': len(item['content'].encode('utf-8')), 'has_backup': bool(item['backup']),
                'active': published is not None, 'change': change,
                'draft_only': not present and not item['deleted'] and published is None})
        files.sort(key=lambda entry: entry['name'].casefold())
        return {'files': files, 'workspace_revision': workspace['revision'],
                'active_revision': active['revision'] if active else '',
                'activated_at': active['activated_at'] if active else None,
                'has_changes': self.signature(workspace['files']) != self.signature(active_files),
                'migration_error': workspace.get('migration_error')}

    def read(self, file_id: str, version: str = 'workspace') -> dict:
        collection = self.active() if version == 'active' else self.workspace()
        item = collection['files'].get(file_id) if collection else None
        if item is None or (item['deleted'] and version != 'backup'):
            raise ManagementError('not_found', 'Ontology version not found', 404)
        if version == 'backup':
            if not item['backup']:
                raise ManagementError('not_found', 'No backup exists', 404)
            if digest(item['backup']['content'].encode('utf-8')) != item['backup']['sha256']:
                raise ManagementError('invalid_backup', 'Backup checksum verification failed', 422)
            return {**item['backup'], 'name': item['name'], 'revision': item['revision']}
        return item

    def collect(self):
        """Delete only verified internal generations outside current pointers."""
        workspace = self._read_json('workspace-current.json')
        active = self.active_pointer()
        keep = {workspace['revision'], *[p['revision'] for p in (active.get('current'), active.get('previous')) if p]}
        for collection in ('workspaces', 'releases'):
            directory = self._path(collection)
            if directory.exists():
                for path in directory.iterdir():
                    target = check_path(path)
                    if target.parent != directory or not re.fullmatch('[a-f0-9]{32}', target.name):
                        raise ManagementError('unsafe_path', 'Unexpected managed generation', 503)
                    if target.name not in keep:
                        for child in target.rglob('*'):
                            check_path(child)
                        shutil.rmtree(target)
