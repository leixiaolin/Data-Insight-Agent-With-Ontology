"""Observe and reserve the existing MAF budget, including native skill tools."""

from __future__ import annotations

from typing import Callable

from agent_framework import ChatMiddleware, FunctionMiddleware, Message

from ..analysis_result import AnalysisLedger
from ..config import AppConfig
from ..utils import get_logger

logger = get_logger(__name__)


class AnalysisFunctionBudget(FunctionMiddleware):
    def __init__(self, ledger: Callable[[], AnalysisLedger | None]):
        self.ledger = ledger

    async def process(self, context, call_next):
        ledger = self.ledger()
        if ledger is None:
            await call_next()
            return
        name = context.function.name
        with ledger.lock:
            ledger.function_calls += 1
            call_number = ledger.function_calls
            limit = AppConfig.QUERY_ENGINE_MAX_FUNCTION_CALLS
            if ledger.completion is not None:
                context.result = "BLOCKED: Analysis already finalized."
                return
            if call_number > limit or (call_number == limit and name != "complete_analysis"):
                ledger.budget_exhausted = True
                context.result = "BLOCKED: No execution budget remains; analysis is incomplete."
                return
        logger.info("analysis request=%s call=%s tool=%s stage=invoked", ledger.trace, call_number, name)
        await call_next()
        with ledger.lock:
            if ledger.function_calls >= limit and ledger.completion is None:
                ledger.budget_exhausted = True


class AnalysisChatBudget(ChatMiddleware):
    def __init__(self, ledger: Callable[[], AnalysisLedger | None]):
        self.ledger = ledger

    async def process(self, context, call_next):
        ledger = self.ledger()
        if ledger is None:
            await call_next()
            return
        ledger.model_roundtrips += 1
        remaining = max(0, AppConfig.QUERY_ENGINE_MAX_FUNCTION_CALLS - ledger.function_calls)
        rounds = max(0, AppConfig.QUERY_ENGINE_MAX_MODEL_ROUNDTRIPS - ledger.model_roundtrips + 1)
        options = dict(context.options or {})
        disabled = options.get("tool_choice") == "none"
        if ledger.completion is not None:
            options["tool_choice"] = "none"
        elif disabled or not remaining or not rounds:
            ledger.budget_exhausted = True
            options["tool_choice"] = "none"
        elif remaining == 1 or rounds == 1:
            # Never re-enable tools after MAF has disabled them.
            options["tools"] = [
                tool for tool in options.get("tools", [])
                if getattr(tool, "name", None) == "complete_analysis"
            ]
            if options["tools"]:
                options["tool_choice"] = "required"
        context.options = options
        context.messages = [*context.messages, Message("system", [
            f"Analysis budget: remaining_function_calls={remaining}; remaining_model_roundtrips={rounds}. "
            "All skills and completion count toward this budget. "
            + ("Stop exploration. Run only essential analysis/diagnosis then complete_analysis, or report gaps. "
               if remaining <= 2 or rounds <= 2 else "")
            + "Before tools are exhausted, submit complete_analysis with evidence or explicit insufficiency."
        ])]
        logger.info("analysis request=%s round=%s remaining_calls=%s tools_disabled=%s",
                    ledger.trace, ledger.model_roundtrips, remaining, disabled)
        await call_next()
