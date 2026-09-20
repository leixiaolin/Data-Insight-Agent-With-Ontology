"""On-demand metadata cache tests over an injected fake MetadataProvider."""

from __future__ import annotations

from typing import Any, Optional

from src.metadata_catalog import MetadataCatalogService


def _column(name: str, type_name: str = "STRING") -> dict[str, Any]:
    return {"name": name, "type": type_name, "nullable": True, "comment": ""}


class _TablesApi:
    """Fake MetadataProvider speaking the final payload shapes."""

    def __init__(self) -> None:
        self.list_calls: list[tuple[str, str]] = []
        self.get_calls: list[str] = []
        self.summaries = [
            {
                "name": "salesorderdetail",
                "full_name": "catalog.silver.salesorderdetail",
                "schema": "silver",
                "table_type": "MANAGED",
                "comment": "Sales order line quantities",
            },
            {
                "name": "salesproduct",
                "full_name": "catalog.silver.salesproduct",
                "schema": "silver",
                "table_type": "MANAGED",
                "comment": "Products",
            },
            {
                "name": "salesproductcategory",
                "full_name": "catalog.silver.salesproductcategory",
                "schema": "silver",
                "table_type": "MANAGED",
                "comment": "Product categories",
            },
        ]
        self.details = {
            "catalog.silver.salesorderdetail": {
                "name": "salesorderdetail",
                "full_name": "catalog.silver.salesorderdetail",
                "schema": "silver",
                "table_type": "MANAGED",
                "comment": "Sales order line quantities",
                "owner": "owner",
                "columns": [
                    _column("SalesOrderID", "INT"),
                    _column("ProductID", "INT"),
                    _column("OrderQty", "INT"),
                ],
            },
            "catalog.silver.salesproduct": {
                "name": "salesproduct",
                "full_name": "catalog.silver.salesproduct",
                "schema": "silver",
                "table_type": "MANAGED",
                "comment": "Products",
                "owner": "owner",
                "columns": [
                    _column("ProductID", "INT"),
                    _column("Name"),
                    _column("ProductNumber"),
                    _column("ProductCategoryID", "INT"),
                ],
            },
            "catalog.silver.salesproductcategory": {
                "name": "salesproductcategory",
                "full_name": "catalog.silver.salesproductcategory",
                "schema": "silver",
                "table_type": "MANAGED",
                "comment": "Product categories",
                "owner": "owner",
                "columns": [
                    _column("ProductCategoryID", "INT"),
                    _column("ParentProductCategoryID", "INT"),
                    _column("Name"),
                ],
            },
        }

    def list_schemas(self, *, catalog: str = "") -> list[str]:
        return []

    def list_tables(
        self, *, schema: str, catalog: str = ""
    ) -> list[dict[str, Any]]:
        self.list_calls.append((catalog, schema))
        return [dict(summary) for summary in self.summaries]

    def get_table(
        self, *, catalog: str, schema: str, table: str
    ) -> Optional[dict[str, Any]]:
        full_name = f"{catalog}.{schema}.{table}"
        self.get_calls.append(full_name)
        detail = self.details.get(full_name)
        if detail is None:
            return None
        return {
            **detail,
            "columns": [dict(column) for column in detail["columns"]],
        }


def _service_with_client() -> tuple[MetadataCatalogService, _TablesApi]:
    tables_api = _TablesApi()
    service = MetadataCatalogService(ttl_seconds=900, provider=tables_api)
    return service, tables_api


def test_listing_tables_does_not_fetch_any_columns() -> None:
    service, tables_api = _service_with_client()

    tables, cache_hit = service.list_tables(catalog="catalog", schema="silver")
    cached_tables, second_cache_hit = service.list_tables(
        catalog="catalog",
        schema="silver",
    )

    assert cache_hit is False
    assert second_cache_hit is True
    assert tables == cached_tables
    assert tables_api.list_calls == [("catalog", "silver")]
    assert tables_api.get_calls == []
    assert service.cache_stats()["table_details"] == 0


def test_only_selected_table_details_are_loaded_and_reused() -> None:
    service, tables_api = _service_with_client()

    detail, cache_hit = service.get_table(
        "salesorderdetail",
        catalog="catalog",
        schema="silver",
    )
    cached_detail, second_cache_hit = service.get_table(
        "salesorderdetail",
        catalog="catalog",
        schema="silver",
    )

    assert cache_hit is False
    assert second_cache_hit is True
    assert detail == cached_detail
    assert [column["name"] for column in detail["columns"]] == [
        "SalesOrderID",
        "ProductID",
        "OrderQty",
    ]
    assert tables_api.get_calls == ["catalog.silver.salesorderdetail"]
    assert service.cache_stats()["table_details"] == 1


def test_search_uses_cached_summaries_until_explicit_refresh() -> None:
    service, tables_api = _service_with_client()
    service.list_tables(catalog="catalog", schema="silver")
    tables_api.summaries.append(
        {
            "name": "inventorymovement",
            "full_name": "catalog.silver.inventorymovement",
            "schema": "silver",
            "table_type": "MANAGED",
            "comment": "Warehouse inventory movement facts",
        }
    )

    stale_matches, cache_hit = service.search_tables(
        "inventory movement",
        catalog="catalog",
        schema="silver",
    )
    service.list_tables(
        catalog="catalog",
        schema="silver",
        force_refresh=True,
    )
    refreshed_matches, refreshed_cache_hit = service.search_tables(
        "inventory movement",
        catalog="catalog",
        schema="silver",
    )

    assert cache_hit is True
    assert stale_matches == []
    assert refreshed_cache_hit is True
    assert [table["name"] for table in refreshed_matches] == [
        "inventorymovement"
    ]
    assert len(tables_api.list_calls) == 2
    assert tables_api.get_calls == []


def test_sql_identifier_validation_uses_only_selected_cached_details() -> None:
    service, tables_api = _service_with_client()
    service.get_table("salesorderdetail", catalog="catalog", schema="silver")
    service.get_table("salesproduct", catalog="catalog", schema="silver")
    sql = (
        "SELECT p.productname, d.orderqty "
        "FROM catalog.silver.salesorderdetail d "
        "JOIN catalog.silver.salesproduct p ON d.productid = p.productid"
    )

    rewritten, corrections = service.rewrite_sql_identifiers(sql)

    assert "p.Name" in rewritten
    assert "d.OrderQty" in rewritten
    assert "d.ProductID = p.ProductID" in rewritten
    assert corrections == [
        {
            "table": "catalog.silver.salesproduct",
            "from": "p.productname",
            "to": "p.Name",
        }
    ]
    assert tables_api.get_calls == [
        "catalog.silver.salesorderdetail",
        "catalog.silver.salesproduct",
    ]


def test_unknown_column_is_not_guessed() -> None:
    service, _ = _service_with_client()
    service.get_table("salesproduct", catalog="catalog", schema="silver")
    sql = "SELECT p.marketsegment FROM catalog.silver.salesproduct p"

    rewritten, corrections = service.rewrite_sql_identifiers(sql)

    assert rewritten == sql
    assert corrections == []


def test_alias_reused_by_different_tables_is_never_rewritten() -> None:
    service, _ = _service_with_client()
    service.get_table("salesproductcategory", catalog="catalog", schema="silver")
    service.get_table("salesproduct", catalog="catalog", schema="silver")
    service.get_table("salesorderdetail", catalog="catalog", schema="silver")
    sql = (
        "WITH RECURSIVE walk AS ( "
        "SELECT p.ProductCategoryID, p.ParentProductCategoryID "
        "FROM catalog.silver.salesproductcategory p "
        ") "
        "SELECT d.OrderQty FROM catalog.silver.salesorderdetail d "
        "JOIN catalog.silver.salesproduct p ON d.ProductID = p.ProductID"
    )

    rewritten, corrections = service.rewrite_sql_identifiers(sql)

    assert "p.ParentProductCategoryID" in rewritten
    assert corrections == []


def test_identifier_valid_in_another_verified_table_is_not_fuzzy_rewritten() -> None:
    service, _ = _service_with_client()
    service.get_table("salesproductcategory", catalog="catalog", schema="silver")
    service.get_table("salesproduct", catalog="catalog", schema="silver")
    sql = "SELECT p.ParentProductCategoryID FROM catalog.silver.salesproduct p"

    rewritten, corrections = service.rewrite_sql_identifiers(sql)

    assert rewritten == sql
    assert corrections == []


def test_similar_physical_column_is_not_fuzzily_guessed() -> None:
    service, _ = _service_with_client()
    service.get_table("salesproduct", catalog="catalog", schema="silver")
    sql = "SELECT p.product FROM catalog.silver.salesproduct p"

    rewritten, corrections = service.rewrite_sql_identifiers(sql)

    assert rewritten == sql
    assert corrections == []


def test_cte_projection_is_not_fuzzily_rewritten_as_physical_column() -> None:
    service, _ = _service_with_client()
    service.get_table("salesproduct", catalog="catalog", schema="silver")
    sql = (
        "WITH product_metrics AS ("
        "SELECT p.ProductID, p.Name AS product "
        "FROM catalog.silver.salesproduct p"
        ") "
        "SELECT p.product FROM product_metrics p"
    )

    rewritten, corrections = service.rewrite_sql_identifiers(sql)

    assert "SELECT p.product FROM product_metrics p" in rewritten
    assert "SELECT p.ProductID FROM product_metrics p" not in rewritten
    assert corrections == []


def test_cte_alias_shadowing_disables_suffix_rewrite() -> None:
    service, _ = _service_with_client()
    service.get_table("salesproduct", catalog="catalog", schema="silver")
    sql = (
        "WITH product_metrics AS ("
        "SELECT p.ProductID, p.Name AS display_name "
        "FROM catalog.silver.salesproduct p"
        ") "
        "SELECT p.display_name FROM product_metrics p"
    )

    rewritten, corrections = service.rewrite_sql_identifiers(sql)

    assert "SELECT p.display_name FROM product_metrics p" in rewritten
    assert "SELECT p.Name FROM product_metrics p" not in rewritten
    assert corrections == []