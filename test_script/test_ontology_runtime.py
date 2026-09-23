"""Worker termination and cancellation tests; no database or model calls."""
import asyncio
import multiprocessing as mp
import threading
import time

import pytest

from src.ontology.managed_runtime import ManagedOntologyRuntime, create_runtime
from src.ontology.management_errors import ManagementError


def test_timeout_terminates_candidate_process():
    before = {child.pid for child in mp.active_children()}
    with pytest.raises(ManagementError) as error:
        ManagedOntologyRuntime({'a': '<never-parsed/>'}, timeout=0.001)
    assert error.value.status == 504
    assert {child.pid for child in mp.active_children()} == before


@pytest.mark.asyncio
async def test_cancellation_waits_for_candidate_owner_and_cannot_commit():
    entered = threading.Event()
    stopped = threading.Event()

    def candidate(documents, *, publication, cancel_event):
        entered.set()
        while not cancel_event.wait(0.01):
            pass
        stopped.set()
        raise ManagementError('cancelled', 'cancelled', 409)

    task = asyncio.create_task(create_runtime({}, factory=candidate))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_completed_candidate_is_closed_when_cancellation_wins():
    entered = threading.Event()
    closed = threading.Event()

    class Candidate:
        def close(self):
            closed.set()

    def candidate(documents, *, publication, cancel_event):
        entered.set()
        cancel_event.wait(2)
        return Candidate()

    task = asyncio.create_task(create_runtime({}, factory=candidate))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
