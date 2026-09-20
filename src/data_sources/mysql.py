"""MySQL backend: SQLAlchemy Core engine (PyMySQL driver) + information_schema metadata.

Design notes
------------
* One process-wide engine owned by MySQLDataSource; SQLAlchemy's QueuePool manages
  concurrency, pre-ping health checks, and recycling — unlike the Databricks path
  (single connection + global lock), concurrent session queries run in parallel.
* Every pooled connection opens with `SET SESSION TRANSACTION READ ONLY` as
  defence-in-depth on top of the keyword blacklist and scope validation.
* MetadataProvider reads information_schema with bound parameters; no SHOW/DESCRIBE
  fallback is needed because SQL *is* the primary channel here.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from ..config import DataSourcePolicyConfig, MySQLConfig
from ..utils import get_logger
from .base import QueryResult, ScopeRules

logger = get_logger(__name__)

READ_ONLY_INIT_COMMAND = "SET SESSION TRANSACTION READ ONLY"


class MySQLDataSource:
    """Read-only SQL execution against the configured MySQL server."""

    name = "mysql"

    def __init__(self) -> None:
        self._engine: Optional[Any] = None
        self._engine_lock = threading.Lock()

    def is_configured(self) -> bool:
        return MySQLConfig.is_configured()

    def scope_rules(self) -> ScopeRules:
        return ScopeRules(
            sqlglot_dialect="mysql",
            catalog="",
            schemas=list(MySQLConfig.DATABASES),
            require_catalog=False,
            qualified_parts=2,
            naming_example="database.table",
            display_name="MySQL",
        )

    def engine(self) -> Any:
        """Return the lazily constructed process-wide SQLAlchemy engine."""
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is None:
                if not MySQLConfig.is_configured():
                    raise RuntimeError(
                        "MySQL connection is not configured. "
                        "Set MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, and "
                        "MYSQL_DATABASES in .env."
                    )
                try:
                    from sqlalchemy import create_engine
                    from sqlalchemy.engine import URL
                except ImportError as exc:
                    raise RuntimeError(
                        "SQLAlchemy is not installed. Run: pip install SQLAlchemy PyMySQL"
                    ) from exc

                url = URL.create(
                    "mysql+pymysql",
                    username=MySQLConfig.USER,
                    password=MySQLConfig.PASSWORD,
                    host=MySQLConfig.HOST,
                    port=MySQLConfig.PORT,
                    query={"charset": MySQLConfig.CHARSET},
                )
                self._engine = create_engine(
                    url,
                    pool_size=MySQLConfig.POOL_SIZE,
                    max_overflow=MySQLConfig.POOL_MAX_OVERFLOW,
                    pool_pre_ping=True,
                    pool_recycle=MySQLConfig.POOL_RECYCLE_SECONDS,
                    pool_timeout=DataSourcePolicyConfig.QUERY_TIMEOUT,
                    connect_args={
                        "read_timeout": DataSourcePolicyConfig.QUERY_TIMEOUT,
                        "init_command": READ_ONLY_INIT_COMMAND,
                    },
                )
                logger.info(
                    "MySQL engine created (pool_size=%s, max_overflow=%s, recycle=%ss).",
                    MySQLConfig.POOL_SIZE,
                    MySQLConfig.POOL_MAX_OVERFLOW,
                    MySQLConfig.POOL_RECYCLE_SECONDS,
                )
            return self._engine

    def execute_query(self, sql: str, *, max_rows: int = 500) -> QueryResult:
        """Execute *sql* on a pooled connection and cap the returned rows."""
        from sqlalchemy import text

        with self.engine().connect() as connection:
            result = connection.execute(text(sql))
            raw_rows = result.fetchmany(max_rows)
            columns = list(result.keys())
            rows = [list(row) for row in raw_rows]
            return QueryResult(
                columns=columns, rows=rows, row_count=len(rows), sql=sql
            )


class MySQLMetadataProvider:
    """information_schema-backed metadata for the allowlisted databases."""

    def __init__(self, data_source: Optional[MySQLDataSource] = None) -> None:
        self._data_source = data_source or MySQLDataSource()

    def _fetchall(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        from sqlalchemy import text

        with self._data_source.engine().connect() as connection:
            result = connection.execute(text(sql), params)
            columns = list(result.keys())
            return [dict(zip(columns, row)) for row in result.fetchall()]

    def list_schemas(self, *, catalog: str = "") -> list[str]:
        """Return allowlisted databases that actually exist on the server."""
        databases = list(MySQLConfig.DATABASES)
        if not databases:
            return []
        placeholders = ", ".join(f":db_{index}" for index in range(len(databases)))
        params = {f"db_{index}": db for index, db in enumerate(databases)}
        rows = self._fetchall(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
            f"WHERE SCHEMA_NAME IN ({placeholders})",
            params,
        )
        schemas = [str(row["SCHEMA_NAME"]) for row in rows if row.get("SCHEMA_NAME")]
        schemas.sort(key=str.casefold)
        return schemas

    def list_tables(self, *, schema: str, catalog: str = "") -> list[dict[str, Any]]:
        if not schema:
            raise ValueError("schema is required when listing tables")
        rows = self._fetchall(
            "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = :schema",
            {"schema": schema},
        )
        tables = [
            {
                "name": str(row["TABLE_NAME"]),
                "full_name": f"{schema}.{row['TABLE_NAME']}",
                "schema": schema,
                "table_type": str(row.get("TABLE_TYPE") or ""),
                "comment": str(row.get("TABLE_COMMENT") or ""),
            }
            for row in rows
            if row.get("TABLE_NAME")
        ]
        tables.sort(key=lambda table: table["full_name"].casefold())
        return tables

    def get_table(
        self,
        *,
        catalog: str,
        schema: str,
        table: str,
    ) -> Optional[dict[str, Any]]:
        schema = schema or MySQLConfig.DATABASE
        table_rows = self._fetchall(
            "SELECT TABLE_TYPE, TABLE_COMMENT "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table",
            {"schema": schema, "table": table},
        )
        if not table_rows:
            return None

        column_rows = self._fetchall(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
            "ORDER BY ORDINAL_POSITION",
            {"schema": schema, "table": table},
        )
        if not column_rows:
            return None

        full_name = f"{schema}.{table}"
        return {
            "name": table,
            "full_name": full_name,
            "schema": schema,
            "table_type": str(table_rows[0].get("TABLE_TYPE") or ""),
            "comment": str(table_rows[0].get("TABLE_COMMENT") or ""),
            "columns": [
                {
                    "name": str(row["COLUMN_NAME"]),
                    "type": str(row["COLUMN_TYPE"]),
                    "nullable": str(row.get("IS_NULLABLE") or "").upper() == "YES",
                    "comment": str(row.get("COLUMN_COMMENT") or ""),
                }
                for row in column_rows
                if row.get("COLUMN_NAME")
            ],
        }
