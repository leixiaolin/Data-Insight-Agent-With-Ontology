# MySQL 演示数据

本目录提供与项目 AdventureWorks 本体及 `metadata-mapping` Skill 对齐的 MySQL/MariaDB 演示数据库。脚本使用兼容性较广的 `utf8mb4_unicode_ci` 排序规则，创建 7 张 `sales*` 表，并写入固定的 2023–2024 年销售样例。

> `01_create_demo_schema.sql` 会删除并重建 `ai_data_insight` 中同名的 7 张演示表。请勿对生产数据库执行。

## 初始化

使用有建库、建表和写数据权限的 MySQL 账号，从仓库根目录执行：

```bash
mysql -h 127.0.0.1 -P 3306 -u root -p < src/sql/01_create_demo_schema.sql
mysql -h 127.0.0.1 -P 3306 -u root -p < src/sql/02_init_demo_data.sql
```

两个脚本都可以重复执行。建表脚本重建演示表；数据脚本在一个事务中清空并重新写入固定数据，不会累积重复记录。

## 配置应用

建议为应用创建只读账号，并只授权演示数据库：

```sql
CREATE USER 'ontology_reader'@'%' IDENTIFIED BY 'replace-with-a-strong-password';
GRANT SELECT ON ai_data_insight.* TO 'ontology_reader'@'%';
```

在本地 `.env` 中配置（不要提交真实密码）：

```dotenv
DATA_SOURCE_TYPE=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=ontology_reader
MYSQL_PASSWORD=replace-with-a-strong-password
MYSQL_DATABASES=ai_data_insight
```

MySQL 模式要求所有物理表使用两段式名称，例如 `ai_data_insight.salesorderheader`。裸表名以及 Databricks 风格的 `catalog.schema.table` 三段式名称会被应用的 scope 校验拒绝。

## 数据内容

| 表 | 行数 | 粒度 |
|---|---:|---|
| `salescustomer` | 10 | 一个客户 |
| `salesaddress` | 12 | 一个邮寄地址 |
| `salescustomeraddress` | 20 | 一个客户/地址/角色关系 |
| `salesproductcategory` | 12 | 一个产品类目，包含两级递归层次 |
| `salesproduct` | 15 | 一个可销售产品 |
| `salesorderheader` | 24 | 一张订单 |
| `salesorderdetail` | 48 | 一张订单中的一个产品行 |

所有金额均为 USD。`LineTotal` 按数量、单价和折扣率计算；订单小计由明细合计反算，税额为小计的 8%，最终应付金额为小计、税额和运费之和。

## 演示问题

可在应用中尝试：

- 2023 年消费最高的客户是谁？他的订单数和平均客单价是多少？
- 按月展示 2023 年销售额和订单数趋势。
- 按国家和州/省比较销售额。
- 哪个产品大类的销量最高？请将二级类目递归汇总到顶级类目。
- 线上和线下订单的销售额、订单数及平均客单价有什么差异？
- 哪些订单使用了折扣？折扣产品和未折扣产品的销售情况如何？
- 2024 年尚未发货的订单有哪些？

`skills/analytics-spec/references/highest-spending-customer.sql` 是面向 Databricks 的 governed 模板，硬编码了 `ai_data_insight.silver.*` 三段式表名，不能直接用于 MySQL。MySQL 演示请求应通过动态 `sql-planning` 流程生成两段式 SQL。

## 快速校验

```sql
-- 应返回 24 张订单和 48 条明细。
SELECT COUNT(*) AS order_count FROM ai_data_insight.salesorderheader;
SELECT COUNT(*) AS line_count FROM ai_data_insight.salesorderdetail;

-- 应返回 0 行：订单头小计与明细合计不一致。
SELECT h.SalesOrderID
FROM ai_data_insight.salesorderheader AS h
JOIN (
  SELECT SalesOrderID, ROUND(SUM(LineTotal), 2) AS DetailTotal
  FROM ai_data_insight.salesorderdetail
  GROUP BY SalesOrderID
) AS d ON d.SalesOrderID = h.SalesOrderID
WHERE h.SubTotal <> d.DetailTotal
   OR h.TotalDue <> ROUND(h.SubTotal + h.TaxAmt + h.Freight, 2);
```
