"""Offline regressions for evidence handoff and analytical completion."""

import json
import asyncio
from contextvars import ContextVar
from types import SimpleNamespace
import sqlite3

import pytest
from agent_framework import Message

from src.analysis_result import AnalysisLedger, EvidenceReference, contains_tool_protocol, sql_fingerprint
from src.agents.analysis_budget import AnalysisChatBudget, AnalysisFunctionBudget
from src.config import AppConfig
from src.data_sources.base import QueryResult, ScopeRules

from src.agents.data_insight_agent import DataInsightAgent
from src.agents.ontology_agent import OntologyAgent
from src.ontology.evidence import usable_ontology_context


def context_result(status="ok", root="SyntheticCharge", confidence=0.8):
    return {"status": status, "confidence": confidence, "data": {"root_entity": root}}


def test_no_match_dictionary_is_not_ready():
    context = {"status": "ok", "primary_business_context": context_result("no_match", None, 0)}
    assert not usable_ontology_context(context)
    assert DataInsightAgent._ontology_context_status(
        json.dumps(context), ontology_enabled=True, ontology_fallback=""
    ) == "incomplete"


def test_recovery_selects_strongest_latest_evidence_and_retains_failed_lookup():
    payloads = [context_result("no_match", None, 0), context_result(root="Old"), context_result(root="New")]
    payloads[0]["unresolved"] = ["billing rule unavailable"]
    results = [{"tool": "get_business_context", "arguments": {"question": "charges"}, "result": p} for p in payloads]
    context = json.loads(OntologyAgent.build_collected_context("", results))
    assert context["primary_business_context"]["data"]["root_entity"] == "New"
    assert context["semantic_summary"]["unresolved"] == ["billing rule unavailable"]
    assert len(context["additional_tool_results"]) == 2
    assert usable_ontology_context(context)


def test_empty_success_does_not_supply_evidence():
    for payload in ({}, {"status": "ok", "data": {}}, context_result("error")):
        context = json.loads(OntologyAgent.build_collected_context("invented summary", [
            {"tool": "get_business_context", "result": payload}
        ]))
        assert context["status"] == "no_match"
        assert not usable_ontology_context(context)


def add_result(ledger, purpose="analysis", truncated=False):
    record = ledger.record(purpose=purpose, fingerprint="test", row_count=1,
                           truncated=truncated, requires_follow_up=False)
    return EvidenceReference(result_id=record.result_id, claim="A measured finding")


@pytest.mark.parametrize("case", ["exploration", "foreign", "pending", "gaps", "truncated", "empty", "dsml", "budget"])
def test_completed_rejects_unsupported_evidence(case):
    ledger = AnalysisLedger()
    evidence = [add_result(ledger, purpose="exploration" if case == "exploration" else "analysis",
                           truncated=case == "truncated")]
    if case == "foreign":
        evidence = [add_result(AnalysisLedger())]
    ledger.budget_exhausted = case == "budget"
    answer = "" if case == "empty" else '<｜DSML｜tool_calls>' if case == "dsml" else "Finding"
    result = ledger.complete("completed", answer, evidence, ["missing rule"] if case == "gaps" else [],
                             pending_diagnostic=case == "pending")
    assert result.startswith("BLOCKED:")
    assert ledger.finish().status == "failed"


def test_partial_and_insufficient_require_visible_gaps():
    ledger = AnalysisLedger()
    evidence = [add_result(ledger, purpose="exploration")]
    assert ledger.complete("partial", "Lead", evidence, [], pending_diagnostic=False).startswith("BLOCKED:")
    assert ledger.complete("partial", "Lead", evidence, ["Rule unavailable"], pending_diagnostic=False).startswith("Completion accepted")
    assert "Rule unavailable" in ledger.finish().answer
    insufficient = AnalysisLedger()
    insufficient.complete("insufficient", "Cannot decide", [], ["Encounter key missing"], pending_diagnostic=False)
    assert insufficient.finish().status == "insufficient"


def test_capped_results_can_only_support_explicit_limited_scope():
    ledger = AnalysisLedger()
    evidence = [add_result(ledger, truncated=True)]
    evidence[0].scope = "limited"
    assert ledger.complete("completed", "Sample finding", evidence, [], pending_diagnostic=False).startswith("BLOCKED:")
    evidence[0].scope_description = "Only the first 100 returned rows"
    assert ledger.complete("completed", "Sample finding", evidence, [], pending_diagnostic=False).startswith("Completion accepted")
    assert "first 100" in ledger.finish().answer


def test_sql_fingerprint_preserves_literals_identifiers_purpose_and_limit():
    args = dict(source="mysql:test", dialect="mysql", max_rows=100, purpose="analysis")
    first = sql_fingerprint("SELECT 'A' FROM test.charges", **args)
    assert first == sql_fingerprint("SELECT  'A'  FROM test.charges -- comment", **args)
    for sql in ("SELECT 'a' FROM test.charges", "SELECT 'A' FROM test.Charges"):
        assert first != sql_fingerprint(sql, **args)
    assert first != sql_fingerprint("SELECT 'A' FROM test.charges", **{**args, "purpose": "exploration"})
    assert first != sql_fingerprint("SELECT 'A' FROM test.charges", **{**args, "max_rows": 20})
    assert sql_fingerprint("SELECT 'broken", **args) is None


@pytest.fixture
def synthetic_sql(monkeypatch):
    """Synthetic billing only: A includes B in the same encounter by TEST rule."""
    import src.agents.data_insight_agent as module
    import src.data_sources as sources
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.execute("ATTACH DATABASE ':memory:' AS test")
    connection.execute("CREATE TABLE test.charges (encounter TEXT, code TEXT, amount INTEGER)")
    calls = []
    rules = ScopeRules("mysql", "", ["test"], False, 2, "test.charges", "Synthetic")

    def execute_query(sql, *, max_rows):
        calls.append(sql)
        cursor = connection.execute(sql)
        rows = [list(row) for row in cursor.fetchmany(max_rows)]
        return QueryResult([item[0] for item in cursor.description], rows, len(rows), sql)

    source = SimpleNamespace(name="mysql", execute_query=execute_query)
    monkeypatch.setattr(module, "get_active_data_source", lambda: source)
    monkeypatch.setattr(module, "get_scope_rules", lambda: rules)
    monkeypatch.setattr(sources, "get_scope_rules", lambda: rules)
    monkeypatch.setattr(module, "skill_was_loaded", lambda _: True)
    agent = DataInsightAgent.__new__(DataInsightAgent)
    agent.metadata_agent = None
    agent.ontology_agent = None
    agent._recovery_state = ContextVar("synthetic-analysis", default=None)
    state, _ = agent._prepare_contextual_question("Find duplicate test charges", schema_context="synthetic",
        ontology_context="", ontology_fallback="", ontology_enabled=False)
    token = agent._recovery_state.set(state)
    tools = {tool.__name__: tool for tool in agent._create_tools()}
    try:
        yield connection, calls, agent, state, tools
    finally:
        agent._recovery_state.reset(token)
        connection.close()


@pytest.mark.parametrize("rows,expected", [
    ([("e1", "A", 100), ("e1", "B", 20)], 1),
    ([("e1", "A", 100), ("e1", "C", 20)], 0),
    ([("e1", "A", 100), ("e2", "B", 20)], 0),
    ([("e1", "A", 100), ("e1", "B", 20), ("e1", "B", -20)], 0),
])
def test_synthetic_encounter_and_refund_rule(synthetic_sql, rows, expected):
    connection, _, _, state, tools = synthetic_sql
    connection.executemany("INSERT INTO test.charges VALUES (?, ?, ?)", rows)
    sql = """SELECT COUNT(*) AS matches FROM (
        SELECT encounter FROM test.charges GROUP BY encounter
        HAVING SUM(CASE WHEN code='A' THEN amount ELSE 0 END)>0
           AND SUM(CASE WHEN code='B' THEN amount ELSE 0 END)>0
    ) AS encounters"""
    result = tools["execute_sql"](sql)
    assert f"| {expected} |" in result
    evidence = [EvidenceReference(result_id=next(iter(state.ledger.executions)), claim="TEST rule count by encounter")]
    if state.pending_diagnostic is not None:
        tools["execute_sql"]("SELECT COUNT(*) AS source_rows FROM test.charges", purpose="diagnostic")
    accepted = tools["complete_analysis"]("completed", f"Synthetic matches: {expected}", evidence, [])
    assert accepted.startswith("Completion accepted")
    assert state.ledger.finish().status == "completed"


def test_unknown_code_and_absent_rule_are_partial_not_violation(synthetic_sql):
    connection, _, _, state, tools = synthetic_sql
    connection.execute("INSERT INTO test.charges VALUES ('e1', 'UNKNOWN', 10)")
    tools["execute_sql"]("SELECT code FROM test.charges WHERE code NOT IN ('A','B')", purpose="exploration")
    reference = EvidenceReference(result_id=next(iter(state.ledger.executions)), claim="Unmapped code exists")
    assert tools["complete_analysis"]("completed", "Duplicate", [reference], []).startswith("BLOCKED:")
    tools["complete_analysis"]("partial", "Unmapped charge found", [reference], ["No authoritative mapping or rule for UNKNOWN"])
    assert state.ledger.finish().status == "partial"


def test_empty_exploration_and_successful_query_reuse_are_request_local(synthetic_sql):
    _, calls, agent, state, tools = synthetic_sql
    sql = "SELECT code FROM test.charges WHERE code='MISSING'"
    first = tools["execute_sql"](sql, purpose="exploration")
    assert state.pending_diagnostic is None
    assert tools["execute_sql"](sql, purpose="exploration") == first
    assert len(calls) == 1
    assert len(state.ledger.executions) == 1
    other, _ = agent._prepare_contextual_question("other", schema_context="synthetic", ontology_context="",
        ontology_fallback="", ontology_enabled=False)
    token = agent._recovery_state.set(other)
    try:
        tools["execute_sql"](sql, purpose="exploration")
        assert len(calls) == 2
        assert set(state.ledger.executions).isdisjoint(other.ledger.executions)
    finally:
        agent._recovery_state.reset(token)


def test_blocked_and_failed_sql_produce_no_evidence_or_reuse(synthetic_sql):
    _, calls, _, state, tools = synthetic_sql
    assert tools["execute_sql"]("DELETE FROM test.charges").startswith("BLOCKED:")
    assert not calls
    for _ in range(2):
        assert tools["execute_sql"]("SELECT missing FROM test.charges").startswith("Query execution failed")
    assert len(calls) == 2
    assert not state.ledger.executions
    assert not state.ledger.reusable


@pytest.mark.asyncio
async def test_budget_counts_skills_and_reserves_completion_without_reenabling(monkeypatch):
    monkeypatch.setattr(AppConfig, "QUERY_ENGINE_MAX_FUNCTION_CALLS", 3)
    ledger = AnalysisLedger()
    function_budget = AnalysisFunctionBudget(lambda: ledger)
    chat_budget = AnalysisChatBudget(lambda: ledger)
    async def proceed():
        pass
    for name in ("load_skill", "execute_sql"):
        await function_budget.process(SimpleNamespace(function=SimpleNamespace(name=name), result=None), proceed)
    tools = [SimpleNamespace(name="execute_sql"), SimpleNamespace(name="complete_analysis")]
    context = SimpleNamespace(options={"tools": tools}, messages=[Message("user", ["question"])])
    await chat_budget.process(context, proceed)
    assert ledger.function_calls == 2
    assert [t.name for t in context.options["tools"]] == ["complete_analysis"]
    assert context.options["tool_choice"] == "required"
    assert "remaining_function_calls=1" in context.messages[-1].text
    disabled = SimpleNamespace(options={"tools": tools, "tool_choice": "none"}, messages=[])
    await chat_budget.process(disabled, proceed)
    assert disabled.options["tool_choice"] == "none"
    assert ledger.finish().reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_budget_blocks_parallel_batch_overshoot(monkeypatch):
    monkeypatch.setattr(AppConfig, "QUERY_ENGINE_MAX_FUNCTION_CALLS", 2)
    ledger = AnalysisLedger()
    middleware = AnalysisFunctionBudget(lambda: ledger)
    executed = []
    async def call():
        executed.append(True)
    contexts = [SimpleNamespace(function=SimpleNamespace(name="execute_sql"), result=None) for _ in range(3)]
    await asyncio.gather(*(middleware.process(context, call) for context in contexts))
    assert len(executed) == 1
    assert contexts[-1].result.startswith("BLOCKED:")


def test_protocol_detection_across_chunks():
    text = "".join(["<｜D", "SM", "L｜tool_", "calls>"])
    assert contains_tool_protocol(text)
    assert AnalysisLedger().finish(text).reason == "invalid_tool_output"


@pytest.mark.asyncio
async def test_real_maf_stream_honors_budget_and_typed_completion(synthetic_sql, monkeypatch):
    """Exercise installed MAF and OpenAI parsing without network or a model."""
    from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
    from src.agents.maf_runtime import CompatibleOpenAIChatCompletionClient
    connection, _, agent, _, tools = synthetic_sql
    connection.execute("INSERT INTO test.charges VALUES ('e1', 'A', 10)")
    monkeypatch.setattr(AppConfig, "QUERY_ENGINE_MAX_FUNCTION_CALLS", 3)
    monkeypatch.setattr(AppConfig, "QUERY_ENGINE_MAX_MODEL_ROUNDTRIPS", 8)
    client = CompatibleOpenAIChatCompletionClient(model="offline", api_key="test", base_url="http://invalid",
        function_invocation_configuration={"enabled": True, "max_iterations": 8, "max_function_calls": 3})
    requests = []

    def load_skill() -> str:
        """Synthetic native-skill stand-in; must consume budget too."""
        return "loaded"

    async def fake_create(**kwargs):
        requests.append(kwargs)
        number = len(requests)
        if number == 1:
            name, arguments = "load_skill", {}
        elif number == 2:
            name, arguments = "execute_sql", {"sql": "SELECT COUNT(*) AS n FROM test.charges"}
        elif number == 3:
            assert [tool["function"]["name"] for tool in kwargs["tools"]] == ["complete_analysis"]
            ledger = agent._recovery_state.get().ledger
            name, arguments = "complete_analysis", {
                "status": "completed", "answer": "One synthetic charge",
                "evidence": [{"result_id": next(iter(ledger.executions)), "claim": "Total count is one"}], "gaps": [],
            }
        else:
            assert kwargs["tool_choice"] == "none"
            name, arguments = "", {}

        async def chunks():
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"c{number}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(arguments)}}]} if name else {"role": "assistant", "content": "untrusted trailing text"}
            yield ChatCompletionChunk.model_validate({"id": f"r{number}", "created": 1, "model": "offline",
                "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
            yield ChatCompletionChunk.model_validate({"id": f"r{number}", "created": 1, "model": "offline",
                "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if name else "stop"}]})
        return chunks()

    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)
    ledger_getter = lambda: agent._recovery_state.get().ledger
    agent.agent = client.as_agent(name="OfflineAnalysis", instructions="test", tools=[load_skill, *tools.values()],
        middleware=[AnalysisFunctionBudget(ledger_getter), AnalysisChatBudget(ledger_getter)])
    sink = []
    async for _ in agent.query_stream("Count charges", schema_context="synthetic", result_sink=sink):
        pass
    assert sink[0].status == "completed"
    assert sink[0].answer == "One synthetic charge"
    assert len(requests) == 4


@pytest.mark.asyncio
async def test_concurrent_analysis_results_and_ids_are_isolated(monkeypatch):
    agent = DataInsightAgent.__new__(DataInsightAgent)
    agent.agent = object()
    agent._recovery_state = ContextVar("concurrent-analysis", default=None)
    observed = {}

    def stream(_agent, question, session=None):
        async def updates():
            state = agent._recovery_state.get()
            ref = add_result(state.ledger)
            observed[state.question] = ref
            await asyncio.sleep(0)
            assert agent._recovery_state.get() is state
            other_refs = [value for key, value in observed.items() if key != state.question]
            for other_ref in other_refs:
                assert state.ledger.complete("completed", "wrong", [other_ref], [], pending_diagnostic=False).startswith("BLOCKED:")
            state.ledger.complete("completed", state.question, [ref], [], pending_diagnostic=False)
            yield SimpleNamespace(text="untrusted", contents=[])
        return updates()

    monkeypatch.setattr("src.agents.data_insight_agent.stream_agent", stream)
    async def run(question):
        sink = []
        async for _ in agent.query_stream(question, thread=object(), result_sink=sink):
            pass
        return sink[0]
    a, b = await asyncio.gather(run("first"), run("second"))
    assert a.answer == "first" and b.answer == "second"
    assert a.evidence[0].result_id != b.evidence[0].result_id
    assert agent._recovery_state.get() is None
