# MySQL 数据源支持 — 优化方案

> 状态：待评审
> 日期：2026-09-20
> 范围：后端数据层抽象 + MySQL 适配 + 配置/提示词/技能/文档配套改造

---

## 1. 背景与目标

当前系统的数据层完全绑定 Azure Databricks：

- **查询执行**：`DataInsightAgent` 通过 databricks-sql-connector 连接 SQL Warehouse 执行只读 NL2SQL 查询
- **元数据**：`MetadataCatalogService` 通过 databricks-sdk `WorkspaceClient` 访问 Unity Catalog

本方案引入 MySQL 数据源支持，使同一套多智能体分析链路（Ontology → Metadata → DataInsight）可以运行在 MySQL 之上。

### 已确认的设计决策

| 决策点 | 结论 |
|---|---|
| 接入模式 | **单一数据源配置切换**：`.env` 中 `DATA_SOURCE_TYPE=databricks\|mysql` 决定当前活跃数据源，同一时间只有一个，不做多源并存与会话级选择 |
| 元数据范围 | **数据库白名单**：`MYSQL_DATABASES` 环境变量，对齐现有 `DATABRICKS_SCHEMAS` 机制 |
| 安全边界 | 保持只读三重防线：关键词黑名单 + sqlglot 语法校验 + 白名单作用域检查 |
| 兼容性 | **Databricks 默认路径行为完全不变**（未设 `DATA_SOURCE_TYPE` 时默认 databricks） |
| 依赖 | 新增 **SQLAlchemy Core（引擎/连接池管理）+ PyMySQL（底层驱动）**：并发会话的查询真正并行而非排队；复用成熟的 `pool_pre_ping` 失效检测 / `pool_recycle` 回收 / `QueuePool` 有界等待；与 backlog ODA-011（用有界连接池替换进程级连接 + 全局锁）方向一致。不使用 ORM，仅用 Core 引擎层 |

---

## 2. 现状分析 — Databricks 耦合点

| 层 | 位置 | 耦合内容 | 可复用部分 |
|---|---|---|---|
| SQL 执行 | `src/agents/data_insight_agent.py` L60-62、L361-453 | 模块级连接单例 + RLock；`_get_db_connection`（databricks-sql-connector）；`_run_databricks_query` | `_json_default` 序列化（date/Decimal/bytes）通用 |
| 只读校验 | 同文件 L65-108 | `_validate_sql_scope`：sqlglot `read="databricks"` + **三段式 catalog.schema.table 强制** + CATALOG/SCHEMAS 白名单 | CTE 豁免逻辑、关键词黑名单（L957-961）通用 |
| 方言特例 | 同文件 L111+、L969-996 | `_extract_sql_measures` 用 databricks 方言；`_rewrite_invalid_qualify` 为 Databricks 专用 QUALIFY 改写 | — |
| 元数据 | `src/metadata_catalog.py` L50-153、L348-357 | `list_schemas`/`list_tables`/`get_table` 绑定 `WorkspaceClient` | **缓存（L321-346）、`search_tables` 模糊匹配（L408-444）、`get_tables_details` 批量（L178-209）、`rewrite_sql_identifiers` 纠错（L211-310）全部通用** |
| 命名补全 | 同文件 L359-377 | `_qualified_table_parts` 把 1/2/3 段名补全为三段式 | 需按数据源参数化为两段式 |
| SQL fallback | `src/agents/metadata_agent.py` L77-99、L240/L340/L439 | `SHOW SCHEMAS` / `SHOW TABLES` / `DESCRIBE TABLE EXTENDED` 为 Databricks 方言 | — |
| 配置 | `src/config/settings.py` L67-126 | `DatabricksConfig` 混合数据源身份（HOST/TOKEN/HTTP_PATH）与查询策略（MAX_ROWS/TTL 等）；import 时求值的类属性单例 | `METADATA_AGENT_*` 等本就是中性命名 |
| 提示词 | `src/prompts/*.py`、两个 agent 的 "## Databricks Context" 运行时注入块 | 静态字符串硬编码 Unity Catalog / 三段式 / Delta 措辞 | metadata_agent.py L565-575 的运行时追加模式可推广 |
| 技能 | `skills/sql-planning`、`skills/analytics-spec` | "read-only SparkSQL/Databricks SQL"；参考 SQL 硬编码 `ai_data_insight.silver.salesorderheader` 与 Spark `date_sub()` | metadata-mapping 通用 |

---

## 3. 架构设计 — `src/data_sources/` 双协议 + 工厂

```
                          ┌────────────────────────────────────┐
                          │  src/data_sources/__init__.py      │
                          │  get_active_data_source()          │
                          │  get_active_metadata_provider()    │  按 DATA_SOURCE_TYPE 分支
                          │  get_scope_rules()                 │  进程级单例（Lock 保护）
                          │  build_identity_block()            │
                          └──────┬──────────────────┬──────────┘
                                 │                  │
                 ┌───────────────▼──────┐   ┌───────▼──────────────┐
                 │ databricks.py        │   │ mysql.py             │
                 │  DatabricksDataSource│   │  MySQLDataSource     │
                 │  DatabricksMetadata  │   │  MySQLMetadataProvider│
                 │    Provider (含SQL   │   │  (information_schema)│
                 │    fallback下沉)     │   │                      │
                 └──────────────────────┘   └──────────────────────┘
```

### 3.1 `base.py` — 数据结构与协议

```python
@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    sql: str
    # 与现 _run_databricks_query 返回 dict 完全同构，消费端零改动

@dataclass(frozen=True)
class ScopeRules:
    sqlglot_dialect: str        # "databricks" | "mysql"
    catalog: str                # MySQL 恒为 ""
    schemas: list[str]          # DATABRICKS_SCHEMAS / MYSQL_DATABASES
    require_catalog: bool       # True=强制三段式；False=强制两段式 db.table
    qualified_parts: int        # 3 | 2
    naming_example: str         # "catalog.schema.table" | "database.table"
    display_name: str           # "Azure Databricks" | "MySQL"

class DataSource(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def execute_query(self, sql: str, *, max_rows: int = 500) -> QueryResult: ...
    def scope_rules(self) -> ScopeRules: ...

class MetadataProvider(Protocol):
    def list_schemas(self) -> list[str]: ...
    def list_tables(self, schema: str) -> list[dict[str, Any]]: ...
    def get_table(self, schema: str, table: str) -> Optional[dict[str, Any]]: ...
```

### 3.2 `__init__.py` — 工厂与身份块

- `DATA_SOURCE_TYPE` 未设或非法值 → 默认 `databricks` 并 log warning（向后兼容）
- `build_identity_block() -> str`：
  - **databricks 分支：与现硬编码 "## Databricks Context" 块逐字节等价**（保证提示词零回归）
  - mysql 分支：生成 "## MySQL Context"，内容含 database 白名单（权威）、两段式命名示例、MySQL 方言声明、MAX_ROWS、configured 状态，以及两句关键提示：
    - 仲裁声明："This runtime block is authoritative for data source identity, naming, and dialect; it supersedes any static reference to Databricks or Unity Catalog above."
    - 本体验证提示："Ontology physical-table mappings were authored against the Databricks schema; verify every candidate against the active MySQL metadata before use."

### 3.3 `databricks.py` — 现有代码平移（不重写）

- `DatabricksDataSource`：搬入连接单例 + RLock、`_get_db_connection`（含 `SELECT 1` 心跳）、`_is_connection_level_error`；`_run_databricks_query` 改名 `execute_query` 并返回 `QueryResult`
- `DatabricksMetadataProvider`：搬入 `_workspace_client()` 与三个 UC 方法的 SDK 调用及 payload 组装（`_column_payload`/`_tags_payload` 一并迁移）；**同时吸收 metadata_agent 的 `_sql_connector_query_metadata`，把 SQL fallback 下沉为 provider 内部逻辑**（SDK 失败时用 DataSource 执行 `SHOW ...`/`DESCRIBE` 并转换 payload，保留 `source` 字段语义）

### 3.4 `mysql.py` — SQLAlchemy Core + PyMySQL 实现

**选型：SQLAlchemy Core（引擎/池管理层）+ PyMySQL（驱动）**。二者非竞争关系——SQLAlchemy 通过 `mysql+pymysql://` 方言使用 PyMySQL 访问数据库，真正的取舍是"裸 PyMySQL + 手写连接管理" vs "SQLAlchemy Engine 托管连接池"：

- **并发收益（核心动因）**：系统支持并发会话，但 Databricks 侧现有的"单连接 + 全局锁"使并发会话的查询实际排队执行。MySQL 服务器天然支持多连接并行；引入 `QueuePool` 后 MySQL 侧查询可真正并行，与新数据源的能力对齐
- **成熟的生命周期管理**：`pool_pre_ping`（取连接前轻量健康检查，失效自动重建）、`pool_recycle`（防 `wait_timeout` 断连）、有界池 + 溢出 + 超时等待，均为 SQLAlchemy 打磨多年的能力，免去手写重连/心跳/锁的正确性风险
- **与路线图一致**：backlog ODA-011 的目标就是"bounded connection pooling 替换进程级连接 + 全局执行锁"；MySQL 侧直接按目标形态实现，Databricks 侧维持现状由 ODA-011 单独处理（HTTP 长连接、冷启动 3-10s，池化语义不同，且 `DataSource` 协议允许两侧实现技术不同）
- **不使用 ORM**：只依赖 Core 的 engine/pool 层，不引入对象映射、关系模型等概念

`MySQLDataSource`：

```python
from sqlalchemy import create_engine, text

class MySQLDataSource:
    name = "mysql"

    def __init__(self) -> None:
        # 进程级单例 engine（线程安全，由 SQLAlchemy 池管理并发）
        self._engine = create_engine(
            f"mysql+pymysql://{MySQLConfig.USER}:{quote_plus(MySQLConfig.PASSWORD)}"
            f"@{MySQLConfig.HOST}:{MySQLConfig.PORT}/?charset={MySQLConfig.CHARSET}",
            pool_size=MySQLConfig.POOL_SIZE,              # 常驻连接数
            max_overflow=MySQLConfig.POOL_MAX_OVERFLOW,   # 峰值溢出连接数
            pool_pre_ping=True,                           # 取用前健康检查，失效自动重建
            pool_recycle=MySQLConfig.POOL_RECYCLE_SECONDS,# 防 wait_timeout 断连
            pool_timeout=DataSourcePolicyConfig.QUERY_TIMEOUT,
            connect_args={"read_timeout": DataSourcePolicyConfig.QUERY_TIMEOUT,
                         # 池中每个新建连接执行只读会话设置（纵深防御，可选）
                         "init_command": "SET SESSION TRANSACTION READ ONLY"},
        )

    def execute_query(self, sql: str, *, max_rows: int = 500) -> QueryResult:
        # with engine.connect() as conn: conn.execute(text(sql))
        # → fetchmany(max_rows) → QueryResult（形状不变）
```

- 连接失效处理交给池：`pre_ping` 检测到断连即丢弃重建，业务代码不再维护"置空单例重连"逻辑
- SQL 语法/对象不存在错误（`ProgrammingError`）正常抛出，由上层错误反馈机制处理
- 现有 `_json_default` 已覆盖 pymysql 返回类型（`datetime.date`、`Decimal`、`bytes`），无需改动

`MySQLMetadataProvider` — 全部走 `information_schema` 参数化查询（杜绝元数据查询自身的注入面；连接同样取自 `MySQLDataSource` 的 engine 池，并发批量详情抓取天然受益）：

| 方法 | 查询 | 返回 |
|---|---|---|
| `list_schemas` | `SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN (...)`（只查白名单，减少往返） | `list[str]` |
| `list_tables` | `SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s` | `{name, full_name: f"{schema}.{table}", schema, table_type, comment}` |
| `get_table` | `SELECT ... FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s` | `COLUMN_TYPE` 保留完整类型（如 `varchar(50)`、`decimal(10,2)`）、`IS_NULLABLE`→nullable、`COLUMN_COMMENT`→comment；无 tags/owner 字段则省略；表不存在返回 `None` |

### 3.5 `scope.py` — 只读校验方言化

`validate_sql_scope(sql, rules)` 从现 L65-108 抽出参数化：

- sqlglot `read=rules.sqlglot_dialect`（唯一方言切换点）；CTE 豁免逻辑原样保留（两方言通用）
- **Databricks 分支**（`require_catalog=True`）：现行为逐行保留 — catalog 与 db 必须存在、catalog 必须等于配置值、db 必须在白名单
- **MySQL 分支**（`require_catalog=False`）：
  - `table.catalog` 非空 → BLOCKED（MySQL 无三段式）
  - `table.db` 缺失 → BLOCKED（强制 `database.table` 两段式）
  - `table.db ∉ MYSQL_DATABASES`（casefold 比较）→ BLOCKED，报错文案引用 `MYSQL_DATABASES`

配套调用点改动（`data_insight_agent.py`）：

- `_extract_sql_measures` 的 `read="databricks"` → 从 `ScopeRules` 取
- QUALIFY 改写重试（`_rewrite_invalid_qualify` 与 "Cannot resolve QUALIFY" 触发）**仅当 `source.name == "databricks"` 时启用**；MySQL 下 QUALIFY 会在校验/执行阶段失败并返回明确错误（正确行为）
- 关键词黑名单与 `_profile_query_result` 完全通用，不动

---

## 4. 配置改造 — `src/config/settings.py`

### 4.1 新增环境变量清单

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_SOURCE_TYPE` | `databricks` | `databricks` \| `mysql`；未设/非法 → databricks + warning |
| `MYSQL_HOST` | 必填 | 主机 |
| `MYSQL_PORT` | `3306` | 端口 |
| `MYSQL_USER` | 必填 | 建议仅授予 SELECT |
| `MYSQL_PASSWORD` | 必填 | 密码 |
| `MYSQL_DATABASES` | 空 | 白名单，逗号分隔，首项为默认（对齐 `DATABRICKS_SCHEMAS`） |
| `MYSQL_CHARSET` | `utf8mb4` | 字符集 |
| `MYSQL_POOL_SIZE` | `5` | 连接池常驻连接数 |
| `MYSQL_POOL_MAX_OVERFLOW` | `5` | 池满后允许的临时溢出连接数（总上限 = pool_size + max_overflow） |
| `MYSQL_POOL_RECYCLE_SECONDS` | `3600` | 连接回收周期，规避 MySQL `wait_timeout` 断连 |
| `DATA_MAX_ROWS` | 回退 `DATABRICKS_MAX_ROWS` → `500` | 通用策略上移 |
| `DATA_QUERY_TIMEOUT` | 回退 `DATABRICKS_QUERY_TIMEOUT` → `120` | 同上 |
| `DATA_METADATA_CACHE_TTL_SECONDS` | 回退 `DATABRICKS_METADATA_CACHE_TTL_SECONDS` → `900` | 同上 |

### 4.2 类结构

```python
class DataSourceConfig:
    TYPE = os.getenv('DATA_SOURCE_TYPE', 'databricks').strip().lower()

class MySQLConfig:
    HOST / PORT / USER / PASSWORD / CHARSET / DATABASES / DATABASE(=DATABASES[0])
    POOL_SIZE / POOL_MAX_OVERFLOW / POOL_RECYCLE_SECONDS
    @classmethod
    def is_configured(cls) -> bool:   # HOST and USER and DATABASES

class DataSourcePolicyConfig:
    """源无关策略：优先读 DATA_* 通用名，回退旧 DATABRICKS_* 名。"""
    MAX_ROWS / QUERY_TIMEOUT / METADATA_CACHE_TTL_SECONDS
```

### 4.3 兼容策略 — 双轨别名

语义上通用项应上移，但 `DatabricksConfig.MAX_ROWS` 等被多处 import。采用**双轨别名方案**：

- 新建 `DataSourcePolicyConfig`（读 `DATA_*` 优先、`DATABRICKS_*` 回退）
- `DatabricksConfig` 属性原样保留，同样改读回退链，保证两类值一致，旧引用零破坏
- 效果：旧 `.env` 零迁移成本，新部署可用中性变量名
- `METADATA_AGENT_TIMEOUT_SECONDS` 等本就中性命名，不动

### 4.4 其他

- `validate_config()`：`TYPE==mysql` 时追加 `MySQLConfig.is_configured()` 检查，缺失与 Azure OpenAI 缺失同等报错
- `src/config/__init__.py` 导出新类；`.env.example` 新增注释段
- `requirements.txt` 增加 `SQLAlchemy>=2.0`（Core 引擎/连接池）、`PyMySQL>=1.1.0`（驱动，+ 可选 `cryptography`，用于 MySQL 8 默认的 caching_sha2_password 认证）、`testcontainers[mysql]>=4.0`（测试专用，真实 MySQL 容器）

---

## 5. 调用点修改总表

| 文件 | 改动 |
|---|---|
| `src/agents/data_insight_agent.py` | 删除 L60-62/L361-453 连接管理 → `get_active_data_source().execute_query()`；`_validate_sql_scope` → `scope.validate_sql_scope(sql, get_scope_rules())`；`_extract_sql_measures` 方言参数化；QUALIFY 重试按源门控；身份块 → `build_identity_block()` |
| `src/metadata_catalog.py` | 构造函数加 `provider: Optional[MetadataProvider]=None`（默认走工厂）；`list_schemas`/`list_tables`/`get_table` 改调 provider；`_qualified_table_parts` 按 `ScopeRules.qualified_parts` 分支（databricks 分支逻辑不变）；`_workspace_client`/`_column_payload`/`_tags_payload` 迁至 provider |
| `src/agents/metadata_agent.py` | `_sql_connector_query_metadata` 迁入 databricks.py，三个工具的 except→fallback 嵌套简化为单次调用 + 错误透传；`validate_catalog` MySQL 模式仅接受空 catalog（非空 → out_of_scope，文案说明 MySQL 无 catalog 概念）；工具 description 在 init 期按活跃源插值；身份块 → `build_identity_block()` |
| `src/api/main.py` | `/config` 增加 `data_source: {type, dialect, databases_or_schemas, configured}`；`/health` 增加 `data_source_type` |

---

## 6. 提示词与技能 — 最小改动策略

**策略：运行时身份块仲裁，不做静态模板化。** 动态注入点已存在，静态提示词中的 Databricks 措辞多数是描述性而非行为性。

| 位置 | 改动 |
|---|---|
| 两个 agent 的 `_create_agent` | 硬编码 "## Databricks Context" → `build_identity_block()`（databricks 下字节等价） |
| `src/prompts/master.py` L13 | "SQL/Spark, Delta tables, UC metadata" → "read-only SQL against the configured data source"（1 行） |
| `src/prompts/ontology.py` L29/67/69 | 同上中性化（2-3 行） |
| `src/prompts/metadata.py`、`data_insight.py` | **静态文本不改**，靠身份块仲裁声明覆盖 |
| `skills/sql-planning/SKILL.md` | frontmatter "Databricks SQL" → "read-only SQL (dialect per runtime context)"；L79-87 方言措辞中性化 |
| `skills/metadata-mapping/SKILL.md` | 不改（AdventureWorks 术语对 MySQL 版同样适用） |
| `skills/analytics-spec/.../highest-spending-customer.sql` | **不改**：硬编码三段式表名在 MySQL 下会被 scope 校验 fail-closed 正确拦截，文档标注限制即可 |

---

## 7. Ontology 层影响 — 无代码改动，天然降级

- OWL 生成的物理表候选只是 **candidate claims**（snake_case、`fact_`/`dim_` 前缀）
- MetadataAgent 的 Ontology Verification Mode 本就要求"candidate 是待验证的主张，而非事实"：元数据中找不到 → `rejected`/`unresolved`，不会静默采用
- DataInsightAgent 侧有 `<schema_context>` 权威 + scope 校验双重拦截

配套动作：
1. MySQL 身份块中加提示句（见 3.2）
2. 文档说明：如需 MySQL 专属本体，可放独立 OWL 目录并通过 `ONTOLOGY_DIR` 切换 —— 本次不建表名映射层（超出范围）

---

## 8. 实施阶段（每步可独立验证、可回滚）

| 阶段 | 内容 | 验证 |
|---|---|---|
| **Phase 0** 配置底座 | `requirements.txt`、`settings.py`（三个新配置类 + validate_config）、`config/__init__.py`、`.env.example` | 不设 `DATA_SOURCE_TYPE` 启动无变化；设 `mysql` 且缺 `MYSQL_*` 时 `validate_config()` 报错 |
| **Phase 1** 抽象 + Databricks 平移 | 新建 `data_sources/{__init__,base,databricks}.py`；`metadata_catalog.py` provider 注入化；两个 agent 委托化 | **关键回归点**：全量现有 pytest 通过；databricks 身份块与主分支逐字节比对 |
| **Phase 2** scope 方言化 | 新建 `scope.py`；`data_insight_agent.py` 调用点改造 | databricks scope 行为单测不变（无现成单测则补回归锚） |
| **Phase 3** MySQL 实现 | 新建 `mysql.py`；工厂分支 | 纯逻辑单测 + testcontainers 真实 MySQL 集成测试全绿 |
| **Phase 4** 提示词/API | 身份块接入、prompts 中性化、`/config` `/health`、sql-planning SKILL.md | databricks 模式端到端冒烟不变；mysql 模式 `/config` 正确反映 |
| **Phase 5** 文档收尾 | README/readme-cn/ARCHITECTURE 增加 MySQL 章节（只读授权示例、`MYSQL_DATABASES` 说明、本体与 analytics-spec 限制）+ 测试 | 全量 `pytest test_script/` |

---

## 9. 测试计划

### 现有测试适配（Databricks 侧，沿用既有测试方式最小平移）

- `test_script/test_metadata_catalog.py`：沿用其现有的 UC API 模拟方式，仅把 patch 点从 `MetadataCatalogService._workspace_client` 移到 `DatabricksMetadataProvider` 的 client 构造，测试语义不变
- `test_pipeline.py` / `test_query_engine.py` / `test_api_ontology.py`：默认 databricks 路径，预期零改动通过

### 新增测试 — 纯逻辑单测（不涉及数据库连接）

以下测试只验证代码逻辑（sqlglot 解析、工厂分支、提示词块生成），无任何 I/O，不属于 mock 测试：

| 文件 | 覆盖 |
|---|---|
| `test_data_source_factory.py` | 类型解析、默认回退、非法值告警、单例复用 |
| `test_sql_scope_mysql.py` | 裸表名 BLOCKED、三段式 BLOCKED、白名单外 BLOCKED、白名单内通过、CTE 豁免、反引号表名解析 |
| `test_sql_scope_databricks.py` | 现行为回归锚（三段式/catalog 匹配/schema 白名单） |
| `test_identity_block.py` | databricks 块与旧硬编码字符串逐字节相等；mysql 块含白名单与两段式示例 |

### 新增测试 — 真实 MySQL 集成测试（核心验收，不用 mock）

**`test_script/test_mysql_integration.py`**：用 `testcontainers-python` 拉起一次性真实 MySQL 容器，全部行为在真库上验证。

基础设施：
- `requirements.txt` 增加 `testcontainers[mysql]>=4.0`（测试专用依赖）
- session 级 fixture 启动 `MySqlContainer("mysql:8.0")`，动态映射端口，测试结束容器自动销毁（数据天然清理，不污染任何现有库）
- fixture 在容器内创建白名单测试 database（如 `aw_test`）+ AdventureWorks 风格表（含主外键、decimal/datetime/varchar 各类型、列注释）+ 种子数据
- 无 Docker 环境（CI 未配 Docker 等）自动 skip，本地开发机装 Docker Desktop 即可跑

覆盖清单（全部对真实服务器执行）：

| 类别 | 用例 |
|---|---|
| 连接与执行 | `execute_query` 正常 SELECT 返回 `QueryResult`（columns/rows/row_count）；`max_rows` 截断生效；中文字符（utf8mb4）往返不乱码 |
| 连接池行为 | 并发多线程同时执行查询（池真正并行，无全局锁排队）；`pool_pre_ping` 在连接被服务端杀掉（`KILL CONNECTION`）后自动重建 |
| 只读安全 | `INSERT/UPDATE/DELETE/DROP` 被关键词黑名单拦截；scope 校验拒白名单外 database、拒三段式、拒裸表名；`SET SESSION TRANSACTION READ ONLY` 生效时写语句在数据库层也失败 |
| 元数据往返 | `list_schemas` 只返回白名单内 database；`list_tables` 返回表摘要（full_name 两段式、TABLE_TYPE、注释）；`get_table` 列信息与真实 DDL 一致（完整 COLUMN_TYPE 如 `decimal(10,2)`、nullable、COLUMN_COMMENT）；不存在的表返回 None |
| 端到端 | 白名单内 `db.table` 查询在 scope 校验后真实执行并返回数据 |

### 全量验证命令

```bash
# 前置：本机 Docker Desktop 已启动（真实 MySQL 容器测试需要；未启动时集成测试自动 skip）
ONTOLOGY_ENABLE_REASONER=false venv/bin/python -m pytest test_script -q -p no:cacheprovider

# 单跑真实 MySQL 集成测试
ONTOLOGY_ENABLE_REASONER=false venv/bin/python -m pytest test_script/test_mysql_integration.py -v -p no:cacheprovider
```

---

## 10. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| 1 | **回归风险（最高优先级）**：Phase 1 平移执行/元数据热路径 | 分阶段提交；databricks 身份块与 `/config` 输出做快照比对；Phase 1 单独跑全量现有测试后再进 Phase 2 |
| 2 | sqlglot mysql 方言解析差异（反引号标识符、个别函数 AST 形状） | scope/measures 两处统一从 `ScopeRules.sqlglot_dialect` 取值；专项单测覆盖反引号；sqlglot 版本锁不动 |
| 3 | MySQL `lower_case_table_names` 平台差异（Linux=0 大小写敏感，Windows=1 不敏感） | 白名单与校验统一 casefold 比较；文档提示部署环境差异 |
| 4 | 连接陈旧（`wait_timeout` 断连） | `pool_pre_ping=True` 取用前检测失效连接并自动重建；`pool_recycle` 强制周期回收；`read_timeout` 对齐 QUERY_TIMEOUT |
| 4b | **池化引入的新并发风险**：(a) 池耗尽时请求排队直至 `pool_timeout` 超时；(b) MySQL 服务器 `max_connections`（默认 151）被应用池上限挤占 | 池上限可配（`MYSQL_POOL_SIZE` + `MYSQL_POOL_MAX_OVERFLOW`，默认合计 10，远低于 151）；文档提醒按服务器 `max_connections` 与其他应用占用调整；池等待超时报错可被上层错误反馈机制正常呈现 |
| 5 | QUALIFY 改写仅 Databricks 适用 | 按 `source.name` 门控；MySQL 下校验/执行失败并返回明确错误（提示词已声明方言） |
| 6 | analytics-spec 技能资源硬编码（三段式表名、Spark `date_sub()`） | MySQL 下被 scope 校验 fail-closed 拦截；文档标注限制；后续可按源提供资源变体（范围外） |
| 7 | 凭据管理（MYSQL_PASSWORD 明文 env） | 与 DATABRICKS_TOKEN 同级对待；文档建议只读账号 `GRANT SELECT ON db.* TO ...` + 可选 `SET SESSION TRANSACTION READ ONLY` 纵深防御 |
| 8 | 元数据能力差异（MySQL 无 UC tags/owner/列级标签） | payload 字段省略；消费方只依赖 name/type/nullable/comment，已验证兼容 |
| 9 | MetadataAgent 工具 `catalog` 参数语义悬空 | MySQL 模式下非空 catalog → `out_of_scope` 错误；工具 description 在 init 期按源插值 |

---

## 11. 安全建议（MySQL 部署前置）

```sql
-- 创建只读账号（示例）
CREATE USER 'oda_reader'@'%' IDENTIFIED BY '<password>';
GRANT SELECT ON <your_db>.* TO 'oda_reader'@'%';
FLUSH PRIVILEGES;
```

- 仅将需要暴露的 database 列入 `MYSQL_DATABASES`
- 应用层防线：关键词黑名单 + sqlglot 只读语法校验 + 白名单作用域检查 + 可选 `SET SESSION TRANSACTION READ ONLY`
- 网络层建议：MySQL 仅对应用服务器开放访问
