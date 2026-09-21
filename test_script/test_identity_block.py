"""Identity block tests: databricks blocks stay byte-identical; mysql blocks carry the allowlist."""

from __future__ import annotations

import pytest

import src.data_sources as ds
from src.config import DatabricksConfig, DataSourceConfig, MySQLConfig


@pytest.fixture(autouse=True)
def _reset_factory():
    ds._active_source = None
    ds._active_provider = None
    yield
    ds._active_source = None
    ds._active_provider = None


def test_databricks_data_insight_block_matches_previous_hardcoded_string(monkeypatch) -> None:
    monkeypatch.setattr(DataSourceConfig, "TYPE", "databricks")
    monkeypatch.setattr(DataSourceConfig, "RAW_TYPE", "databricks")
    schemas_list = ", ".join(f"`{s}`" for s in DatabricksConfig.SCHEMAS)
    previous_hardcoded = (
        f"\n\n## Databricks Context\n"
        f"- Catalog: `{DatabricksConfig.CATALOG}`\n"
        f"- Available schemas: {schemas_list}\n"
        f"- Default schema (when unspecified): `{DatabricksConfig.SCHEMA}`\n"
        f"- Always use fully-qualified names: `{DatabricksConfig.CATALOG}.<schema>.<table>`\n"
        f"- Max rows per query: {DatabricksConfig.MAX_ROWS}\n"
        f"- Configured: {DatabricksConfig.is_configured()}\n"
    )
    assert ds.build_identity_block("data_insight") == previous_hardcoded


def test_databricks_metadata_block_matches_previous_hardcoded_string(monkeypatch) -> None:
    monkeypatch.setattr(DataSourceConfig, "TYPE", "databricks")
    monkeypatch.setattr(DataSourceConfig, "RAW_TYPE", "databricks")
    previous_hardcoded = (
        f"\n\n## Databricks Context\n"
        f"- Default catalog: `{DatabricksConfig.CATALOG}`\n"
        f"- Default schema: `{DatabricksConfig.SCHEMA}`\n"
        f"- Exposed schemas (authoritative allowlist): "
        f"{', '.join(f'`{schema}`' for schema in DatabricksConfig.SCHEMAS)}\n"
        f"- Never request or describe a catalog/schema outside this allowlist.\n"
        f"- Configured: {DatabricksConfig.is_configured()}\n"
    )
    assert ds.build_identity_block("metadata") == previous_hardcoded


@pytest.fixture()
def _mysql_active(monkeypatch):
    monkeypatch.setattr(DataSourceConfig, "RAW_TYPE", "mysql")
    monkeypatch.setattr(DataSourceConfig, "TYPE", "mysql")
    monkeypatch.setattr(MySQLConfig, "HOST", "localhost")
    monkeypatch.setattr(MySQLConfig, "USER", "root")
    monkeypatch.setattr(MySQLConfig, "DATABASES", ["aw_test", "reporting"])
    monkeypatch.setattr(MySQLConfig, "DATABASE", "aw_test")


def test_mysql_data_insight_block_carries_allowlist_and_dialect(
    _mysql_active,
) -> None:
    block = ds.build_identity_block("data_insight")
    assert block.startswith("\n\n## MySQL Context\n")
    assert "`aw_test`, `reporting`" in block
    assert "- Default database (when unspecified): `aw_test`" in block
    assert "<database>.<table>" in block
    assert "no QUALIFY clause" in block
    assert "supersedes any static reference to Databricks or Unity Catalog" in block
    assert "verify every candidate against the active MySQL metadata" in block


def test_mysql_metadata_block_carries_allowlist_and_no_catalog_rule(
    _mysql_active,
) -> None:
    block = ds.build_identity_block("metadata")
    assert block.startswith("\n\n## MySQL Context\n")
    assert "- Default database: `aw_test`" in block
    assert "no catalog layer" in block
    assert "Never request or describe a database outside this allowlist." in block
