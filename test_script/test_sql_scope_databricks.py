"""Databricks scope-validation regression anchor (behavior must not change)."""

from __future__ import annotations

from src.data_sources.base import ScopeRules
from src.data_sources.scope import validate_sql_scope


def _rules() -> ScopeRules:
    return ScopeRules(
        sqlglot_dialect="databricks",
        catalog="cat",
        schemas=["silver", "gold"],
        require_catalog=True,
        qualified_parts=3,
        naming_example="catalog.schema.table",
        display_name="Azure Databricks",
    )


def test_three_part_name_inside_catalog_and_schema_passes() -> None:
    assert validate_sql_scope("SELECT * FROM cat.silver.t", _rules()) is None


def test_bare_or_two_part_name_is_blocked() -> None:
    error = validate_sql_scope("SELECT * FROM t", _rules())
    assert error is not None and error.startswith(
        "BLOCKED: Physical table"
    ) and "fully-qualified configured catalog.schema.table" in error

    assert validate_sql_scope("SELECT * FROM silver.t", _rules()) is not None


def test_foreign_catalog_is_blocked() -> None:
    error = validate_sql_scope("SELECT * FROM other.silver.t", _rules())
    assert error is not None and "outside the configured catalog" in error


def test_schema_outside_allowlist_is_blocked() -> None:
    error = validate_sql_scope("SELECT * FROM cat.bronze.t", _rules())
    assert error is not None and "outside DATABRICKS_SCHEMAS" in error


def test_cte_names_are_exempt_from_qualification() -> None:
    sql = "WITH top_buyers AS (SELECT 1 AS x) SELECT * FROM top_buyers"
    assert validate_sql_scope(sql, _rules()) is None


def test_multiple_statements_are_blocked() -> None:
    error = validate_sql_scope("SELECT 1; SELECT 2", _rules())
    assert error == "BLOCKED: Exactly one SQL statement is permitted."


def test_unparsable_sql_is_blocked() -> None:
    error = validate_sql_scope("SELEC FROM WHERE", _rules())
    assert error is not None and error.startswith("BLOCKED: SQL could not be parsed")
