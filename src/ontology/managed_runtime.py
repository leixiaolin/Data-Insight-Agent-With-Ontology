"""Bounded, terminable ontology World owned by a dedicated worker process."""
from __future__ import annotations

import multiprocessing as mp
import asyncio
import time
import os
import signal
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from src.config.settings import OntologyConfig, OntologyManagementConfig as Config
from .management_errors import ManagementError

METHODS = frozenset({
    'search_entities', 'describe_entity', 'expand_neighbors', 'find_paths',
    'find_related_by_type', 'get_schema_mapping', 'get_join_paths', 'get_lineage',
    'get_semantic_candidates', 'get_business_context', 'list_defined_classes',
})


def _worker(connection, documents: dict[str, str], publication: bool, options: dict, limits: dict):
    if os.name != 'nt':
        os.setsid()
    from .validation import inspect_documents
    from .service import OntologyService
    service = None
    try:
        for key, value in limits.items():
            setattr(Config, key, value)
        report, sanitized = inspect_documents(documents, publication=publication)
        if not report['valid']:
            connection.send({'report': report})
            return
        with tempfile.TemporaryDirectory(prefix='oda-ontology-') as directory:
            for index, content in enumerate(sanitized.values()):
                Path(directory, f'{index}.owl').write_bytes(content)
            service = OntologyService(directory, file_glob='*.owl', only_local=True, **options)
            service.load()
            if service.load_errors or service.reasoning_error:
                raise ManagementError('load_failed', 'Candidate ontology failed strict loading or reasoning')
            # Imports never reach a file/network resolver. Link only loaded objects.
            for file, targets in report['imports'].items():
                origin = service.world.get_ontology(report['ontology_iris'][file])
                for target in targets:
                    if target in report['ontology_iris'].values():
                        origin.imported_ontologies.append(service.world.get_ontology(target))
            health = service.health()
            health.pop('directory', None)
            report['checks'].append({'name': 'runtime_load', 'status': 'passed'})
            if options['enable_reasoner']:
                report['checks'][1]['status'] = 'passed'
            connection.send({'report': report, 'health': health})
            while True:
                method, args, kwargs = connection.recv()
                if method == 'close':
                    break
                if method not in METHODS:
                    raise ValueError('Unsupported operation')
                connection.send(getattr(service, method)(*args, **kwargs))
    except EOFError:
        pass
    except ManagementError as exc:
        connection.send({'failure': exc.detail, 'status': exc.status})
    except Exception:
        # Parser exceptions may contain file paths or uploaded text.
        try:
            connection.send({'failure': {'code': 'load_failed', 'message': 'Unable to parse or index ontology'}, 'status': 422})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if service is not None:
            service.close()
        connection.close()


class ManagedOntologyRuntime:
    """OntologyService-compatible query facade; the World never leaves its worker."""

    def __init__(self, documents: dict[str, str], *, publication: bool = True, timeout: float | None = None,
                 cancel_event: threading.Event | None = None):
        self.loaded = False
        self.reasoning_error = None
        self._lock = threading.RLock()
        self._closed = False
        self._timeout = timeout or Config.TIMEOUT_SECONDS
        self._cancel_event = cancel_event
        context = mp.get_context('spawn')
        self._connection, child = context.Pipe()
        self._process = context.Process(target=_worker, args=(child, documents, publication, {
            'enable_reasoner': OntologyConfig.ENABLE_REASONER if publication else False,
            'reasoner': OntologyConfig.REASONER,
            'max_results': OntologyConfig.MAX_RESULTS, 'max_depth': OntologyConfig.MAX_DEPTH,
            'max_paths': OntologyConfig.MAX_PATHS, 'max_nodes': OntologyConfig.MAX_NODES,
            'fuzzy_threshold': OntologyConfig.FUZZY_THRESHOLD,
        }, {key: getattr(Config, key) for key in ('MAX_FILES', 'MAX_FILE_BYTES', 'MAX_TOTAL_BYTES')}), daemon=True)
        self._process.start()
        child.close()
        try:
            initial = self._receive()
            if 'failure' in initial:
                failure = initial['failure']
                raise ManagementError(failure['code'], failure['message'], initial['status'])
            self.report = initial['report']
            if not self.report['valid']:
                raise ManagementError('invalid_ontology', 'Ontology validation failed', issues=self.report)
            self._health = initial['health']
            self.loaded = True
        except BaseException:
            self.close()
            raise

    def _receive(self):
        deadline = time.monotonic() + self._timeout
        while not self._connection.poll(min(0.1, max(0, deadline - time.monotonic()))):
            if self._cancel_event is not None and self._cancel_event.is_set():
                self.close()
                raise ManagementError('cancelled', 'Ontology operation was cancelled', 409)
            if time.monotonic() >= deadline:
                self.close()
                raise ManagementError('timeout', 'Ontology execution exceeded its time budget', 504)
        try:
            return self._connection.recv()
        except (EOFError, OSError) as exc:
            self.loaded = False
            raise ManagementError('worker_unavailable', 'Ontology worker is unavailable', 503) from exc

    def health(self) -> dict:
        return {**self._health, 'available': self.loaded and self._process.is_alive()}

    def __getattr__(self, name: str):
        if name not in METHODS:
            raise AttributeError(name)
        def query(*args: Any, **kwargs: Any):
            with self._lock:
                try:
                    self._connection.send((name, args, kwargs))
                    return self._receive()
                except (ManagementError, OSError):
                    return {'status': 'error', 'data': {}, 'confidence': 0,
                            'warnings': ['Ontology worker unavailable'], 'evidence': []}
        return query

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.loaded = False
            if self._process.is_alive():
                # Kill descendants too: a configured reasoner can own a JVM.
                if os.name == 'nt':
                    try:
                        subprocess.run(['taskkill', '/PID', str(self._process.pid), '/T', '/F'],
                                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                    finally:
                        if self._process.is_alive():
                            self._process.kill()
                else:
                    try:
                        os.killpg(self._process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self._process.join(timeout=5)
            self._connection.close()


def validate_documents(documents: dict[str, str], *, publication: bool = False) -> dict:
    try:
        runtime = ManagedOntologyRuntime(documents, publication=publication)
    except ManagementError as exc:
        if 'issues' in exc.detail:
            return exc.detail['issues']
        if exc.status == 422:
            return {'scope': 'publication' if publication else 'file', 'valid': False,
                    'ontology_iri': None, 'class_count': 0, 'property_count': 0, 'individual_count': 0,
                    'errors': [{**exc.detail, 'file': '', 'severity': 'error'}], 'warnings': [], 'checks': []}
        raise
    try:
        return runtime.report
    finally:
        runtime.close()


async def create_runtime(documents: dict[str, str], *, publication: bool = True,
                         factory=ManagedOntologyRuntime) -> ManagedOntologyRuntime:
    """Cancellation waits for the worker owner to terminate; no abandoned World."""
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(
        factory, documents, publication=publication, cancel_event=cancelled))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        try:
            runtime = await asyncio.shield(task)
            await asyncio.to_thread(runtime.close)
        except Exception:
            pass
        raise


async def validate_async(documents: dict[str, str], *, publication: bool = False) -> dict:
    try:
        runtime = await create_runtime(documents, publication=publication)
    except ManagementError as exc:
        if 'issues' in exc.detail:
            return exc.detail['issues']
        if exc.status != 422:
            raise
        return {'scope': 'publication' if publication else 'file', 'valid': False,
                'ontology_iri': None, 'class_count': 0, 'property_count': 0, 'individual_count': 0,
                'errors': [{**exc.detail, 'severity': 'error', 'file': ''}], 'warnings': [], 'checks': []}
    try:
        return runtime.report
    finally:
        await asyncio.to_thread(runtime.close)
