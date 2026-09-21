"""Request-local SQL evidence and validated completion; no extra model loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from contextvars import ContextVar
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlglot import exp, parse

AnalysisStatus = Literal["completed", "partial", "insufficient", "failed"]
request_trace: ContextVar[tuple[str, str]] = ContextVar("analysis_request_trace", default=("local", "local"))


def contains_tool_protocol(text: str) -> bool:
    return bool(re.search(
        r"DSML\s*[|｜]|[<＜]\s*[|｜][^>＞]*DSML|<\s*(?:tool_calls|function_calls|invoke\s+name\s*=)",
        text, re.IGNORECASE,
    ))


def sql_fingerprint(sql: str, *, source: str, dialect: str, max_rows: int, purpose: str) -> str | None:
    try:
        statements = parse(sql, read=dialect)
        if len(statements) != 1 or statements[0] is None:
            return None
        # AST serialization preserves identifier case, quotes and literal values.
        canonical = statements[0].sql(dialect=dialect, comments=False)
        return sha256(json.dumps([source, dialect, canonical, max_rows, purpose]).encode()).hexdigest()
    except Exception:
        return None


def query_has_limit(sql: str, dialect: str) -> bool:
    try:
        tree = parse(sql, read=dialect)[0]
        return tree is None or tree.find(exp.Limit) is not None
    except Exception:
        return True


class EvidenceReference(BaseModel):
    result_id: str
    claim: str = Field(min_length=1, description="Specific conclusion supported by this query")
    scope: Literal["full", "limited"] = "full"
    scope_description: str = Field(default="", description="Required for limited evidence; state its query/sample range")


@dataclass(frozen=True)
class SQLExecution:
    result_id: str
    purpose: str
    fingerprint: str | None
    row_count: int
    truncated: bool
    requires_follow_up: bool


@dataclass(frozen=True)
class AnalysisCompletion:
    status: AnalysisStatus
    answer: str
    evidence: tuple[EvidenceReference, ...] = ()
    gaps: tuple[str, ...] = ()
    reason: str = ""

    def public_payload(self) -> dict:
        return {"analysis_status": self.status, "content": self.answer, "reason": self.reason}


@dataclass
class AnalysisLedger:
    request_id: str = field(default_factory=lambda: uuid4().hex)
    thread_id: str = field(default_factory=lambda: request_trace.get()[0])
    run_id: str = field(default_factory=lambda: request_trace.get()[1])
    executions: dict[str, SQLExecution] = field(default_factory=dict)
    # Existing tool output is reused only during this request, never logged/persisted here.
    reusable: dict[str, str] = field(default_factory=dict)
    attempts: list[dict] = field(default_factory=list)
    completion: AnalysisCompletion | None = None
    function_calls: int = 0
    model_roundtrips: int = 0
    budget_exhausted: bool = False
    last_error: str = ""
    lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def trace(self) -> str:
        return f"{self.request_id} thread={self.thread_id} run={self.run_id} agent=DataInsightAgent"

    def record(self, *, purpose: str, fingerprint: str | None, row_count: int,
               truncated: bool, requires_follow_up: bool) -> SQLExecution:
        record = SQLExecution(
            result_id=f"{self.request_id}:{len(self.executions) + 1}",
            purpose=purpose, fingerprint=fingerprint, row_count=row_count,
            truncated=truncated, requires_follow_up=requires_follow_up,
        )
        self.executions[record.result_id] = record
        return record

    def complete(self, status: str, answer: str, evidence: list[EvidenceReference],
                 gaps: list[str], *, pending_diagnostic: bool) -> str:
        if self.completion is not None:
            return "BLOCKED: A completion was already accepted."
        if status not in {"completed", "partial", "insufficient"}:
            return "BLOCKED: Invalid analysis status."
        if not answer.strip() or contains_tool_protocol(answer):
            return "BLOCKED: A nonempty natural-language answer without tool protocol is required."
        if any(not ref.claim.strip() for ref in evidence):
            return "BLOCKED: Every evidence reference must state the supported claim."
        if any(ref.result_id not in self.executions for ref in evidence):
            return "BLOCKED: Evidence must reference successful SQL from this request."
        gaps = [gap.strip() for gap in gaps if gap.strip()]
        if status == "completed":
            if self.budget_exhausted:
                return "BLOCKED: Analysis budget exhausted."
            if gaps or pending_diagnostic:
                return "BLOCKED: Resolve gaps and the pending diagnostic before completed."
            if not any(self.executions[ref.result_id].purpose == "analysis" for ref in evidence):
                return "BLOCKED: Completed requires a successful analysis query."
        if status == "partial" and not evidence:
            return "BLOCKED: Partial requires successful SQL evidence."
        if status in {"partial", "insufficient"} and not gaps:
            return "BLOCKED: State the missing evidence or limitations in gaps."
        for ref in evidence:
            record = self.executions[ref.result_id]
            if record.truncated and ref.scope != "limited":
                return "BLOCKED: Capped results cannot support full-range claims; use complete aggregation or limited scope."
            if ref.scope == "limited" and not ref.scope_description.strip():
                return "BLOCKED: Limited evidence requires a scope_description."
        # Render limitations deterministically, so declarations cannot hide them in metadata.
        limitations = list(dict.fromkeys([
            *gaps, *(ref.scope_description.strip() for ref in evidence if ref.scope == "limited")
        ]))
        rendered = answer.strip()
        if limitations:
            rendered += "\n\n范围与限制：\n" + "\n".join(f"- {item}" for item in limitations)
        if contains_tool_protocol(rendered):
            return "BLOCKED: Tool protocol cannot appear in a completion."
        self.completion = AnalysisCompletion(status, rendered, tuple(evidence), tuple(gaps))
        return "Completion accepted. Do not call more tools or restate the answer."

    def finish(self, raw_text: str = "") -> AnalysisCompletion:
        if self.completion is not None:
            return self.completion
        reason = (
            "invalid_tool_output" if contains_tool_protocol(raw_text) else
            "budget_exhausted" if self.budget_exhausted else
            "missing_completion"
        )
        messages = {
            "invalid_tool_output": "分析未完成：模型返回了工具协议文本，尚未形成有效结论。",
            "budget_exhausted": "分析未完成：已达到本次调用预算，现有证据尚未形成有效结论。",
            "missing_completion": "分析未完成：未提交通过证据校验的最终结论。",
        }
        return AnalysisCompletion("failed", messages[reason], reason=reason)
