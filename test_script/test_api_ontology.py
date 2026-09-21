"""FastAPI ontology configuration contract tests."""

from __future__ import annotations

import asyncio
from threading import Event
import pytest
from fastapi import HTTPException
from datetime import datetime, timezone

from src.api import main


def test_mysql_settings_reject_invalid_allowlist() -> None:
    body = main.MySQLSettingsBody(host="localhost", port=3306, user="reader", password="secret", databases="sales,other-db")
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.put_mysql_settings(body))
    assert error.value.status_code == 422


def test_mysql_settings_wait_for_active_query() -> None:
    body = main.MySQLSettingsBody(host="localhost", port=3306, user="reader", password="secret", databases="sales")
    run = main.ActiveRun(run_id="test", cancel_event=Event())
    main.state.active_runs["test"] = run
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(main.put_mysql_settings(body))
        assert error.value.status_code == 409
    finally:
        main.state.active_runs.pop("test", None)


def test_mysql_connection_failure_preserves_runtime_settings(monkeypatch, tmp_path) -> None:
    body = main.MySQLSettingsBody(host="invalid-host", port=3306, user="reader", password="secret", databases="sales")
    old_host = main.MySQLConfig.HOST
    old_type = main.DataSourceConfig.TYPE
    env_file = tmp_path / ".env"
    monkeypatch.setattr(main, "MYSQL_ENV_FILE", env_file)

    def fail_connection(_source):
        raise ConnectionError("unavailable")

    monkeypatch.setattr(main, "_check_mysql_connection", fail_connection)
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.put_mysql_settings(body))
    assert error.value.status_code == 422
    assert main.MySQLConfig.HOST == old_host
    assert main.DataSourceConfig.TYPE == old_type
    assert not env_file.exists()


class _OntologyCapability:
    def health(self):
        return {
            "available": True,
            "reasoner_enabled": True,
            "reasoner": "hermit",
            "reasoning_status": "completed",
            "reasoning_error": None,
            "file_count": 1,
            "ontology_count": 1,
            "entity_count": 10,
        }


def test_chat_request_accepts_session_ontology_override() -> None:
    inherited = main.ChatRequest(message="question")
    disabled = main.ChatRequest(message="question", enable_ontology=False)

    assert inherited.enable_ontology is None
    assert disabled.enable_ontology is False


def test_runtime_config_exposes_default_and_capability_status() -> None:
    original_service = main.state.ontology_service
    original_error = main.state.ontology_error
    try:
        main.state.ontology_service = _OntologyCapability()
        main.state.ontology_error = None
        result = asyncio.run(main.get_runtime_config())
    finally:
        main.state.ontology_service = original_service
        main.state.ontology_error = original_error

    assert result["default_enable_ontology"] is main.AppConfig.DEFAULT_ENABLE_ONTOLOGY
    assert result["ontology"]["available"] is True
    assert result["ontology"]["reasoner"] == "hermit"
    assert "directory" not in result["ontology"]


def test_health_exposes_backward_compatible_data_source_type() -> None:
    result = asyncio.run(main.health_check())
    assert result["data_source_type"] == main.DataSourceConfig.TYPE
    assert result["data_source"]["type"] == main.DataSourceConfig.TYPE


def test_same_session_question_and_ontology_mode_bypass_agents() -> None:
    original_initialized = main.state.initialized
    original_master = main.state.master_agent
    original_threads = main.state.threads
    original_history = main.state.thread_history
    original_runs = main.state.active_runs
    thread_id = "cached-thread"

    class _FailIfCalled:
        def chat_stream(self, *args, **kwargs):
            raise AssertionError("MasterAgent must not run on a response-cache hit")

    try:
        main.state.initialized = True
        main.state.master_agent = _FailIfCalled()
        main.state.threads = {thread_id: object()}
        main.state.active_runs = {}
        main.state.thread_history = {
            thread_id: [
                {
                    "user": "Monthly   Sales",
                    "assistant": "cached answer",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "enable_ontology": True,
                        "cache_eligible": True,
                    "analysis_status": "completed",
                }
            ]
        }

        response = asyncio.run(
            main.chat_stream(
                main.ChatRequest(
                    message=" monthly sales ",
                    thread_id=thread_id,
                    enable_ontology=True,
                )
            )
        )

        assert response.headers["x-cache"] == "HIT"
        assert main._find_cached_response(
            thread_id,
            "monthly sales",
            enable_ontology=False,
        ) is None
    finally:
        main.state.initialized = original_initialized
        main.state.master_agent = original_master
        main.state.threads = original_threads
        main.state.thread_history = original_history
        main.state.active_runs = original_runs
