"""Data-source factory tests: type resolution, fallback, and singleton reuse."""

from __future__ import annotations

import pytest

import src.data_sources as ds
from src.config import DataSourceConfig, MySQLConfig
from src.data_sources.base import QueryResult, ScopeRules


@pytest.fixture(autouse=True)
def _reset_factory():
    ds._active_source = None
    ds._active_provider = None
    yield
    ds._active_source = None
    ds._active_provider = None


def test_defaults_to_databricks_when_unset() -> None:
    assert ds.get_active_data_source().name == "databricks"
    assert ds.get_active_metadata_provider().list_tables  # provider constructed


def test_unknown_type_falls_back_to_databricks_with_warning(caplog) -> None:
    import logging

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(DataSourceConfig, "RAW_TYPE", "oracle")
        patch.setattr(DataSourceConfig, "TYPE", "databricks")
        with caplog.at_level(logging.WARNING):
            assert ds._resolve_type() == "databricks"
    assert any("DATA_SOURCE_TYPE" in message for message in caplog.messages)


def test_mysql_type_constructs_mysql_source() -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(DataSourceConfig, "RAW_TYPE", "mysql")
        patch.setattr(DataSourceConfig, "TYPE", "mysql")
        source = ds.get_active_data_source()
    assert source.name == "mysql"
    rules = source.scope_rules()
    assert rules.sqlglot_dialect == "mysql"
    assert rules.require_catalog is False
    assert rules.qualified_parts == 2
    assert rules.catalog == ""
    assert rules.schemas == list(MySQLConfig.DATABASES)


def test_mysql_configuration_requires_password() -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(MySQLConfig, "HOST", "localhost")
        patch.setattr(MySQLConfig, "USER", "reader")
        patch.setattr(MySQLConfig, "PASSWORD", "")
        patch.setattr(MySQLConfig, "DATABASES", ["sales"])
        assert MySQLConfig.is_configured() is False
        patch.setattr(MySQLConfig, "PASSWORD", "secret")
        assert MySQLConfig.is_configured() is True


def test_factory_returns_process_wide_singletons() -> None:
    assert ds.get_active_data_source() is ds.get_active_data_source()
    assert ds.get_active_metadata_provider() is ds.get_active_metadata_provider()


def test_mysql_provider_reuses_active_source_and_engine_owner() -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(DataSourceConfig, "RAW_TYPE", "mysql")
        patch.setattr(DataSourceConfig, "TYPE", "mysql")
        source = ds.get_active_data_source()
        provider = ds.get_active_metadata_provider()
    assert provider._data_source is source


def test_query_result_shape_matches_historical_contract() -> None:
    result = QueryResult(
        columns=["a"], rows=[[1]], row_count=1, sql="SELECT 1"
    )
    assert result.columns == ["a"]
    assert result.rows == [[1]]
    assert result.row_count == 1
    assert result.sql == "SELECT 1"


def test_scope_rules_default_schema_is_first_allowlist_entry() -> None:
    rules = ScopeRules(
        sqlglot_dialect="mysql",
        catalog="",
        schemas=["db_one", "db_two"],
        require_catalog=False,
        qualified_parts=2,
        naming_example="database.table",
        display_name="MySQL",
    )
    assert rules.default_schema == "db_one"
