"""
FastAPI backend server — bridges the React frontend to the Python agent system.

Endpoints
---------
POST  /chat/stream          Server-Sent Events streaming chat (main endpoint)
POST  /threads/new          Create a new conversation thread
GET   /threads              List all active threads
GET   /threads/{id}/history Get message history for a thread
DELETE /threads/{id}        Delete a thread
POST  /threads/{id}/stop    Stop the active run for one thread
GET   /business-layer       Read the workspace business semantic document
PUT   /business-layer       Save the workspace business semantic document
GET   /skills               List available skills
GET   /config               Return non-sensitive runtime capability defaults
GET   /health               Health check

SSE event format (matches what the frontend expects)
----------------------------------------------------
data: {"type": "thinking", "message": "<step description>"}
data: {"type": "text",     "content": "<response chunk>"}
data: {"type": "answer_reset"}
data: {"type": "thinking_done"}
data: {"type": "stopped",  "message": "<stop description>"}
data: {"type": "done"}
data: {"type": "error",    "message": "<error description>"}

Architecture
------------
* A single MasterAgent is created at startup and shared across all requests.
* Thread objects are stored in an in-memory dict keyed by thread_id (UUID string).
* Agent-scoped native MAF SkillsProvider instances progressively disclose assigned Skills.
* CORS is configured to allow the Vite dev server (localhost:3000) and production origins.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Event
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit, unquote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.agents import (
    DataInsightAgent,
    MasterAgent,
    MetadataAgent,
    OntologyAgent,
)
from src.ontology import OntologyService
from src.config import AppConfig
from src.business_layer import load_business_layer, save_business_layer
from src.utils import get_logger
from src.utils.activity import (
    delegated_agent,
    narration_activity,
    tool_activity,
)
from src.skills_provider import list_skill_metadata

logger = get_logger(__name__)

# ─── Application state ─────────────────────────────────────────────────────────


@dataclass
class ActiveRun:
    """One in-flight agent task owned by a single conversation thread."""

    run_id: str
    cancel_event: Event
    task: Optional[asyncio.Task] = None

class AppState:
    """Holds singletons shared across all requests."""

    master_agent: Optional[MasterAgent] = None
    ontology_service: Optional[OntologyService] = None
    ontology_error: Optional[str] = None
    # thread_id (str) → MAF thread object
    threads: Dict[str, object] = {}
    # thread_id → list of {"user": str, "assistant": str, "timestamp": str}
    thread_history: Dict[str, List[dict]] = {}
    # A thread may run one task at a time; different threads run concurrently.
    active_runs: Dict[str, ActiveRun] = {}
    initialized: bool = False
    init_error: Optional[str] = None


state = AppState()


# ─── Lifespan: startup / shutdown ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context — runs startup logic before serving requests."""
    logger.info("FastAPI server starting up…")

    # Initialise agents and their native MAF Skill providers.
    try:
        # MetadataAgent and DataInsightAgent are optional (need Databricks config)
        metadata_agent: Optional[MetadataAgent] = None
        data_insight_agent: Optional[DataInsightAgent] = None

        metadata_agent = MetadataAgent()
        logger.info("MetadataAgent initialised.")

        ontology_agent: Optional[OntologyAgent] = None
        try:
            state.ontology_service = OntologyService().load()
            ontology_agent = OntologyAgent(state.ontology_service)
            state.ontology_error = state.ontology_service.reasoning_error
            logger.info("OntologyAgent initialised.")
        except Exception as ontology_exc:
            state.ontology_service = None
            state.ontology_error = str(ontology_exc)
            logger.error(
                "Ontology capability initialisation failed: %s",
                ontology_exc,
                exc_info=True,
            )

        data_insight_agent = DataInsightAgent(
            metadata_agent=metadata_agent,
            ontology_agent=ontology_agent,
        )
        logger.info("DataInsightAgent initialised.")

        state.master_agent = MasterAgent(
            data_insight_agent=data_insight_agent,
            metadata_agent=metadata_agent,
            ontology_agent=ontology_agent,
        )
        state.initialized = True
        logger.info("MasterAgent initialised successfully.")
        skills = await list_skill_metadata(state.master_agent.agent)
        logger.info(f"Skills discovered by MAF: {len(skills)}")

    except Exception as exc:
        state.init_error = str(exc)
        state.initialized = False
        logger.error(f"Agent initialisation failed: {exc}", exc_info=True)

    yield  # Server is running

    if state.ontology_service is not None:
        state.ontology_service.close()
    logger.info("FastAPI server shutting down.")


# ─── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Ontology Data Agent API",
    description="Enterprise AI agent backend (ontology-driven data insight + metadata)",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow Vite dev server and same-origin production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    enable_ontology: Optional[bool] = None


class NewThreadRequest(BaseModel):
    thread_id: Optional[str] = None


class BusinessLayerBody(BaseModel):
    content: str


class ThreadInfo(BaseModel):
    thread_id: str
    message_count: int
    last_updated: str


# ─── SSE streaming helper ───────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    """Format a dict as a Server-Sent Events data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalize_cache_question(question: str) -> str:
    """Normalize an exact question for safe, session-local response caching."""
    normalized = unicodedata.normalize("NFKC", question or "").strip().casefold()
    return " ".join(normalized.split())


_CACHE_FAILURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"\b(?:timed?\s*out|timeout)\b",
        r"\b(?:please\s+)?(?:try|retry)\s+again\b",
        r"\bno\s+(?:reliable|usable)\s+(?:result|answer)\b",
        r"\b(?:connection|network|service|request|query|analysis|agent)\b.{0,40}\b(?:failed|failure|error|unavailable|reset|refused|interrupted)\b",
        r"\b(?:unable|failed)\s+to\s+(?:obtain|retrieve|generate|complete|confirm|return)\b",
        r"超时",
        r"请(?:稍后)?重试",
        r"(?:网络|连接).{0,20}(?:错误|异常|中断|失败|重置)",
        r"(?:未能|无法|暂时无法).{0,30}(?:获得|获取|生成|完成|确认|返回).{0,20}(?:结果|答案)?",
        r"(?:查询|分析|请求|代理|服务).{0,20}(?:失败|错误|不可用|异常)",
    )
)


def _response_cache_eligibility(response: str) -> tuple[bool, str]:
    """Classify whether a completed response is safe to reuse as an answer."""
    text = unicodedata.normalize("NFKC", str(response or "")).strip()
    if not text:
        return False, "empty_response"
    if any(pattern.search(text) for pattern in _CACHE_FAILURE_PATTERNS):
        return False, "failure_response"
    return True, "completed_response"


def _find_cached_response(
    thread_id: str,
    question: str,
    enable_ontology: bool = AppConfig.DEFAULT_ENABLE_ONTOLOGY,
) -> Optional[str]:
    """Return the latest completed answer for the same question in this thread only."""
    if not AppConfig.SESSION_RESPONSE_CACHE_ENABLED:
        return None
    cache_key = _normalize_cache_question(question)
    if not cache_key:
        return None
    for turn in reversed(state.thread_history.get(thread_id, [])):
        if AppConfig.SESSION_RESPONSE_CACHE_TTL_SECONDS > 0:
            try:
                cached_at = datetime.fromisoformat(str(turn.get("timestamp") or ""))
                age_seconds = (datetime.now(timezone.utc) - cached_at).total_seconds()
                if age_seconds > AppConfig.SESSION_RESPONSE_CACHE_TTL_SECONDS:
                    continue
            except (TypeError, ValueError):
                continue
        if (
            _normalize_cache_question(str(turn.get("user") or "")) == cache_key
            and str(turn.get("assistant") or "").strip()
            and turn.get("cache_eligible") is True
            and turn.get("enable_ontology") is enable_ontology
        ):
            return str(turn["assistant"])
    return None


def _public_ontology_health() -> Dict[str, Any]:
    """Return non-sensitive ontology status with a concise error summary."""
    raw = (
        state.ontology_service.health()
        if state.ontology_service is not None
        else {
            "available": False,
            "reasoner_enabled": False,
            "reasoner": "unavailable",
            "reasoning_status": "unavailable",
            "reasoning_error": state.ontology_error,
            "file_count": 0,
            "ontology_count": 0,
            "entity_count": 0,
        }
    )
    raw_error = str(raw.get("reasoning_error") or "")
    error_lines = [
        line.strip()
        for line in raw_error.splitlines()
        if line.strip()
        and line.strip() != "Java error message is:"
        and not line.lstrip().startswith("at ")
    ]
    return {
        "available": bool(raw.get("available")),
        "reasoner_enabled": bool(raw.get("reasoner_enabled")),
        "reasoner": raw.get("reasoner"),
        "reasoning_status": raw.get("reasoning_status"),
        "reasoning_error": error_lines[0][:500] if error_lines else None,
        "file_count": int(raw.get("file_count") or 0),
        "ontology_count": int(raw.get("ontology_count") or 0),
        "entity_count": int(raw.get("entity_count") or 0),
    }


async def _cached_response_stream(
    message: str,
    thread_id: str,
    cached_response: str,
    enable_ontology: bool,
) -> AsyncGenerator[str, None]:
    """Serve a session-memory hit without invoking MAF or external services."""
    yield _sse(
        {
            "type": "thinking",
            "id": f"cache-{uuid.uuid4().hex[:12]}",
            "kind": "status",
            "category": "session-cache",
            "state": "completed",
            "agent": "MasterAgent",
            "message": "Reused answer from this session",
            "summary": "Exact normalized question and ontology mode matched a completed turn",
            "metrics": {"cache_hit": True, "enable_ontology": enable_ontology},
        }
    )
    yield _sse({"type": "text", "content": cached_response})
    _append_history(
        thread_id,
        message,
        cached_response,
        cache_hit=True,
        enable_ontology=enable_ontology,
    )
    yield _sse({"type": "thinking_done"})
    yield _sse({"type": "done", "content": cached_response, "cache_hit": True})


def _table_row_cells(row: str) -> Optional[List[str]]:
    stripped = row.strip()
    if len(stripped) < 2 or not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return stripped[1:-1].split("|")


def _is_table_separator_row(cells: List[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells
    )


def _split_collapsed_table_row(
    line: str,
    expected_cells: Optional[int],
) -> Optional[List[str]]:
    """Split one flattened line into table rows, or return None to leave it alone."""
    candidates = re.sub(r"\|\s*\|", "|\n|", line).splitlines()
    if len(candidates) < 2:
        return None
    parsed = [_table_row_cells(candidate) for candidate in candidates]
    if any(cells is None for cells in parsed):
        return None
    widths = {len(cells) for cells in parsed if cells is not None}
    if len(widths) != 1:
        return None
    width = widths.pop()
    if expected_cells is not None:
        # A row with genuinely empty cells splits into the wrong width, so it stays intact.
        return candidates if width == expected_cells else None
    return (
        candidates
        if any(_is_table_separator_row(cells) for cells in parsed if cells)
        else None
    )


def _repair_collapsed_markdown_tables(text: str) -> str:
    """Restore newlines when a model flattens GFM table rows onto one line.

    Rows are only split when every resulting row matches the table's column
    count, so ordinary prose and rows with empty cells are left unchanged.
    """
    if not text or "|" not in text:
        return text

    repaired_lines: List[str] = []
    in_fence = False
    expected_cells: Optional[int] = None

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            expected_cells = None
            repaired_lines.append(line)
            continue

        cells = None if in_fence else _table_row_cells(line)
        if cells is None:
            expected_cells = None
            repaired_lines.append(line)
            continue

        rows = _split_collapsed_table_row(line, expected_cells)
        if rows is None:
            repaired_lines.append(line)
            if _is_table_separator_row(cells):
                expected_cells = len(cells)
            continue

        repaired_lines.extend(rows)
        first_row = _table_row_cells(rows[0])
        expected_cells = len(first_row) if first_row else None

    return "\n".join(repaired_lines)


async def _stream_agent_response(
    message: str,
    thread,
    thread_id: str,
    active_run: ActiveRun,
    enable_ontology: bool = AppConfig.DEFAULT_ENABLE_ONTOLOGY,
    business_layer: str = "",
) -> AsyncGenerator[str, None]:
    """
    Run MasterAgent.chat_stream and convert MAF update objects to SSE events.

    Architecture — single combined asyncio.Queue (no polling):
    ─ feed_master task:  master agent updates → combined.put(("maf", upd))
    ─ insight tool thread: push() → call_soon_threadsafe → combined.put(("text"|"thinking", data))
    ─ main loop: await combined.get() — wakes instantly when any item arrives
    """
    full_response_parts: List[str] = []
    _working_text_parts: List[str] = []
    _working_text_id = 1
    cache_failure_observed = False

    # ── Single combined queue — avoids all polling ────────────────────────────
    combined: asyncio.Queue = asyncio.Queue()
    main_loop = asyncio.get_event_loop()

    async def feed_master():
        """Push every MAF update from chat_stream into the combined queue."""
        stream = state.master_agent.chat_stream(
            message=message,
            thread=thread,
            stream_context=(combined, main_loop),
            cancel_event=active_run.cancel_event,
            enable_ontology=enable_ontology,
            business_layer=business_layer,
        )
        try:
            async for upd in stream:
                await combined.put(("maf", upd))
        except asyncio.CancelledError:
            await combined.put(("cancelled", None))
            raise
        except Exception as exc:
            await combined.put(("error", exc))
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass
            if not active_run.cancel_event.is_set():
                await combined.put(("done", None))

    master_task = asyncio.create_task(feed_master())
    active_run.task = master_task

    # ── Pending tool-call tracker (accumulates streamed args) ─────────────────
    # MAF may stream function_call arguments across multiple deltas.
    # We hold the last seen call name + accumulated args string.
    _pending_call_name: Optional[str] = None
    _pending_call_args: str = ""
    _pending_call_id: str = ""
    _tool_activities: Dict[str, dict] = {}

    def _start_tool(name: str, args: dict, call_id: str, agent: Optional[str] = None) -> str:
        activity = tool_activity(
            name,
            args,
            call_id,
            agent=agent or "MasterAgent",
        )
        _tool_activities[call_id] = activity
        return _sse({"type": "thinking", **activity})

    def _end_tool(call_id: str, error: bool = False) -> Optional[str]:
        activity = _tool_activities.get(call_id)
        if not activity:
            return None
        return _sse(
            {
                "type": "thinking",
                **activity,
                "state": "error" if error else "completed",
            }
        )

    def _reclassify_working_text() -> List[str]:
        """Move assistant text followed by a tool call from answer to working narration."""
        nonlocal _working_text_id, _working_text_parts
        if not _working_text_parts:
            return []
        raw_text = "".join(_working_text_parts)
        text = raw_text.strip()
        segment_id = f"narration-{_working_text_id}"
        _working_text_id += 1
        _working_text_parts = []
        if not text:
            return []
        if (
            not enable_ontology
            and _pending_call_name == "delegate_data_analysis"
            and re.search(r"ontology\s*agent|ontologyagent|本体", text, re.IGNORECASE)
        ):
            text = (
                "我会让 MetadataAgent 核验所需表、字段和连接，再由 "
                "DataInsightAgent 执行分析。"
                if re.search(r"[\u3400-\u9fff]", text)
                else (
                    "MetadataAgent will verify the required tables, fields, and joins; "
                    "DataInsightAgent will then run the analysis."
                )
            )
        full_response_parts.clear()
        return [
            _sse({"type": "answer_reset"}),
            _sse({"type": "thinking", **narration_activity(
                segment_id,
                text,
                agent="MasterAgent",
            )}),
        ]

    def _flush_pending_call() -> List[str]:
        """Flush accumulated tool arguments as a structured lifecycle event."""
        nonlocal _pending_call_id, _pending_call_name, _pending_call_args
        if not _pending_call_name:
            return []
        try:
            args = json.loads(_pending_call_args) if _pending_call_args else {}
        except Exception:
            args = {}
        call_id = _pending_call_id or f"anonymous-{len(_tool_activities) + 1}"
        events = _reclassify_working_text()
        if delegated_agent(_pending_call_name) is None:
            events.append(_start_tool(_pending_call_name, args, call_id, "MasterAgent"))
        _pending_call_id = ""
        _pending_call_name = None
        _pending_call_args = ""
        return events

    def _process_maf_update(update) -> List[str]:
        """Convert one MAF update object → list of SSE strings."""
        nonlocal _pending_call_id, _pending_call_name, _pending_call_args
        nonlocal cache_failure_observed
        events: List[str] = []

        # Ordinary assistant text is streamed immediately. If a tool call follows,
        # it is reclassified as working narration; the final trailing segment remains the answer.
        if hasattr(update, "text") and update.text:
            _working_text_parts.append(update.text)
            events.append(_sse({"type": "text", "content": update.text}))

        if not (hasattr(update, "contents") and update.contents):
            return events

        for content in update.contents:
            ct = getattr(content, "type", None)
            if ct == "text":
                continue

            if ct == "text_reasoning":
                reasoning_text = (getattr(content, "text", "") or "").strip()
                if reasoning_text:
                    events.append(
                        _sse(
                            {
                                "type": "thinking",
                                "id": "model-reasoning",
                                "kind": "reasoning",
                                "state": "running",
                                "agent": "MasterAgent",
                                "message": reasoning_text,
                                "append": True,
                            }
                        )
                    )
                continue

            if ct == "function_call":
                tname = getattr(content, "name", "") or ""
                targs = getattr(content, "arguments", "") or ""

                if tname and tname != _pending_call_name:
                    # New tool call started — flush the previous one first
                    events.extend(_flush_pending_call())
                    _pending_call_name = tname
                    _pending_call_id = getattr(content, "call_id", "") or ""
                    _pending_call_args = targs
                else:
                    # Same tool call — accumulate argument delta
                    _pending_call_args += targs

                # If args look complete (valid JSON), flush immediately
                if _pending_call_args:
                    try:
                        json.loads(_pending_call_args)
                        events.extend(_flush_pending_call())
                    except json.JSONDecodeError:
                        pass  # args still streaming, wait for more

            elif ct == "function_result":
                result_payload = getattr(content, "result", None)
                exception = getattr(content, "exception", None)
                _, result_reason = _response_cache_eligibility(
                    str(result_payload or "")
                )
                if exception or result_reason == "failure_response":
                    cache_failure_observed = True
                events.extend(_flush_pending_call())
                call_id = getattr(content, "call_id", "") or ""
                completed = _end_tool(call_id, error=bool(exception))
                if completed:
                    events.append(completed)
        return events

    try:
        while True:
            # await — no polling; wakes instantly when any item arrives
            item_type, item_data = await combined.get()

            if item_type == "done":
                # Flush any remaining pending call
                for evt in _flush_pending_call():
                    yield evt
                break

            elif item_type == "cancelled":
                yield _sse({"type": "stopped", "message": "Task stopped by user"})
                return

            elif item_type == "error":
                raise item_data

            elif item_type == "maf":
                for evt in _process_maf_update(item_data):
                    yield evt

            elif item_type == "text":
                # DataInsightAgent or MetadataAgent streaming text
                full_response_parts.append(item_data)
                yield _sse({"type": "text", "content": item_data})

            elif item_type == "activity" and isinstance(item_data, dict):
                if item_data.get("state") == "error":
                    cache_failure_observed = True
                yield _sse({"type": "thinking", **item_data})

    except Exception as exc:
        logger.error(f"Streaming error: {exc}", exc_info=True)
        yield _sse({"type": "error", "message": str(exc)})
        return
    finally:
        # Cancel (if still running) and ALWAYS await master_task so async-generator
        # finalizers are drained before the request scope exits.
        if not master_task.done():
            master_task.cancel()
        try:
            await asyncio.wait_for(master_task, timeout=3)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        current_run = state.active_runs.get(thread_id)
        if current_run is active_run:
            state.active_runs.pop(thread_id, None)

    # The only assistant text not followed by another tool call is the final answer.
    final_text = "".join(_working_text_parts)
    full_response_parts.append(final_text)

    # ── Post-process final response ───────────────────────────────────────────
    full_response = "".join(full_response_parts)

    # Models occasionally preserve table pipes but collapse all row newlines.
    # Repair that narrow malformed shape before the response is returned.
    full_response = _repair_collapsed_markdown_tables(full_response)

    # Normalize markdown image alt from chunked figcaption form.
    full_response = re.sub(r'!\[<figcaption>(.*?)</figcaption>\]', r'![\1]', full_response)
    full_response = re.sub(r'!\[<figcaption></figcaption>\]', r'![]', full_response)

    full_response = full_response.rstrip()

    response_cache_eligible, _ = _response_cache_eligibility(full_response)
    _append_history(
        thread_id,
        message,
        full_response,
        enable_ontology=enable_ontology,
        cache_eligible=(response_cache_eligible and not cache_failure_observed),
    )

    yield _sse({"type": "thinking_done"})
    yield _sse({"type": "done", "content": full_response})



# ─── Route helpers ──────────────────────────────────────────────────────────────

def _get_or_create_thread(thread_id: Optional[str]) -> tuple[str, object]:
    """
    Return (thread_id, thread_object).
    Creates a new thread if thread_id is None or not found in the store.
    """
    if thread_id and thread_id in state.threads:
        return thread_id, state.threads[thread_id]

    # Create a new thread via MasterAgent
    thread = state.master_agent.get_new_thread()
    new_id = thread_id or str(uuid.uuid4())
    state.threads[new_id] = thread
    state.thread_history[new_id] = []
    logger.info(f"Created new thread: {new_id}")
    return new_id, thread


def _append_history(
    thread_id: str,
    user_msg: str,
    assistant_msg: str,
    *,
    cache_hit: bool = False,
    enable_ontology: bool,
    cache_eligible: Optional[bool] = None,
) -> None:
    """Store a completed turn in the thread history."""
    if thread_id not in state.thread_history:
        state.thread_history[thread_id] = []
    inferred_eligible, eligibility_reason = _response_cache_eligibility(
        assistant_msg
    )
    effective_eligible = (
        inferred_eligible if cache_eligible is None else bool(cache_eligible)
    )
    if cache_hit:
        effective_eligible = True
        eligibility_reason = "cache_hit"
    elif not effective_eligible and eligibility_reason == "completed_response":
        eligibility_reason = "explicitly_ineligible"
    state.thread_history[thread_id].append(
        {
            "user": user_msg,
            "assistant": assistant_msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cache_hit": cache_hit,
            "cache_eligible": effective_eligible,
            "cache_eligibility_reason": eligibility_reason,
            "enable_ontology": enable_ontology,
        }
    )


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Return service health and agent status."""
    return {
        "status": "ok" if state.initialized else "degraded",
        "agent_initialized": state.initialized,
        "init_error": state.init_error,
        "active_threads": len(state.threads),
        "ontology": _public_ontology_health(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/config")
async def get_runtime_config():
    """Return non-sensitive runtime defaults and optional capability status."""
    return {
        "default_enable_ontology": AppConfig.DEFAULT_ENABLE_ONTOLOGY,
        "ontology": _public_ontology_health(),
    }


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Main chat endpoint with Server-Sent Events streaming.
    The frontend calls this directly at http://localhost:8000/chat/stream.
    """
    if not state.initialized or state.master_agent is None:
        async def _error_stream():
            yield _sse(
                {
                    "type": "error",
                    "message": f"Agent not initialised. {state.init_error or ''}",
                }
            )
            yield _sse({"type": "done"})

        return StreamingResponse(_error_stream(), media_type="text/event-stream")

    thread_id, thread = _get_or_create_thread(request.thread_id)
    enable_ontology = (
        AppConfig.DEFAULT_ENABLE_ONTOLOGY
        if request.enable_ontology is None
        else request.enable_ontology
    )

    existing_run = state.active_runs.get(thread_id)
    if existing_run is not None:
        if existing_run.task is None or not existing_run.task.done():
            raise HTTPException(
                status_code=409,
                detail="This session already has an active task.",
            )
        state.active_runs.pop(thread_id, None)

    cached_response = _find_cached_response(
        thread_id,
        request.message,
        enable_ontology,
    )
    if cached_response is not None:
        return StreamingResponse(
            _cached_response_stream(
                request.message,
                thread_id,
                cached_response,
                enable_ontology,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Thread-Id": thread_id,
                "X-Cache": "HIT",
            },
        )

    active_run = ActiveRun(
        run_id=str(uuid.uuid4()),
        cancel_event=Event(),
    )
    state.active_runs[thread_id] = active_run

    return StreamingResponse(
        _stream_agent_response(
            request.message,
            thread,
            thread_id,
            active_run,
            enable_ontology,
            load_business_layer(),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Thread-Id": thread_id,
        },
    )


@app.post("/threads/{thread_id}/stop")
async def stop_thread_run(thread_id: str):
    """Cancel the active agent loop for one session without affecting other sessions."""
    active_run = state.active_runs.get(thread_id)
    if active_run is None:
        return {"thread_id": thread_id, "stopped": False, "reason": "no_active_task"}

    active_run.cancel_event.set()
    if active_run.task is not None and not active_run.task.done():
        active_run.task.cancel()
    return {"thread_id": thread_id, "stopped": True, "run_id": active_run.run_id}


@app.post("/threads/new")
async def create_thread(body: NewThreadRequest = NewThreadRequest()):
    """Create a new conversation thread and return its ID."""
    if not state.initialized or state.master_agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialised.")

    thread = state.master_agent.get_new_thread()
    thread_id = body.thread_id or str(uuid.uuid4())
    state.threads[thread_id] = thread
    state.thread_history[thread_id] = []
    return {
        "thread_id": thread_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/threads")
async def list_threads():
    """List all active threads."""
    result = []
    for tid, history in state.thread_history.items():
        last_updated = (
            history[-1]["timestamp"] if history else datetime.now(timezone.utc).isoformat()
        )
        result.append(
            {"id": tid, "message_count": len(history), "last_updated": last_updated}
        )
    return result


@app.get("/threads/{thread_id}/history")
async def get_thread_history(thread_id: str):
    """Return message history for a specific thread."""
    if thread_id not in state.thread_history:
        raise HTTPException(status_code=404, detail="Thread not found.")
    return {"thread_id": thread_id, "messages": state.thread_history[thread_id]}


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a thread and its history from memory."""
    active_run = state.active_runs.pop(thread_id, None)
    if active_run is not None and active_run.task is not None and not active_run.task.done():
        active_run.task.cancel()
    state.threads.pop(thread_id, None)
    state.thread_history.pop(thread_id, None)
    return {"deleted": thread_id}


@app.get("/skills")
async def list_skills():
    """Return all indexed skills (name, description, tags)."""
    if state.master_agent is None:
        return []
    return await list_skill_metadata(state.master_agent.agent)


@app.get("/business-layer")
async def get_business_layer():
    """Return the workspace business semantic document."""
    return {"content": load_business_layer()}


@app.put("/business-layer")
async def put_business_layer(body: BusinessLayerBody):
    """Persist the workspace business semantic document."""
    stored = save_business_layer(body.content)
    return {"ok": True, "length": len(stored)}


# ─── Dev entry-point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    port = int(__import__("os").getenv("BACKEND_PORT", "8000"))
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
