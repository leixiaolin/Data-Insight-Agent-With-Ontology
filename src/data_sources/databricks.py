"""Azure Databricks backend: SQL warehouse execution + Unity Catalog metadata.

Relocated verbatim from src/agents/data_insight_agent.py (connection handling and
query execution) and src/metadata_catalog.py / src/agents/metadata_agent.py
(Unity Catalog access incl. the SQL-connector fallback), so the Databricks
default path keeps its exact runtime behaviour.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from ..config import DatabricksConfig
from ..utils import get_logger
from .base import QueryResult, ScopeRules

logger = get_logger(__name__)

# ─── Databricks connection singleton (avoids per-query cold-start) ────────────
# Performance note: the biggest latency contributors are:
#   1. Databricks warehouse cold-start (first connect ~3-10 s, warm ~<1 s)
#   2. Ontology routing and MetadataAgent verification model turns
#   3. DataInsightAgent SQL generation and result interpretation
# Reusing the SQL connector connection eliminates the cold-start penalty for subsequent queries.
_db_connection: Optional[Any] = None
# Re-entrant so a query can hold it across acquisition and execution.
_db_lock = threading.RLock()


def _get_db_connection():
    """Return a reusable Databricks SQL connection, creating one if needed."""
    global _db_connection

    if not DatabricksConfig.is_configured():
        raise RuntimeError(
            "Databricks connection is not configured. "
            "Set DATABRICKS_HOST, DATABRICKS_TOKEN, and DATABRICKS_HTTP_PATH in .env."
        )

    try:
        from databricks import sql as dbsql
    except ImportError as exc:
        raise RuntimeError(
            "databricks-sql-connector is not installed. "
            "Run: pip install databricks-sql-connector"
        ) from exc

    with _db_lock:
        # Test existing connection with a lightweight ping
        if _db_connection is not None:
            try:
                cur = _db_connection.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return _db_connection
            except Exception:
                logger.warning("Stale Databricks connection, reconnecting…")
                try:
                    _db_connection.close()
                except Exception:
                    pass
                _db_connection = None

        logger.info("Opening new Databricks SQL connection…")
        _db_connection = dbsql.connect(
            server_hostname=DatabricksConfig.HOST.replace("https://", ""),
            http_path=DatabricksConfig.HTTP_PATH,
            access_token=DatabricksConfig.TOKEN,
            _socket_timeout=DatabricksConfig.QUERY_TIMEOUT,
        )
        return _db_connection


def _is_connection_level_error(exc: BaseException) -> bool:
    """A server-side SQL error leaves the connection usable; anything else may not."""
    try:
        from databricks.sql import exc as dbsql_exc
    except ImportError:
        return True
    return not isinstance(exc, dbsql_exc.ServerOperationError)


class DatabricksDataSource:
    """Read-only SQL execution against the configured Databricks SQL warehouse."""

    name = "databricks"

    def is_configured(self) -> bool:
        return DatabricksConfig.is_configured()

    def scope_rules(self) -> ScopeRules:
        return ScopeRules(
            sqlglot_dialect="databricks",
            catalog=DatabricksConfig.CATALOG,
            schemas=list(DatabricksConfig.SCHEMAS),
            require_catalog=True,
            qualified_parts=3,
            naming_example="catalog.schema.table",
            display_name="Azure Databricks",
        )

    def execute_query(self, sql: str, *, max_rows: int = 500) -> QueryResult:
        """
        Execute *sql* against the configured Databricks SQL warehouse.
        Reuses a persistent connection to avoid per-call cold-start latency.
        """
        global _db_connection

        # One process-wide connection is shared by every session thread, and a DB-API
        # connection is not safe for concurrent cursors, so queries run one at a time.
        with _db_lock:
            connection = _get_db_connection()
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute(sql)
                raw_rows = cursor.fetchmany(max_rows)
                columns = [desc[0] for desc in (cursor.description or [])]
                rows = [list(row) for row in raw_rows]
                return QueryResult(
                    columns=columns, rows=rows, row_count=len(rows), sql=sql
                )
            except Exception as exc:
                if _is_connection_level_error(exc):
                    _db_connection = None
                raise
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:
                        pass


class DatabricksMetadataProvider:
    """Unity Catalog metadata via the SDK, with a SQL-connector recovery path."""

    def __init__(self, data_source: Optional[DatabricksDataSource] = None) -> None:
        self._data_source = data_source or DatabricksDataSource()

    # ── Public interface ────────────────────────────────────────────────────

    def list_schemas(self, *, catalog: str = "") -> list[str]:
        catalog = catalog or DatabricksConfig.CATALOG
        try:
            schemas = [
                schema.name
                for schema in self._workspace_client()
                .schemas.list(catalog_name=catalog)
                if schema.name
            ]
        except Exception as exc:
            logger.warning(
                "[DatabricksMetadata] SDK failed for schemas, SQL connector fallback: %s",
                exc,
            )
            rows = _sql_connector_query_metadata(f"SHOW SCHEMAS IN `{catalog}`")
            schemas = [
                str(row[0] if isinstance(row, (list, tuple)) else row) for row in rows
            ]
        schemas.sort(key=str.casefold)
        return schemas

    def list_tables(self, *, schema: str, catalog: str = "") -> list[dict[str, Any]]:
        if not schema:
            raise ValueError("schema is required when listing tables")
        catalog = catalog or DatabricksConfig.CATALOG
        try:
            tables = [
                {
                    "name": table.name,
                    "full_name": table.full_name
                    or f"{catalog}.{schema}.{table.name}",
                    "schema": schema,
                    "table_type": str(table.table_type),
                    "comment": table.comment or "",
                }
                for table in self._workspace_client()
                .tables.list(catalog_name=catalog, schema_name=schema)
                if table.name
            ]
        except Exception as exc:
            logger.warning(
                "[DatabricksMetadata] SDK failed for tables in %s, SQL connector fallback: %s",
                schema,
                exc,
            )
            rows = _sql_connector_query_metadata(
                f"SHOW TABLES IN `{catalog}`.`{schema}`"
            )
            tables = [
                {
                    "name": str(row.get("tableName", "")),
                    "full_name": f"{catalog}.{schema}.{row.get('tableName', '')}",
                    "schema": schema,
                    "table_type": "",
                    "comment": "",
                }
                for row in rows
                if row.get("tableName")
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
        catalog = catalog or DatabricksConfig.CATALOG
        schema = schema or DatabricksConfig.SCHEMA
        full_name = f"{catalog}.{schema}.{table}"

        try:
            uc_table = self._workspace_client().tables.get(full_name=full_name)
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            logger.warning(
                "[DatabricksMetadata] SDK failed for %s, DESCRIBE fallback: %s",
                full_name,
                exc,
            )
            return self._describe_fallback(catalog=catalog, schema=schema, table=table)

        detail = {
            "name": table,
            "full_name": full_name,
            "schema": schema,
            "table_type": str(uc_table.table_type),
            "comment": getattr(uc_table, "comment", "") or "",
            "owner": getattr(uc_table, "owner", "") or "",
            "columns": [
                self._column_payload(column)
                for column in (uc_table.columns or [])
                if column.name
            ],
        }
        table_tags = self._tags_payload(getattr(uc_table, "tags", None))
        if table_tags:
            detail["table_tags"] = table_tags
        return detail

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _workspace_client():
        if not DatabricksConfig.is_configured():
            raise RuntimeError("Databricks connection is not configured")
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient(
            host=DatabricksConfig.HOST,
            token=DatabricksConfig.TOKEN,
        )

    @staticmethod
    def _column_payload(column: Any) -> dict[str, Any]:
        result = {
            "name": column.name,
            "type": str(column.type_name),
            "nullable": getattr(column, "nullable", None),
            "comment": getattr(column, "comment", "") or "",
        }
        tags = DatabricksMetadataProvider._tags_payload(
            getattr(column, "tags", None)
        )
        if tags:
            result["tags"] = tags
        return result

    @staticmethod
    def _tags_payload(tags: Any) -> dict[str, Any]:
        if not tags:
            return {}
        try:
            return dict(tags.items())
        except (AttributeError, TypeError, ValueError):
            return {}

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        text = str(exc).casefold()
        return "not found" in text or "does not exist" in text

    def _describe_fallback(
        self,
        *,
        catalog: str,
        schema: str,
        table: str,
    ) -> Optional[dict[str, Any]]:
        """Recover column definitions via DESCRIBE TABLE EXTENDED when the SDK fails."""
        rows = _sql_connector_query_metadata(
            f"DESCRIBE TABLE EXTENDED `{catalog}`.`{schema}`.`{table}`"
        )
        columns: list[dict[str, Any]] = []
        for row in rows:
            name = str(row.get("col_name", "")).strip()
            data_type = str(row.get("data_type", "")).strip()
            comment = str(row.get("comment", "") or "")
            if not name or name.startswith("#"):
                # Column section ends at the metadata partition marker.
                break
            columns.append(
                {
                    "name": name,
                    "type": data_type,
                    "nullable": None,
                    "comment": comment,
                }
            )
        if not columns:
            return None
        full_name = f"{catalog}.{schema}.{table}"
        return {
            "name": table,
            "full_name": full_name,
            "schema": schema,
            "table_type": "",
            "comment": "",
            "owner": "",
            "columns": columns,
        }


def _sql_connector_query_metadata(sql: str) -> list[dict[str, Any]]:
    """Execute a metadata query through the Databricks SQL connector."""
    if not DatabricksConfig.is_configured():
        raise RuntimeError("Databricks connection not configured.")
    try:
        from databricks import sql as dbsql
    except ImportError as exc:
        raise RuntimeError("databricks-sql-connector not installed.") from exc

    connection = dbsql.connect(
        server_hostname=DatabricksConfig.HOST.replace("https://", ""),
        http_path=DatabricksConfig.HTTP_PATH,
        access_token=DatabricksConfig.TOKEN,
        _socket_timeout=DatabricksConfig.QUERY_TIMEOUT,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        columns = [d[0] for d in (cursor.description or [])]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        connection.close()
