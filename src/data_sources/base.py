"""Shared data-source contracts: query execution, metadata access, and scope rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class QueryResult:
    """Uniform payload for every executed read-only query."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    sql: str


@dataclass(frozen=True)
class ScopeRules:
    """Per-source rules that parameterise SQL validation and name completion.

    catalog      — UC catalog for Databricks; always "" for MySQL (no catalog layer).
    schemas      — authoritative allowlist (DATABRICKS_SCHEMAS / MYSQL_DATABASES).
    qualified_parts — 3 for catalog.schema.table, 2 for database.table.
    """

    sqlglot_dialect: str
    catalog: str
    schemas: list[str]
    require_catalog: bool
    qualified_parts: int
    naming_example: str
    display_name: str

    @property
    def default_schema(self) -> str:
        return self.schemas[0] if self.schemas else ""


@runtime_checkable
class DataSource(Protocol):
    """Read-only SQL execution against one backend."""

    name: str

    def is_configured(self) -> bool: ...

    def execute_query(self, sql: str, *, max_rows: int = 500) -> QueryResult: ...

    def scope_rules(self) -> ScopeRules: ...


@runtime_checkable
class MetadataProvider(Protocol):
    """Raw metadata fetching (no caching; caching lives in MetadataCatalogService)."""

    def list_schemas(self, *, catalog: str = "") -> list[str]: ...

    def list_tables(self, *, schema: str, catalog: str = "") -> list[dict[str, Any]]: ...

    def get_table(
        self,
        *,
        catalog: str,
        schema: str,
        table: str,
    ) -> Optional[dict[str, Any]]: ...
