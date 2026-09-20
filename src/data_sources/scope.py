"""Dialect-aware read-only scope validation for agent-generated SQL.

Relocated from src/agents/data_insight_agent.py._validate_sql_scope and
parameterised by ScopeRules: the databricks branch reproduces the previous
behaviour byte-for-byte; the mysql branch enforces two-part database.table
names against the MYSQL_DATABASES allowlist.
"""

from __future__ import annotations

from typing import Optional

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .base import ScopeRules


def validate_sql_scope(sql: str, rules: ScopeRules) -> Optional[str]:
    """Return a blocking error when SQL references an unexposed object."""
    try:
        statements = [
            statement for statement in parse(sql, read=rules.sqlglot_dialect)
            if statement
        ]
    except ParseError as exc:
        return f"BLOCKED: SQL could not be parsed for catalog/schema validation: {exc}"
    if len(statements) != 1:
        return "BLOCKED: Exactly one SQL statement is permitted."

    statement = statements[0]
    cte_names = {
        cte.alias_or_name.casefold()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }

    if rules.require_catalog:
        return _validate_catalog_scope(statement, rules, cte_names)
    return _validate_database_scope(statement, rules, cte_names)


def _validate_catalog_scope(
    statement: exp.Expression,
    rules: ScopeRules,
    cte_names: set[str],
) -> Optional[str]:
    """Databricks: every physical table must be catalog.schema.table inside the allowlist."""
    configured_catalog = rules.catalog
    configured_schemas = {
        schema.casefold(): schema for schema in rules.schemas
    }

    for table in statement.find_all(exp.Table):
        table_name = table.name
        if (
            not table.catalog
            and not table.db
            and table_name.casefold() in cte_names
        ):
            continue
        if not table.catalog or not table.db:
            return (
                f"BLOCKED: Physical table '{table.sql(dialect=rules.sqlglot_dialect)}' must use a "
                "fully-qualified configured catalog.schema.table name."
            )
        if table.catalog.casefold() != configured_catalog.casefold():
            return (
                f"BLOCKED: Catalog '{table.catalog}' is outside the configured catalog "
                f"'{configured_catalog}'."
            )
        if table.db.casefold() not in configured_schemas:
            return (
                f"BLOCKED: Schema '{table.db}' is outside DATABRICKS_SCHEMAS "
                f"({', '.join(rules.schemas)})."
            )
    return None


def _validate_database_scope(
    statement: exp.Expression,
    rules: ScopeRules,
    cte_names: set[str],
) -> Optional[str]:
    """MySQL: every physical table must be database.table inside the allowlist."""
    configured_databases = {
        database.casefold(): database for database in rules.schemas
    }

    for table in statement.find_all(exp.Table):
        table_name = table.name
        if (
            not table.catalog
            and not table.db
            and table_name.casefold() in cte_names
        ):
            continue
        if table.catalog:
            return (
                f"BLOCKED: Physical table '{table.sql(dialect=rules.sqlglot_dialect)}' uses a "
                "three-part catalog.schema.table name, but MySQL has no catalog layer; "
                "use a fully-qualified database.table name."
            )
        if not table.db:
            return (
                f"BLOCKED: Physical table '{table.sql(dialect=rules.sqlglot_dialect)}' must use a "
                "fully-qualified configured database.table name."
            )
        if table.db.casefold() not in configured_databases:
            return (
                f"BLOCKED: Database '{table.db}' is outside MYSQL_DATABASES "
                f"({', '.join(rules.schemas)})."
            )
    return None
