"""MySQL scope-validation tests: two-part names against MYSQL_DATABASES."""

from __future__ import annotations

from src.data_sources.base import ScopeRules
from src.data_sources.scope import validate_sql_scope


def _rules() -> ScopeRules:
    return ScopeRules(
        sqlglot_dialect="mysql",
        catalog="",
        schemas=["aw_test", "reporting"],
        require_catalog=False,
        qualified_parts=2,
        naming_example="database.table",
        display_name="MySQL",
    )


def test_two_part_name_inside_allowlist_passes() -> None:
    assert validate_sql_scope("SELECT * FROM aw_test.orders", _rules()) is None


def test_backquoted_two_part_name_passes() -> None:
    assert (
        validate_sql_scope("SELECT * FROM `aw_test`.`orders`", _rules()) is None
    )


def test_bare_table_name_is_blocked() -> None:
    error = validate_sql_scope("SELECT * FROM orders", _rules())
    assert error is not None and "fully-qualified configured database.table" in error


def test_three_part_name_is_blocked_because_mysql_has_no_catalog() -> None:
    error = validate_sql_scope("SELECT * FROM cat.aw_test.orders", _rules())
    assert error is not None and "MySQL has no catalog layer" in error


def test_database_outside_allowlist_is_blocked() -> None:
    error = validate_sql_scope("SELECT * FROM mysql.user", _rules())
    assert error is not None and "outside MYSQL_DATABASES" in error


def test_allowlist_match_is_case_insensitive() -> None:
    assert validate_sql_scope("SELECT * FROM AW_TEST.orders", _rules()) is None


def test_cte_names_are_exempt_from_qualification() -> None:
    sql = "WITH recent AS (SELECT 1 AS n) SELECT * FROM recent"
    assert validate_sql_scope(sql, _rules()) is None


def test_joins_validate_every_table() -> None:
    ok = (
        "SELECT o.id FROM aw_test.orders o "
        "JOIN aw_test.customers c ON o.customer_id = c.id"
    )
    assert validate_sql_scope(ok, _rules()) is None

    bad = (
        "SELECT o.id FROM aw_test.orders o "
        "JOIN reporting.customers c ON o.customer_id = c.id "
        "JOIN secret.audit a ON a.id = c.id"
    )
    error = validate_sql_scope(bad, _rules())
    assert error is not None and "secret" in error


def test_multiple_statements_are_blocked() -> None:
    error = validate_sql_scope("SELECT 1; SELECT 2", _rules())
    assert error == "BLOCKED: Exactly one SQL statement is permitted."
