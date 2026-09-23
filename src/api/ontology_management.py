"""Online ontology management endpoints (PRD P0) and the activation coordinator.

The router is attached to the shared application state via ``attach()`` so the
module never imports ``src.api.main`` (which imports this module). All writes
share the application configuration lock and reject active queries; publication
always goes through the immutable-generation store and a candidate runtime.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from functools import partial
from collections import OrderedDict
from typing import Any, Optional

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import Literal
from urllib.parse import quote

from src.agents import DataInsightAgent, MasterAgent, MetadataAgent, OntologyAgent
from src.config.settings import OntologyManagementConfig as Config
from src.ontology.management_errors import ManagementError
from src.ontology.managed_runtime import ManagedOntologyRuntime, validate_documents, create_runtime, validate_async
from src.ontology.generator import generate_draft, list_generator_tables, generator_source_revision
from src.ontology.store import OntologyStore
from src.ontology.structure import parse_structure, patch_structure
from src.ontology.validation import decode_content, inspect_documents
from src.utils import get_logger
from .ontology_operations import OntologyOperationMiddleware, current_operation, operations, stage

logger = get_logger(__name__)

router = APIRouter(prefix="/ontology", tags=["ontology-management"])

_state: Any = None
_lock: Optional[asyncio.Lock] = None

_INVALIDATED_THREAD_LIMIT = 1000
_HEALTH_KEYS = ('available', 'reasoner_enabled', 'reasoner', 'reasoning_status',
                'file_count', 'ontology_count', 'entity_count')


def attach(state: Any, configuration_lock: asyncio.Lock) -> None:
    """Bind the shared application state and configuration lock."""
    global _state, _lock
    _state = state
    _lock = configuration_lock


def register_management_error_handler(app) -> None:
    """Map ManagementError onto the PRD detail={code,message,...} HTTP shape."""

    app.add_middleware(OntologyOperationMiddleware)

    @app.exception_handler(ManagementError)
    async def _handler(_request, exc: ManagementError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


def build_candidate_agents(runtime: Any) -> MasterAgent:
    """Build a complete candidate agent stack without global side effects."""
    metadata_agent = MetadataAgent()
    ontology_agent = OntologyAgent(runtime)
    data_insight_agent = DataInsightAgent(
        metadata_agent=metadata_agent,
        ontology_agent=ontology_agent,
    )
    return MasterAgent(
        data_insight_agent=data_insight_agent,
        metadata_agent=metadata_agent,
        ontology_agent=ontology_agent,
    )


def bootstrap_ontology_management() -> tuple[OntologyStore, Any, Optional[OntologyAgent], Optional[str]]:
    """Initialize governed storage and the published ontology runtime.

    Returns (store, runtime_or_None, agent_or_None, error_summary). Never raises:
    failures degrade to a metadata-only startup with a visible error, and the
    startup path only ever trusts the published pointer (PRD 6.1).
    """
    try:
        store = OntologyStore()
    except ManagementError as exc:
        logger.error("Ontology management storage unavailable: %s", exc.detail['message'])
        return _stub_store(), None, None, exc.detail['message']
    try:
        store.initialize(validate_documents)
    except ManagementError as exc:
        logger.error("Ontology management storage recovery failed: %s", exc.detail['message'])
        return store, None, None, exc.detail['message']
    try:
        workspace = store.workspace()
    except ManagementError:
        workspace = {}
    active = store.active()
    if workspace.get('migration_error') and active is None:
        summary = str(workspace['migration_error'].get('message') or 'Seed migration failed')
        logger.error("Ontology seed migration failed: %s", summary)
        return store, None, None, summary
    if active is None:
        return store, None, None, 'No published ontology available yet'
    runtime = None
    try:
        runtime = ManagedOntologyRuntime(store.documents(active), publication=True)
        agent = OntologyAgent(runtime)
    except Exception:
        if runtime is not None:
            runtime.close()
        return store, None, None, 'Published ontology runtime could not be initialized'
    return store, runtime, agent, runtime.reasoning_error


def _stub_store() -> OntologyStore:
    """A non-functional holder so management endpoints report 503, not crash."""
    class _UnavailableStore:
        def __getattr__(self, name):
            def _unavailable(*_args, **_kwargs):
                raise ManagementError('storage_unavailable', 'Ontology management storage is unavailable', 503)
            return _unavailable
    return _UnavailableStore()  # type: ignore[return-value]


def _store() -> OntologyStore:
    store = getattr(_state, 'ontology_management', None) if _state is not None else None
    if store is None:
        raise ManagementError('storage_unavailable', 'Ontology management is not initialized', 503)
    return store


def _require_idle() -> None:
    if _state is None:
        raise ManagementError('storage_unavailable', 'Ontology management is not initialized', 503)
    if any(run.task is None or not run.task.done() for run in _state.active_runs.values()):
        raise ManagementError('active_queries', 'Wait for active queries to finish before changing ontologies', 409)


def _safe_health() -> dict:
    """Public runtime health without local paths or raw parser output."""
    service = getattr(_state, 'ontology_service', None) if _state is not None else None
    if service is None:
        return {'available': False, 'reasoner_enabled': False, 'reasoner': 'unavailable',
                'reasoning_status': 'unavailable',
                'reasoning_error': getattr(_state, 'ontology_error', None) if _state is not None else None,
                'file_count': 0, 'ontology_count': 0, 'entity_count': 0}
    raw = service.health()
    raw_error = str(raw.get('reasoning_error') or '')
    first_line = next(
        (line.strip() for line in raw_error.splitlines()
         if line.strip() and line.strip() != 'Java error message is:' and not line.lstrip().startswith('at ')),
        None,
    )
    return {**{key: raw.get(key) for key in _HEALTH_KEYS if key != 'reasoning_error'},
            'reasoning_error': first_line[:500] if first_line else None}


async def _file_report(content: str, name: str) -> dict:
    """Parse in a terminable worker, never on the ASGI event loop."""
    stage('validating')
    decode_content(content.encode('utf-8'))
    return await validate_async({name: content}, publication=False)


def _require_valid(report: dict) -> None:
    if not report['valid']:
        raise ManagementError('invalid_ontology', 'Ontology validation failed', 422, issues=report)


async def _prepare_in_thread(function, *args):
    """Drain uncommitted work before releasing the configuration lock on cancellation.

    The callable must never publish a pointer or install runtime state. A cancelled
    caller waits for ownership to return, then discards the candidate; no abandoned
    thread can later commit or race the next configuration change.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # retrieve an error even if cancellation wins the race
        raise


def _remaining_budget(deadline: float) -> float:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise ManagementError('timeout', 'Activation exceeded its time budget', 504)
    return remaining


def _download(content: str, name: str) -> Response:
    return Response(
        content.encode('utf-8'),
        media_type='application/rdf+xml',
        headers={'Content-Disposition': f"attachment; filename=ontology.owl; filename*=UTF-8''{quote(name, safe='')}"},
    )


# ─── Pydantic request models ───────────────────────────────────────────────────

class FileSaveBody(BaseModel):
    content: str
    expected_workspace: Optional[str] = None
    expected_file: Optional[str] = None


class EnabledBody(BaseModel):
    enabled: bool
    expected_workspace: Optional[str] = None
    expected_file: Optional[str] = None


class RestoreBody(BaseModel):
    expected_workspace: Optional[str] = None
    expected_file: Optional[str] = None


class ActivateBody(BaseModel):
    expected_workspace: Optional[str] = None
    expected_active: Optional[str] = None


class ValidateBody(BaseModel):
    content: Optional[str] = None
    name: Optional[str] = None
    file_id: Optional[str] = None
    scope: Literal['file', 'publication'] = 'file'


class GenerateDraftBody(BaseModel):
    tables: list[str]
    namespace: Optional[str] = None
    source_revision: Optional[str] = None


class StructureSaveBody(BaseModel):
    model: dict
    expected_workspace: Optional[str] = None
    expected_file: Optional[str] = None


class StructureParseBody(BaseModel):
    content: str
    name: Optional[str] = None


class StructureSerializeBody(BaseModel):
    content: str
    model: dict


# ─── P0 endpoints ──────────────────────────────────────────────────────────────

@router.get('/operations/{operation_id}')
async def operation_status(operation_id: str):
    if operation_id not in operations:
        raise ManagementError('not_found', 'Operation not found or expired; check published revision', 404)
    return dict(operations[operation_id])

@router.get('/files')
async def list_files():
    listing = _store().listing()
    return {**listing, 'health': _safe_health()}


@router.post('/files')
async def upload_file(
    file: UploadFile = File(...),
    name: str = Form(''),
    overwrite: bool = Form(False),
    file_id: Optional[str] = Form(None),
    expected_workspace: Optional[str] = Form(None),
    expected_file: Optional[str] = Form(None),
    source_revision: Optional[str] = Form(None),
):
    # Byte limit first, then strict UTF-8, then syntax — nothing is committed on failure.
    raw = await file.read(Config.MAX_FILE_BYTES + 1)
    if len(raw) > Config.MAX_FILE_BYTES:
        raise ManagementError('size_limit', 'Ontology file exceeds configured limit', 413)
    content = decode_content(raw)
    chosen = (name or file.filename or '').strip()
    report = await _file_report(content, chosen or 'upload.owl')
    _require_valid(report)
    async with _lock:
        _require_idle()
        if source_revision is not None and source_revision != generator_source_revision():
            raise ManagementError('source_changed', 'The data source changed; regenerate the draft', 409)
        if overwrite:
            if not file_id:
                raise ManagementError('precondition_required', 'Overwrite requires the target file', 428)
            result = _store().mutate('overwrite', expected_workspace=expected_workspace,
                                     file_id=file_id, expected_file=expected_file, content=content)
        else:
            result = _store().mutate('upload', expected_workspace=expected_workspace,
                                     content=content, name=chosen)
    logger.info("Ontology upload committed: file=%s overwrite=%s bytes=%d",
                result['file_id'], overwrite, len(raw))
    return {'operation_id': current_operation.get() or uuid.uuid4().hex, **result, 'report': report}


@router.get('/files/{file_id}')
async def get_file(file_id: str, version: Literal['workspace', 'active'] = 'workspace', download: bool = False):
    item = _store().read(file_id, version)
    if download:
        return _download(item['content'], item['name'])
    return {'id': file_id, 'version': version, 'name': item['name'], 'content': item['content'],
            'revision': item['revision'], 'enabled': item['enabled'], 'protected': item['protected'],
            'modified_at': item['modified_at'], 'size': len(item['content'].encode('utf-8'))}


@router.put('/files/{file_id}')
async def save_file(file_id: str, body: FileSaveBody):
    item = _store().read(file_id)  # 404 for missing or deleted files
    report = await _file_report(body.content, item['name'])
    _require_valid(report)
    async with _lock:
        _require_idle()
        result = _store().mutate('edit', expected_workspace=body.expected_workspace,
                                 file_id=file_id, expected_file=body.expected_file, content=body.content)
    logger.info("Ontology edit committed: file=%s bytes=%d", file_id, len(body.content))
    return {'operation_id': current_operation.get() or uuid.uuid4().hex, **result, 'report': report}


@router.delete('/files/{file_id}')
async def delete_file(
    file_id: str,
    x_expected_workspace: Optional[str] = Header(None),
    x_expected_file: Optional[str] = Header(None),
):
    # DELETE carries its preconditions in headers, never a request body.
    async with _lock:
        _require_idle()
        result = _store().mutate('delete', expected_workspace=x_expected_workspace,
                                 file_id=file_id, expected_file=x_expected_file)
    logger.info("Ontology delete committed: file=%s", file_id)
    return {'operation_id': current_operation.get() or uuid.uuid4().hex, **result}


@router.post('/validate')
async def validate(body: ValidateBody):
    store = _store()
    if body.scope == 'publication':
        workspace = store.workspace()
        documents = store.documents(workspace)
        if body.content is not None:
            if body.file_id:
                target = workspace['files'].get(body.file_id)
                if target is None:
                    raise ManagementError('not_found', 'Ontology file not found', 404)
                if target['deleted'] or not target['enabled']:
                    raise ManagementError('deleted_file' if target['deleted'] else 'disabled_file',
                                          'Only enabled workspace files can be substituted', 409)
                documents[body.file_id] = body.content
            else:
                extra = body.name or 'draft.owl'
                match = next((key for key, item in workspace['files'].items()
                              if not item['deleted'] and item['name'].casefold() == extra.casefold()), None)
                if match is not None:
                    documents[match] = body.content
                else:
                    documents[f'new:{extra}'] = body.content
        if not documents:
            # Independent validation reports the problem instead of failing the request.
            return {'scope': 'publication', 'valid': False, 'ontology_iri': None,
                    'class_count': 0, 'property_count': 0, 'individual_count': 0,
                    'checks': [], 'errors': [{'code': 'empty_publication', 'message':
                                              'Enable at least one ontology before activation',
                                              'file': '', 'severity': 'error'}], 'warnings': []}
        return await validate_async(documents, publication=True)

    if body.content is None and body.file_id is None:
        raise ManagementError('invalid_request', 'Provide content or file_id to validate', 422)
    if body.content is not None:
        name = body.name or 'draft.owl'
        return await _file_report(body.content, name)
    item = store.read(body.file_id)
    return await _file_report(item['content'], item['name'])


@router.put('/files/{file_id}/enabled')
async def set_enabled(file_id: str, body: EnabledBody):
    async with _lock:
        _require_idle()
        result = _store().mutate('enabled', expected_workspace=body.expected_workspace,
                                 file_id=file_id, expected_file=body.expected_file, enabled=body.enabled)
    logger.info("Ontology enabled=%s committed: file=%s", body.enabled, file_id)
    return {'operation_id': current_operation.get() or uuid.uuid4().hex, **result}


@router.get('/files/{file_id}/backup')
async def get_backup(file_id: str, download: bool = False):
    item = _store().read(file_id, 'backup')
    if download:
        return _download(item['content'], item['name'])
    return {'id': file_id, 'name': item['name'], 'content': item['content'],
            'enabled': item['enabled'], 'created_at': item['created_at'],
            'sha256': item['sha256'], 'file_revision': item['revision']}


@router.post('/files/{file_id}/restore')
async def restore_file(file_id: str, body: RestoreBody):
    backup = _store().read(file_id, 'backup')  # 404 when no backup exists
    report = await _file_report(backup['content'], backup['name'])
    _require_valid(report)
    async with _lock:
        _require_idle()
        result = _store().mutate('restore', expected_workspace=body.expected_workspace,
                                 file_id=file_id, expected_file=body.expected_file)
    logger.info("Ontology restore committed: file=%s", file_id)
    return {'operation_id': current_operation.get() or uuid.uuid4().hex, **result}


@router.post('/activate')
async def activate(body: ActivateBody):
    """Explicit hot activation per PRD FR-9 and the 6.2 failure matrix."""
    operation = current_operation.get() or uuid.uuid4().hex
    started = time.perf_counter()
    deadline = started + Config.TIMEOUT_SECONDS
    warnings: list[str] = []
    store = _store()

    async with _lock:
        _require_idle()
        workspace = store.workspace()
        OntologyStore.require_revision(workspace['revision'], body.expected_workspace)
        active = store.active()
        current_active = active['revision'] if active else ''
        if body.expected_active is None:
            raise ManagementError('precondition_required', 'Supply the published version you reviewed',
                                  428, current_revision=current_active)
        if body.expected_active != current_active:
            raise ManagementError('revision_conflict', 'The published version has changed; refresh and review again',
                                  409, current_revision=current_active)

        # No content/enablement difference: nothing to rebuild, sessions stay.
        if active is not None and store.signature(workspace['files']) == store.signature(active['files']):
            return {'active_revision': current_active, 'workspace_revision': workspace['revision'],
                    'changed': False, 'sessions_reset': False, 'warnings': [],
                    'operation_id': operation,
                    'elapsed_seconds': round(time.perf_counter() - started, 3)}

        documents = store.documents(workspace)
        if not documents:
            raise ManagementError('empty_publication', 'Enable at least one ontology before activation', 409)

        candidate_runtime: Optional[ManagedOntologyRuntime] = None
        candidate_master = None
        old_pointer = store.active_pointer()
        state_fields = ('ontology_service', 'master_agent', 'ontology_error', 'invalidated_threads',
                        'threads', 'thread_history', 'active_runs', 'initialized', 'init_error')
        previous_state = {name: getattr(_state, name) for name in state_fields}
        committed = False
        try:
            stage('validating_and_loading')
            build_started = time.perf_counter()
            candidate_runtime = await create_runtime(documents, factory=partial(
                ManagedOntologyRuntime, timeout=_remaining_budget(deadline)))
            stage('building_agents')
            _remaining_budget(deadline)
            candidate_master = await _prepare_in_thread(build_candidate_agents, candidate_runtime)
            _remaining_budget(deadline)
            logger.info("Activation %s: candidates ready in %.2fs (%d files, %d bytes)",
                        operation, time.perf_counter() - build_started,
                        len(documents), sum(len(value.encode('utf-8')) for value in documents.values()))
            stage('persisting_release')
            release, pointer = await _prepare_in_thread(store.prepare_release, workspace)
            _remaining_budget(deadline)
            invalidated = OrderedDict(_state.invalidated_threads)
            invalidated.update({thread_id: release['revision'] for thread_id in _state.threads})
            while len(invalidated) > _INVALIDATED_THREAD_LIMIT:
                invalidated.popitem(last=False)
            next_state = dict(ontology_service=candidate_runtime, master_agent=candidate_master,
                              ontology_error=None, invalidated_threads=invalidated,
                              threads={}, thread_history={}, active_runs={}, initialized=True, init_error=None)
            stage('preparing_commit')
            await _prepare_in_thread(store.stage_publication, pointer, operation)
            # No await between the deadline-checked pointer rename and installation.
            store.commit_publication(operation, deadline)
            committed = True
        except (ManagementError, asyncio.CancelledError):
            if candidate_runtime is not None:
                await _prepare_in_thread(candidate_runtime.close)
            raise
        except Exception as exc:
            if candidate_runtime is not None:
                await _prepare_in_thread(candidate_runtime.close)
            raise ManagementError('activation_failed',
                                  f'Candidate activation failed: {type(exc).__name__}', 422) from exc
        finally:
            # A cancelled preparation is fully drained before deleting its staging file.
            try:
                store.discard_publication(operation)
            except Exception:
                logger.warning('Unable to remove uncommitted publication staging: %s', operation)

        old_runtime = getattr(_state, 'ontology_service', None)
        try:
            # Commit boundary passed: install synchronously — no await, no I/O.
            _install_state(next_state)
            stage('committed')
            if operation in operations:
                operations[operation]['result'] = {'active_revision': release['revision'],
                    'workspace_revision': workspace['revision'], 'changed': True, 'sessions_reset': True}
        except BaseException:
            try:
                if committed:
                    store.restore_pointer(old_pointer)
                _state.__dict__.update(previous_state)
            except BaseException:
                _state.initialized = False
                _state.init_error = 'Ontology recovery required; restart after repairing storage'
                raise ManagementError('recovery_required', 'Ontology recovery required; queries are blocked', 503)
            finally:
                candidate_runtime.close()
            raise ManagementError('activation_failed',
                                  'Runtime installation failed; the previous version was restored', 503)

    # Post-commit cleanup failures must not turn a success into a rollback report.
    if old_runtime is not None and old_runtime is not candidate_runtime:
        try:
            await asyncio.to_thread(old_runtime.close)
        except Exception as exc:
            warnings.append(f'Previous ontology runtime cleanup failed: {type(exc).__name__}')
    try:
        async with _lock:
            await _prepare_in_thread(store.collect)
    except Exception as exc:
        warnings.append(f'Stale generation cleanup failed: {type(exc).__name__}')

    elapsed = round(time.perf_counter() - started, 3)
    logger.info("Activation %s completed in %.2fs: active=%s files=%d",
                operation, elapsed, release['revision'], len(documents))
    return {'active_revision': release['revision'], 'workspace_revision': workspace['revision'],
            'changed': True, 'sessions_reset': True, 'warnings': warnings,
            'operation_id': operation, 'elapsed_seconds': elapsed}


def _install_state(values: dict) -> None:
    """No allocations, awaits or external calls in the runtime installation boundary."""
    _state.__dict__.update(values)


# ─── P1: MySQL schema draft generation (read-only; not under the write lock) ───

@router.get('/generate-draft/tables')
async def generator_table_listing():
    return await asyncio.to_thread(list_generator_tables)


@router.post('/generate-draft')
async def generate_schema_draft(body: GenerateDraftBody):
    # Custom namespaces must not collide with any ontology already in the workset.
    existing_iris: set[str] = set()
    try:
        workspace = _store().workspace()
    except ManagementError:
        workspace = {'files': {}}
    for item in workspace['files'].values():
        if item['deleted']:
            continue
        try:
            report, _ = inspect_documents({item['name']: item['content']})
            existing_iris.update(report['ontology_iris'].values())
        except ManagementError:
            continue
    result = await asyncio.to_thread(lambda: generate_draft(
        body.tables, namespace=body.namespace,
        source_revision=body.source_revision, existing_ontology_iris=existing_iris))
    logger.info("Ontology draft generated: %d tables, %d columns, namespace=%s",
                result['report']['table_count'], result['report']['column_count'],
                result['namespace'])
    return result


# ─── Structured view/edit (classes, properties, relationships) ─────────────────

@router.get('/files/{file_id}/structure')
async def get_file_structure(file_id: str, version: Literal['workspace', 'active', 'backup'] = 'workspace'):
    stage('parsing')
    item = _store().read(file_id, version)
    model = await asyncio.to_thread(parse_structure, item['content'])
    return {'id': file_id, 'version': version, 'name': item['name'],
            'revision': item['revision'], 'model': model}


@router.post('/structure/parse')
async def parse_structure_content(body: StructureParseBody):
    if len(body.content.encode('utf-8')) > Config.MAX_FILE_BYTES:
        raise ManagementError('size_limit', 'Ontology content exceeds configured limit', 413)
    stage('parsing')
    model = await asyncio.to_thread(parse_structure, body.content)
    return {'model': model}


@router.post('/structure/serialize')
async def serialize_structure(body: StructureSerializeBody):
    if len(body.content.encode('utf-8')) > Config.MAX_FILE_BYTES:
        raise ManagementError('size_limit', 'Ontology content exceeds configured limit', 413)
    stage('patching')
    content, diff = await asyncio.to_thread(patch_structure, body.content, body.model)
    return {'content': content, 'diff': diff}


@router.put('/files/{file_id}/structure')
async def save_file_structure(file_id: str, body: StructureSaveBody):
    item = _store().read(file_id)  # 404 for missing or deleted files
    if item['protected']:
        raise ManagementError('protected_file', 'Built-in ontology content is protected', 403)
    if item['deleted']:
        raise ManagementError('deleted_file', 'Restore this file before editing it', 409)
    stage('patching')
    patched, diff = await asyncio.to_thread(patch_structure, item['content'], body.model)
    if not (diff.get('added') or diff.get('removed') or diff.get('changed')
            or diff.get('header_changes')):
        async with _lock:
            _require_idle()
            workspace = _store().workspace()
            OntologyStore.require_revision(workspace['revision'], body.expected_workspace)
            current = _store().read(file_id)
            OntologyStore.require_revision(current['revision'], body.expected_file)
            # The model was patched against this exact snapshot before awaiting.
            OntologyStore.require_revision(current['revision'], item['revision'])
            return {'operation_id': current_operation.get() or uuid.uuid4().hex,
                    'file_id': file_id, 'workspace_revision': workspace['revision'],
                    'revision': current['revision'], 'changed': False, 'diff': diff}
    report = await _file_report(patched, item['name'])
    _require_valid(report)
    async with _lock:
        _require_idle()
        result = _store().mutate('edit', expected_workspace=body.expected_workspace,
                                 file_id=file_id, expected_file=body.expected_file,
                                 content=patched)
    logger.info("Ontology structure edit committed: file=%s changed=%d added=%d removed=%d",
                file_id, len(diff['changed']), len(diff['added']), len(diff['removed']))
    return {'operation_id': current_operation.get() or uuid.uuid4().hex, 'changed': True,
            **result, 'report': report, 'diff': diff}
