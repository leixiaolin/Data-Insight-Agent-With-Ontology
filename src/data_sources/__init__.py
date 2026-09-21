"""Active data-source factory.

Exactly one backend (databricks | mysql) is active per process. Startup uses
DATA_SOURCE_TYPE; the MySQL settings API can replace the active source after
validation. Unknown values fall back to databricks with a warning.
"""

from __future__ import annotations

import threading
from typing import Optional

from ..config import DataSourceConfig
from ..utils import get_logger
from .base import DataSource, MetadataProvider, QueryResult, ScopeRules

logger = get_logger(__name__)

__all__ = [
    "DataSource",
    "MetadataProvider",
    "QueryResult",
    "ScopeRules",
    "get_active_data_source",
    "get_active_metadata_provider",
    "get_scope_rules",
    "build_identity_block",
]

_active_source: Optional[DataSource] = None
_active_provider: Optional[MetadataProvider] = None
_factory_lock = threading.RLock()


def replace_active_source(source: DataSource) -> tuple[Optional[DataSource], Optional[MetadataProvider]]:
    """Atomically install a validated source and discard its old metadata provider."""
    global _active_source, _active_provider
    with _factory_lock:
        previous = (_active_source, _active_provider)
        _active_source = source
        _active_provider = None
        return previous


def restore_active_source(previous: tuple[Optional[DataSource], Optional[MetadataProvider]]) -> None:
    global _active_source, _active_provider
    with _factory_lock:
        _active_source, _active_provider = previous


def _resolve_type() -> str:
    source_type = DataSourceConfig.TYPE
    if DataSourceConfig.RAW_TYPE not in DataSourceConfig.SUPPORTED_TYPES:
        logger.warning(
            "DATA_SOURCE_TYPE=%r is not supported (expected one of %s); "
            "falling back to 'databricks'.",
            DataSourceConfig.RAW_TYPE,
            ", ".join(DataSourceConfig.SUPPORTED_TYPES),
        )
    return source_type


def get_active_data_source() -> DataSource:
    """Return the process-wide active DataSource, constructing it on first use."""
    global _active_source
    with _factory_lock:
        if _active_source is None:
            if _resolve_type() == "mysql":
                from .mysql import MySQLDataSource

                _active_source = MySQLDataSource()
            else:
                from .databricks import DatabricksDataSource

                _active_source = DatabricksDataSource()
        return _active_source


def get_active_metadata_provider() -> MetadataProvider:
    """Return the process-wide active MetadataProvider, constructing it on first use."""
    global _active_provider
    with _factory_lock:
        if _active_provider is None:
            # The provider and query executor must share the same DataSource instance.
            # In MySQL mode this means one SQLAlchemy Engine/QueuePool per process;
            # in Databricks mode it keeps metadata recovery on the active executor.
            source = get_active_data_source()
            if _resolve_type() == "mysql":
                from .mysql import MySQLDataSource, MySQLMetadataProvider

                if not isinstance(source, MySQLDataSource):
                    raise RuntimeError("Active data source/provider type mismatch for MySQL")
                _active_provider = MySQLMetadataProvider(data_source=source)
            else:
                from .databricks import DatabricksDataSource, DatabricksMetadataProvider

                if not isinstance(source, DatabricksDataSource):
                    raise RuntimeError("Active data source/provider type mismatch for Databricks")
                _active_provider = DatabricksMetadataProvider(data_source=source)
        return _active_provider


def get_scope_rules() -> ScopeRules:
    """Return the active source's scope rules (dialect, allowlist, naming)."""
    return get_active_data_source().scope_rules()


_MYSQL_ARBITRATION = (
    "This runtime block is authoritative for data source identity, naming, and "
    "dialect; it supersedes any static reference to Databricks or Unity Catalog above."
)


def build_identity_block(for_agent: str) -> str:
    """Runtime identity block appended to a system prompt.

    for_agent: "data_insight" | "metadata". For the databricks source the returned
    block is byte-identical to the previously hardcoded "## Databricks Context"
    block of the matching agent, keeping prompts regression-free.
    """
    source = get_active_data_source()
    rules = source.scope_rules()
    allowlist = ", ".join(f"`{schema}`" for schema in rules.schemas)

    if source.name == "databricks":
        from ..config import DatabricksConfig

        if for_agent == "metadata":
            return (
                f"\n\n## Databricks Context\n"
                f"- Default catalog: `{rules.catalog}`\n"
                f"- Default schema: `{rules.default_schema}`\n"
                f"- Exposed schemas (authoritative allowlist): {allowlist}\n"
                f"- Never request or describe a catalog/schema outside this allowlist.\n"
                f"- Configured: {DatabricksConfig.is_configured()}\n"
            )
        return (
            f"\n\n## Databricks Context\n"
            f"- Catalog: `{rules.catalog}`\n"
            f"- Available schemas: {allowlist}\n"
            f"- Default schema (when unspecified): `{rules.default_schema}`\n"
            f"- Always use fully-qualified names: `{rules.catalog}.<schema>.<table>`\n"
            f"- Max rows per query: {DatabricksConfig.MAX_ROWS}\n"
            f"- Configured: {DatabricksConfig.is_configured()}\n"
        )

    from ..config import MySQLConfig

    ontology_note = (
        "- Ontology physical-table mappings were authored against the Databricks "
        "schema; verify every candidate against the active MySQL metadata before use.\n"
    )
    if for_agent == "metadata":
        return (
            f"\n\n## MySQL Context\n"
            f"- Default database: `{rules.default_schema}`\n"
            f"- Exposed databases (authoritative allowlist): {allowlist}\n"
            f"- Never request or describe a database outside this allowlist.\n"
            f"- MySQL has no catalog layer; use two-part `database.table` names.\n"
            f"- Configured: {MySQLConfig.is_configured()}\n"
            f"- {_MYSQL_ARBITRATION}\n"
        )
    return (
        f"\n\n## MySQL Context\n"
        f"- Dialect: MySQL (no QUALIFY clause; rewrite such filters as subqueries)\n"
        f"- Databases (authoritative allowlist): {allowlist}\n"
        f"- Default database (when unspecified): `{rules.default_schema}`\n"
        f"- Always use fully-qualified names: `<database>.<table>`\n"
        f"- Max rows per query: {_policy_max_rows()}\n"
        f"- Configured: {MySQLConfig.is_configured()}\n"
        f"- {_MYSQL_ARBITRATION}\n"
        f"{ontology_note}"
    )


def _policy_max_rows() -> int:
    from ..config import DataSourcePolicyConfig

    return DataSourcePolicyConfig.MAX_ROWS
