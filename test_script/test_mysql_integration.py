"""Real-MySQL integration tests on a throwaway testcontainers instance.

Every assertion runs against an actual MySQL 8.0 server (no mocks). Requires a
running Docker daemon; the whole module skips automatically otherwise.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("testcontainers.mysql", reason="testcontainers not installed")

from sqlalchemy import create_engine, text

import src.data_sources as ds
from src.config import MySQLConfig
from src.data_sources.mysql import MySQLDataSource, MySQLMetadataProvider
from src.data_sources.scope import validate_sql_scope
from src.metadata_catalog import MetadataCatalogService

ALLOWLIST_DB = "test"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS customers (
        CustomerID INT NOT NULL PRIMARY KEY COMMENT 'Customer surrogate key',
        FullName VARCHAR(100) NOT NULL COMMENT 'Customer display name',
        Region VARCHAR(50) NULL COMMENT 'Sales region'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AdventureWorks customers'
    """,
    """
    CREATE TABLE IF NOT EXISTS salesorders (
        SalesOrderID INT NOT NULL PRIMARY KEY COMMENT 'Order surrogate key',
        CustomerID INT NOT NULL COMMENT 'Owning customer',
        OrderDate DATE NOT NULL COMMENT 'Order date',
        TotalDue DECIMAL(10,2) NOT NULL COMMENT 'Order total amount'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AdventureWorks sales orders'
    """,
    "DELETE FROM customers",
    "DELETE FROM salesorders",
    (
        "INSERT INTO customers (CustomerID, FullName, Region) VALUES "
        "(1, 'Li Lei', 'East'), (2, N'李雷', N'华北'), (3, 'Han Meimei', 'West')"
    ),
    (
        "INSERT INTO salesorders (SalesOrderID, CustomerID, OrderDate, TotalDue) VALUES "
        "(1001, 1, '2023-01-15', 250.00), (1002, 1, '2023-02-20', 89.90), "
        "(1003, 2, '2023-03-11', 1234.56), (1004, 3, '2023-04-02', 42.00)"
    ),
]


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def mysql_host_port():
    from testcontainers.mysql import MySqlContainer

    try:
        container = MySqlContainer("mysql:8.0")
        container.start()
    except Exception as exc:  # Docker daemon down, image pull failure, etc.
        pytest.skip(f"MySQL testcontainer unavailable: {exc}")
    try:
        yield container.get_container_host_ip(), container.get_exposed_port(3306)
    finally:
        container.stop()


@pytest.fixture(scope="module")
def admin_engine(mysql_host_port):
    """Unrestricted engine used only for DDL, seeding, and connection kills."""
    host, port = mysql_host_port
    return create_engine(
        f"mysql+pymysql://root:test@{host}:{port}/?charset=utf8mb4"
    )


@pytest.fixture(scope="module", autouse=True)
def seeded_schema(admin_engine):
    with admin_engine.begin() as connection:
        for statement in DDL:
            connection.execute(text(statement))
    return admin_engine


@pytest.fixture()
def data_source(mysql_host_port, monkeypatch):
    """A fresh MySQLDataSource pointed at the container via patched config."""
    host, port = mysql_host_port
    monkeypatch.setattr(MySQLConfig, "HOST", host)
    monkeypatch.setattr(MySQLConfig, "PORT", int(port))
    monkeypatch.setattr(MySQLConfig, "USER", "test")
    monkeypatch.setattr(MySQLConfig, "PASSWORD", "test")
    monkeypatch.setattr(MySQLConfig, "DATABASES", [ALLOWLIST_DB])
    monkeypatch.setattr(MySQLConfig, "DATABASE", ALLOWLIST_DB)
    return MySQLDataSource()


@pytest.fixture()
def mysql_factory(data_source):
    """Make the module factory return the mysql source, like DATA_SOURCE_TYPE=mysql would."""
    ds._active_source = data_source
    ds._active_provider = MySQLMetadataProvider(data_source=data_source)
    yield
    ds._active_source = None
    ds._active_provider = None


# ─── Query execution ──────────────────────────────────────────────────────────


def test_execute_query_returns_result_contract(data_source):
    result = data_source.execute_query(
        "SELECT CustomerID, FullName FROM customers ORDER BY CustomerID"
    )
    assert result.columns == ["CustomerID", "FullName"]
    assert result.row_count == 3
    assert result.rows[0] == [1, "Li Lei"]
    assert result.sql.startswith("SELECT")


def test_max_rows_truncates_result(data_source):
    result = data_source.execute_query(
        "SELECT SalesOrderID FROM salesorders ORDER BY SalesOrderID",
        max_rows=2,
    )
    assert result.row_count == 2
    assert result.rows == [[1001], [1002]]


def test_utf8_values_round_trip(data_source):
    result = data_source.execute_query(
        "SELECT FullName FROM customers WHERE CustomerID = 2"
    )
    assert result.rows == [["李雷"]]


def test_concurrent_queries_run_in_parallel(data_source):
    """Three SLEEP(2) queries must overlap: serialised execution would need ≥6 s."""
    results = []
    errors = []

    def run() -> None:
        try:
            results.append(
                data_source.execute_query("SELECT SLEEP(2) AS waited").row_count
            )
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    elapsed = time.monotonic() - started

    assert errors == []
    assert results == [1, 1, 1]
    assert elapsed < 4.5, f"queries appear serialised (took {elapsed:.1f}s)"


def test_pre_ping_recovers_after_server_side_kill(data_source, admin_engine):
    with data_source.engine().connect() as connection:
        connection_id = connection.execute(
            text("SELECT CONNECTION_ID() AS id")
        ).scalar_one()

    with admin_engine.connect() as admin:
        admin.execute(text(f"KILL {connection_id}"))

    result = data_source.execute_query("SELECT 1 AS one")
    assert result.rows == [[1]]


def test_read_only_session_rejects_writes_at_the_database_level(data_source):
    with pytest.raises(Exception, match="(?i)read only"):
        data_source.execute_query(
            "INSERT INTO salesorders (SalesOrderID, CustomerID, OrderDate, TotalDue) "
            "VALUES (9999, 1, '2024-01-01', 1.00)"
        )


# ─── Scope validation against the live allowlist ─────────────────────────────


def test_scope_allows_allowlisted_database_and_blocks_others(data_source):
    rules = data_source.scope_rules()
    assert rules.schemas == [ALLOWLIST_DB]

    allowed = "SELECT COUNT(*) FROM test.salesorders"
    assert validate_sql_scope(allowed, rules) is None
    assert data_source.execute_query(allowed).rows == [[4]]

    blocked = validate_sql_scope("SELECT * FROM mysql.user", rules)
    assert blocked is not None and "outside MYSQL_DATABASES" in blocked

    bare = validate_sql_scope("SELECT * FROM salesorders", rules)
    assert bare is not None and "fully-qualified" in bare


# ─── Metadata provider (information_schema) ──────────────────────────────────


def test_list_schemas_returns_only_allowlisted_databases(data_source):
    provider = MySQLMetadataProvider(data_source=data_source)
    # The server also has mysql/sys/information_schema; none may leak through.
    assert provider.list_schemas() == [ALLOWLIST_DB]


def test_list_tables_returns_summaries_with_two_part_full_names(data_source):
    provider = MySQLMetadataProvider(data_source=data_source)
    tables = {table["name"]: table for table in provider.list_tables(schema="test")}
    assert {"customers", "salesorders"} <= set(tables)

    orders = tables["salesorders"]
    assert orders["full_name"] == "test.salesorders"
    assert orders["schema"] == "test"
    assert orders["table_type"] == "BASE TABLE"
    assert "sales orders" in orders["comment"].casefold()


def test_get_table_columns_match_real_ddl(data_source):
    provider = MySQLMetadataProvider(data_source=data_source)
    detail = provider.get_table(catalog="", schema="test", table="salesorders")

    assert detail is not None
    assert detail["full_name"] == "test.salesorders"
    columns = {column["name"]: column for column in detail["columns"]}
    assert columns["TotalDue"]["type"] == "decimal(10,2)"
    assert columns["TotalDue"]["nullable"] is False
    assert columns["TotalDue"]["comment"] == "Order total amount"
    assert columns["OrderDate"]["type"] == "date"
    assert columns["CustomerID"]["nullable"] is False


def test_get_table_returns_none_for_missing_table(data_source):
    provider = MySQLMetadataProvider(data_source=data_source)
    assert provider.get_table(catalog="", schema="test", table="nope") is None


def test_get_table_returns_none_for_database_outside_allowlist(data_source):
    provider = MySQLMetadataProvider(data_source=data_source)
    assert provider.get_table(catalog="", schema="mysql", table="user") is None


# ─── MetadataCatalogService over the mysql provider ──────────────────────────


def test_metadata_service_caches_and_rewrites_over_mysql(
    data_source, mysql_factory
):
    service = MetadataCatalogService(ttl_seconds=900)

    tables, first_hit = service.list_tables(schema="test")
    _, second_hit = service.list_tables(schema="test")
    assert first_hit is False
    assert second_hit is True
    assert any(table["full_name"] == "test.salesorders" for table in tables)

    detail, detail_hit = service.get_table("test.salesorders")
    assert detail_hit is False
    assert detail["full_name"] == "test.salesorders"

    sql = (
        "SELECT o.customerid, o.totaldue FROM test.salesorders o "
        "WHERE o.customer_id = 1"
    )
    rewritten, corrections = service.rewrite_sql_identifiers(sql)
    assert "o.CustomerID" in rewritten
    assert corrections == [
        {
            "table": "test.salesorders",
            "from": "o.customer_id",
            "to": "o.CustomerID",
        }
    ]
