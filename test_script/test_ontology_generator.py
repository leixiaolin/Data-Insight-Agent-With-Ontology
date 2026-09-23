"""Offline schema-draft generator tests (PRD FR-10/FR-11, AC-17..AC-19).

The MySQL metadata provider is replaced with in-memory fixtures; no database,
no business rows.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.config.settings import DataSourceConfig, MySQLConfig, OntologyManagementConfig as Config
from src.ontology import generator as gen
from src.ontology.management_errors import ManagementError
from src.ontology.validation import inspect_documents


class FakeProvider:
    """In-memory MySQLMetadataProvider stand-in (duck-typed)."""

    def __init__(self, schemas, tables, details, foreign_keys):
        self._schemas = schemas
        self._tables = tables
        self._details = details
        self._foreign_keys = foreign_keys

    def list_schemas(self, *, catalog=""):
        return list(self._schemas)

    def list_tables(self, *, schema, catalog=""):
        return [dict(table, schema=schema, full_name=f"{schema}.{table['name']}")
                for table in self._tables.get(schema, [])]

    def get_table(self, *, catalog, schema, table):
        return self._details.get(f"{schema}.{table}")

    def list_foreign_keys(self, *, schema):
        return self._foreign_keys.get(schema, [])


def _table(name, columns, comment=""):
    return {"name": name, "full_name": f"sales.{name}", "table_type": "BASE TABLE",
            "comment": comment, "columns": columns}


def _column(name, raw_type, comment=""):
    return {"name": name, "type": raw_type, "nullable": True, "comment": comment}


CUSTOMERS = _table("customers", [
    _column("id", "int unsigned"),
    _column("name", "varchar(100)"),
])
ORDERS = _table("orders", [
    _column("id", "int"),
    _column("order_no", "int"),
    _column("customer_id", "int"),
    _column("amount", "decimal(10,2)"),
    _column("status", "enum('new','done')"),
    _column("created_at", "datetime"),
    _column("flag_ts", "timestamp"),
    _column("day_time", "time"),
    _column("payload", "json"),
    _column("blob_col", "blob"),
    _column("mystery", "money"),
])
EMPLOYEES = _table("employees", [
    _column("id", "int"),
    _column("manager_id", "int"),
])
ORDER_ITEMS = _table("order_items", [
    _column("order_id", "int"),
    _column("order_no", "int"),
])
CHINESE = _table("订单明细", [
    _column("数量", "int"),
    _column("备注", "text"),
])
UNRELATED = _table("audit_log", [_column("id", "int")])

FOREIGN_KEYS = {
    "sales": [
        {"constraint": "fk_orders_customer", "schema": "sales", "table": "orders",
         "referenced_table": "customers", "column_pairs": [("customer_id", "id")]},
        {"constraint": "fk_items_order", "schema": "sales", "table": "order_items",
         "referenced_table": "orders",
         "column_pairs": [("order_id", "id"), ("order_no", "order_no")]},
        {"constraint": "fk_employee_manager", "schema": "sales", "table": "employees",
         "referenced_table": "employees", "column_pairs": [("manager_id", "id")]},
        {"constraint": "fk_orders_audit", "schema": "sales", "table": "orders",
         "referenced_table": "audit_log", "column_pairs": [("id", "id")]},
    ],
}

TABLE_LIST = [
    dict(CUSTOMERS), dict(ORDERS), dict(EMPLOYEES), dict(ORDER_ITEMS), dict(CHINESE),
    {**UNRELATED, "table_type": "VIEW"},
]
DETAILS = {
    "sales.customers": CUSTOMERS, "sales.orders": ORDERS, "sales.employees": EMPLOYEES,
    "sales.order_items": ORDER_ITEMS, "sales.订单明细": CHINESE,
    "sales.audit_log": {**UNRELATED, "table_type": "VIEW"},
}


@pytest.fixture
def mysql(monkeypatch):
    provider = FakeProvider(["sales"], {"sales": TABLE_LIST}, DETAILS, FOREIGN_KEYS)
    monkeypatch.setattr(gen, "get_active_metadata_provider", lambda: provider)
    monkeypatch.setattr(DataSourceConfig, "TYPE", "mysql")
    monkeypatch.setattr(MySQLConfig, "HOST", "db.example")
    monkeypatch.setattr(MySQLConfig, "PORT", 3306)
    monkeypatch.setattr(MySQLConfig, "USER", "reader")
    monkeypatch.setattr(MySQLConfig, "DATABASES", ["sales"])
    return gen.generator_source_revision()


def _generate(mysql, tables, **kwargs):
    return gen.generate_draft(tables, source_revision=mysql, **kwargs)


def test_cross_database_foreign_key_never_binds_to_same_named_local_table(mysql, monkeypatch):
    provider = gen.get_active_metadata_provider()
    monkeypatch.setattr(provider, 'list_foreign_keys', lambda **kwargs: [{
        'constraint': 'cross_db', 'schema': 'sales', 'table': 'orders',
        'referenced_schema': 'other', 'referenced_table': 'customers',
        'column_pairs': [('customer_id', 'id')]}])
    result = _generate(mysql, ['sales.orders', 'sales.customers'])
    assert result['report']['missing_relations'][0]['to'] == 'other.customers'
    assert 'fk__sales__cross_db' not in result['content']
    assert result['source_revision'] == mysql


def test_generation_rechecks_source_after_metadata_reads(mysql, monkeypatch):
    provider = gen.get_active_metadata_provider()
    original = provider.get_table

    def changed(**kwargs):
        monkeypatch.setattr(MySQLConfig, 'HOST', 'new-host')
        return original(**kwargs)

    monkeypatch.setattr(provider, 'get_table', changed)
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ['sales.orders'])
    assert error.value.detail['code'] == 'source_changed'


def test_listing_marks_views_unsupported(mysql):
    result = gen.list_generator_tables()
    supported = {table["full_name"]: table["supported"] for table in result["tables"]}
    assert supported["sales.customers"] is True
    assert supported["sales.audit_log"] is False
    assert result["limits"]["max_tables"] == Config.MAX_TABLES
    assert result["source_identity"]["databases"] == ["sales"]


def test_same_column_names_do_not_merge(mysql):
    draft = _generate(mysql, ["sales.customers", "sales.orders"])
    report, _ = inspect_documents({"draft.owl": draft["content"]}, publication=False)
    assert report["valid"] is True
    property_iris = re.findall(r'<owl:DatatypeProperty rdf:about="([^"]+)"', draft["content"])
    assert len(property_iris) == len(set(property_iris)), "IRIs must stay unique"
    # customers.name and orders.order_no live under table-scoped IRIs
    assert any("customers" in iri for iri in property_iris)
    assert any("orders" in iri for iri in property_iris)


def test_chinese_identifiers_encoded_and_labelled(mysql):
    draft = _generate(mysql, ["sales.订单明细"])
    assert "%E8%AE%A2%E5%8D%95%E6%98%8E%E7%BB%86" in draft["content"], "non-ASCII must be percent-encoded"
    assert ">订单明细<" in draft["content"], "original names kept as labels"
    assert ">数量<" in draft["content"]
    report, _ = inspect_documents({"draft.owl": draft["content"]}, publication=False)
    assert report["valid"] is True


def test_composite_fk_keeps_ordered_column_pairs(mysql):
    draft = _generate(mysql, ["sales.orders", "sales.order_items"])
    pairs = [line.split(">")[1].split("<")[0] for line in draft["content"].splitlines()
             if "sourceColumnPair" in line and ">" in line]
    item_pairs = [pair for pair in pairs if pair.startswith(("1|", "2|")) and "order_items" in pair]
    assert any(pair.startswith("1|sales.order_items.order_id|sales.orders.id") for pair in item_pairs)
    assert any(pair.startswith("2|sales.order_items.order_no|sales.orders.order_no") for pair in item_pairs)


def test_self_referential_fk_uses_same_domain_and_range(mysql):
    draft = _generate(mysql, ["sales.employees"])
    object_properties = [line for line in draft["content"].splitlines() if "ObjectProperty" in line and "owl:" in line]
    assert any("fk_employee_manager" in line for line in object_properties)
    domain_range = draft["content"].split("fk_employee_manager")[1]
    assert domain_range.count("table__sales__employees") >= 2, "domain and range point at the same class"


def test_unselected_fk_target_reported_as_missing(mysql):
    draft = _generate(mysql, ["sales.orders"])
    missing = draft["report"]["missing_relations"]
    assert any(item["constraint"] == "fk_orders_audit" for item in missing)
    assert "fk_orders_audit" not in draft["content"], "unusable relations are not generated"
    assert any("foreign key" in warning for warning in draft["report"]["warnings"])


def test_type_mapping_and_degradation_warnings(mysql):
    draft = _generate(mysql, ["sales.customers", "sales.orders"])
    content = draft["content"]
    assert 'resource="http://www.w3.org/2001/XMLSchema#integer"' in content
    assert 'resource="http://www.w3.org/2001/XMLSchema#decimal"' in content
    assert 'resource="http://www.w3.org/2001/XMLSchema#dateTime"' in content
    assert 'resource="http://www.w3.org/2001/XMLSchema#base64Binary"' in content
    assert ">enum('new','done')<" in content, "raw sourceType keeps enum/unsigned detail"
    assert ">int unsigned<" in content, "customers.id keeps the unsigned raw type"
    warnings = " ".join(draft["report"]["warnings"])
    assert "timestamp" in warnings
    assert "time is not an intra-day" in warnings
    assert "json" in warnings
    assert "money" in warnings, "unknown types degrade to string with a warning"


def test_source_revision_mismatch_rejected(mysql, monkeypatch):
    monkeypatch.setattr(MySQLConfig, "HOST", "other-host")
    with pytest.raises(ManagementError) as error:
        gen.generate_draft(["sales.orders"], source_revision=mysql)
    assert error.value.status == 409
    assert error.value.detail["code"] == "source_changed"


def test_allowlist_violation_and_bad_names_rejected(mysql):
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["other.orders"])
    assert error.value.status == 422
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["sales.missing_table"])
    assert error.value.detail["code"] == "invalid_selection"
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["no-dot"])
    assert error.value.status == 422


def test_views_rejected(mysql):
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["sales.audit_log"])
    assert "view" in error.value.detail["message"]


def test_limits_reject_without_truncation(mysql, monkeypatch):
    monkeypatch.setattr(Config, "MAX_TABLES", 2)
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["sales.customers", "sales.orders", "sales.employees"])
    assert error.value.status == 413
    monkeypatch.setattr(Config, "MAX_TABLES", 50)
    monkeypatch.setattr(Config, "MAX_COLUMNS", 3)
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["sales.orders"])
    assert error.value.status == 413
    assert "10 columns" in error.value.detail["message"] or "column" in error.value.detail["message"]


def test_namespace_validation_and_conflict(mysql):
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["sales.orders"], namespace="not absolute")
    assert error.value.detail["code"] == "invalid_namespace"
    with pytest.raises(ManagementError) as error:
        _generate(mysql, ["sales.orders"], namespace="http://example.org/taken#",
                  existing_ontology_iris={"http://example.org/taken"})
    assert error.value.status == 409
    custom = _generate(mysql, ["sales.orders"], namespace="http://example.org/draft/1.0#")
    assert "http://example.org/draft/1.0#" in custom["content"]


def test_draft_markers_and_identity_annotations(mysql):
    draft = _generate(mysql, ["sales.orders"])
    content = draft["content"]
    assert "SCHEMA DRAFT" in content and "requires human review" in content
    assert "mysql://db.example:3306/sales" in content, "source identity without credentials"
    assert "generatedAt" in content
    assert ">sales.orders<" in content, "selection scope recorded"
    assert "password" not in content.lower()


def test_unsupported_data_source(monkeypatch):
    monkeypatch.setattr(DataSourceConfig, "TYPE", "databricks")
    with pytest.raises(ManagementError) as error:
        gen.list_generator_tables()
    assert error.value.status == 422
    assert error.value.detail["code"] == "unsupported_source"


def test_generated_mappings_readable_by_schema_mapping(mysql, tmp_path: Path):
    from src.ontology.service import OntologyService

    draft = _generate(mysql, ["sales.orders", "sales.order_items"])
    (tmp_path / "draft.owl").write_text(draft["content"], encoding="utf-8")
    service = OntologyService(tmp_path, file_glob="*.owl", only_local=True).load()
    try:
        assert not service.load_errors
        order_iri = next(
            iri for iri in re.findall(r'<owl:Class rdf:about="([^"]+)"', draft["content"])
            if "orders" in iri
        )
        mapping = service.get_schema_mapping(order_iri)
        tables = mapping["data"]["physical_mappings"]["tables"]
        assert any(item["value"] == "sales.orders" and item["confidence"] == 1.0
                   for item in tables), "sourceTable annotation is explicit mapping"
        fk_property = next(
            iri for iri in re.findall(r'<owl:ObjectProperty rdf:about="([^"]+)"', draft["content"])
            if "fk_items_order" in iri
        )
        fk_mapping = service.get_schema_mapping(fk_property)
        join_pairs = fk_mapping["data"]["physical_mappings"]["join_columns"]
        assert join_pairs and join_pairs[0]["value"].startswith("1|sales.order_items.order_id|sales.orders.id")
    finally:
        service.close()
