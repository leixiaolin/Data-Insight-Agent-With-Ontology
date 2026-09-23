"""Bounded, process-local management progress, separate from chat SSE."""
import asyncio
import json
import re
import time
import uuid
from collections import OrderedDict
from contextvars import ContextVar

from src.utils import get_logger

logger = get_logger(__name__)
operations: OrderedDict[str, dict] = OrderedDict()
current_operation: ContextVar[str | None] = ContextVar('ontology_operation', default=None)


def stage(value: str) -> None:
    operation = operations.get(current_operation.get())
    if operation is not None:
        operation['stage'] = value


class OntologyOperationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or not scope['path'].startswith('/ontology/')
                or scope['method'] == 'GET'):
            return await self.app(scope, receive, send)
        requested = dict(scope.get('headers', [])).get(b'x-operation-id', b'').decode('ascii', errors='ignore')
        operation_id = requested if re.fullmatch('[a-f0-9]{32}', requested) else uuid.uuid4().hex
        if operation_id in operations:
            from starlette.responses import JSONResponse
            return await JSONResponse({'detail': {'code': 'operation_exists',
                'message': 'Query the existing operation before retrying'}}, status_code=409)(scope, receive, send)
        for key in list(operations):
            if len(operations) < 200:
                break
            if operations[key]['status'] != 'running':
                del operations[key]
        record = {'operation_id': operation_id, 'stage': 'queued', 'status': 'running',
                  'path': scope['path'], 'started_at': time.time()}
        operations[operation_id] = record
        token = current_operation.set(operation_id)
        started = time.monotonic()

        async def tracked_send(message):
            if message['type'] == 'http.response.start':
                record['http_status'] = message['status']
                record['status'] = 'succeeded' if message['status'] < 400 else 'failed'
                message['headers'] = [*message.get('headers', []), (b'x-operation-id', operation_id.encode())]
            if message['type'] == 'http.response.body' and scope['path'] == '/ontology/activate':
                try:
                    result = json.loads(message.get('body', b''))
                    record['result'] = result
                except (ValueError, UnicodeDecodeError):
                    pass
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except asyncio.CancelledError:
            record['status'] = 'cancelled' if record['stage'] != 'committed' else 'succeeded'
            raise
        except Exception:
            record['status'] = 'failed'
            raise
        finally:
            record['stage'] = record['status']
            record['elapsed_seconds'] = round(time.monotonic() - started, 3)
            logger.info('Ontology operation %s: %s in %.3fs', operation_id, record['status'], record['elapsed_seconds'])
            current_operation.reset(token)
