# AGENTS.md

本文件适用于整个仓库，供自动化编码代理和贡献者快速、安全地修改本项目。若子目录将来出现更具体的 `AGENTS.md`，以离目标文件最近的说明为准。

## 项目概览

Ontology Data Agent 是一个面向企业分析的多代理应用：React 前端通过 SSE 调用 FastAPI，后端使用 Microsoft Agent Framework 和 Azure OpenAI 协调 Ontology、Metadata、DataInsight 三类能力，并在 Azure Databricks 或 MySQL 上执行受限的只读 SQL。

主要技术栈：

- Python 3.10+、FastAPI、Pydantic、pytest
- Microsoft Agent Framework 1.11、Azure OpenAI
- Owlready2（OWL 本体查询）
- Databricks SDK / SQL Connector，或 SQLAlchemy + PyMySQL
- React 18、TypeScript、Vite、ESLint

## 先阅读这些文件

开始修改前，根据任务范围阅读：

- `README.md`：功能、启动方式、配置、验证命令和产品约束
- `ARCHITECTURE.md`：组件关系、调用链、证据传递和扩展点
- `src/api/main.py`：API、SSE 协议、线程/运行状态和应用生命周期
- `src/agents/master_agent.py`：总路由及代理流水线
- `src/agents/data_insight_agent.py`：规划、SQL 执行和结果诊断
- `src/metadata_catalog.py`：元数据召回、缓存和物理标识符校验
- `src/ontology/service.py`：OWL 的只读加载、索引和图查询
- `src/data_sources/`：Databricks/MySQL 适配器及 SQL 作用域规则
- `src/config/settings.py` 与 `.env.example`：所有运行时配置
- `skills/*/SKILL.md`：运行时代理技能契约；它们不是本文件的开发说明

## 目录职责

```text
src/api/                 FastAPI 路由、SSE、会话及取消逻辑
src/agents/              Master/Metadata/Ontology/DataInsight 代理与 MAF 适配层
src/data_sources/        数据源协议、工厂、Databricks/MySQL 实现和 SQL scope 校验
src/ontology/            OWL 读取与语义查询服务
src/prompts/             各代理系统提示词
src/config/              环境变量解析和配置对象
src/utils/               日志与可流式展示的 activity 事件
skills/                  运行时渐进披露的 Agent Skills
Ontology/                业务 OWL 文件；默认只读
frontend/src/            React 页面、组件、类型和 API/SSE 客户端
test_script/             pytest 单元、契约、流式、管道和集成测试
data/                    本地业务层文档等敏感运行时数据；默认被忽略
logs/, tmp/              运行产物；不要提交
```

## 必须保持的系统约束

### 语义与元数据边界

- OWL 是正式业务语义、关系、约束和 lineage 的权威来源。
- 数据源元数据是物理 catalog/database、schema、table、column、type 和权限的权威来源。
- OWL 中的物理映射只能作为候选，生成 SQL 前必须用物理元数据验证；不要让提示词或模型猜测不存在的列。
- `data/business_layer.md` 是用户维护的补充语义，不得覆盖已验证的物理结构，也不能被提升为 OWL 等价事实。
- 默认只读加载 `Ontology/**/*.owl`。除非任务明确要求，不要重写或格式化 OWL 文件，也不要默认启用 HermiT。

### 代理流水线

- 保持 MasterAgent 的有界 agentic loop，不要引入无上限重试、递归委派或隐藏的额外模型循环。
- 开启 ontology 时，标准动态路径是 Ontology → Metadata 验证 → DataInsight；命中 governed skill 时可以走受控捷径。
- ontology 关闭或不可用时，必须能回退到 metadata-driven 工作流；取消操作不能被误当作失败并触发回退。
- MetadataAgent 负责候选召回和物理验证，DataInsightAgent 负责最终规划与执行；不要把职责悄悄移到 MasterAgent。
- 修改代理输出、工具名称、activity 或提示词时，同时检查流水线测试及前端可见事件。

### SQL 与数据安全

- 所有数据查询必须只读。不要削弱语句 blocklist、`sqlglot` 解析、作用域验证或数据库层只读保护。
- Databricks 物理表必须使用 `catalog.schema.table`，且在配置的 catalog/schema allowlist 内。
- MySQL 物理表必须使用 `database.table`，且在 `MYSQL_DATABASES` allowlist 内；MySQL 没有 catalog 层。
- CTE 名可不限定，但其引用的物理表仍须完整限定和校验。
- 保持 `QueryResult(columns, rows, row_count, sql)` 和 `DataSource`/`MetadataProvider` 协议的一致性。
- 新数据源应实现协议、提供 `ScopeRules`、接入工厂，并新增作用域、只读、结果契约及配置测试。

### API、流式与并发

- `/chat/stream` 的 SSE 类型是前后端契约：`thinking`、`text`、`answer_reset`、`thinking_done`、`stopped`、`done`、`error`。
- 每个 thread 同时最多一个活动 run；不同 thread 可并发，状态、缓存、取消和历史不得串线。
- `stopped` 不应再发送 `done`；异常路径必须产生可消费的终止事件。
- 响应缓存仅限同一会话、同一标准化问题及相同 ontology 模式；失败、超时、不完整答案不得缓存。
- 当前 thread、history、cache 和 active run 是进程内状态。除非任务明确解决持久化/多 worker 问题，不要宣称其已支持多实例。
- 更改 Pydantic 请求/响应或 SSE payload 时，同步更新 `frontend/src/types.ts`、`frontend/src/types/activity.ts`、API 客户端及测试。

### Skills 与提示词

- `skills/` 下的内容会成为运行时模型指令，修改时按生产代码审查，而不是普通文档编辑。
- 保持 agent-scoped skill 暴露和渐进加载；不要把全部技能正文无条件塞入所有提示词。
- governed SQL 必须来自技能索引声明的资源，并通过与动态 SQL 相同的只读和 scope 校验。
- 提示词中的安全约束需要代码强制；不要仅依赖自然语言提示实现访问控制。

## 配置与秘密

- 本地配置来自 `.env`；只提交 `.env.example` 中的占位符。
- 不读取、打印或提交真实 Azure OpenAI key、Databricks token、MySQL 密码、查询敏感行或本地业务层内容。
- 新增环境变量时，更新 `src/config/settings.py`、`.env.example` 和相关文档/测试。
- 保持既有 `DATA_*` → `DATABRICKS_*` 策略配置回退，除非明确执行破坏性配置迁移。
- 配置类多在导入时读取环境变量；测试变更配置时需 patch 对应类属性或安全地重载，并恢复全局单例。
- Azure/Databricks/MySQL 未配置时，尽量保持应用可启动并在调用相关能力时返回明确错误。

## 开发与运行

仓库的 `run.sh` 面向 Bash（Linux/macOS/WSL/Git Bash）：

```bash
./run.sh install
cp .env.example .env
./run.sh             # backend :8000 + frontend :3000
./run.sh backend
./run.sh frontend
./stop.sh
```

在 Windows PowerShell 中可直接使用现有虚拟环境：

```powershell
.\venv\Scripts\python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
npm --prefix frontend run dev -- --port 3000 --strictPort
```

前端开发服务器把 `/api` 代理到 `http://localhost:8000`。从仓库根目录执行 Python 命令，保证 `src` 导入路径正确。

## 验证要求

优先运行与修改直接相关的最小测试，再在交付前扩大验证范围。不要仅因外部服务未配置就跳过可离线执行的测试。

完整的本地验证：

```powershell
$env:ONTOLOGY_ENABLE_REASONER='false'
.\venv\Scripts\python.exe -m pytest test_script -q -p no:cacheprovider
npm --prefix frontend run lint
npm --prefix frontend run build
.\venv\Scripts\python.exe -m pip check
```

Bash 等价命令：

```bash
ONTOLOGY_ENABLE_REASONER=false venv/bin/python -m pytest test_script -q -p no:cacheprovider
npm --prefix frontend run lint
npm --prefix frontend run build
venv/bin/python -m pip check
```

常用定向测试：

- API/SSE/会话：`test_api_ontology.py`、`test_streaming.py`、`test_query_engine.py`
- 代理编排：`test_pipeline.py`
- 本体：`test_ontology_service.py`
- 元数据与 scope：`test_metadata_catalog.py`、`test_sql_scope_*.py`
- 数据源：`test_data_source_factory.py`
- Skills：`test_skills.py`
- 前端：至少执行 `npm --prefix frontend run lint` 和 `npm --prefix frontend run build`

`test_mysql_integration.py` 使用 testcontainers，需要可用的 Docker daemon；环境不具备条件时应如实报告 skip/未运行，不能把它描述为通过。启用 HermiT 的测试需要 Java 11+，正常测试默认令 `ONTOLOGY_ENABLE_REASONER=false`。

## 修改约定

- 遵循现有 Python 类型标注、dataclass/Pydantic 模型和异步边界；不要在 async 请求路径直接加入长时间阻塞操作。
- 保持改动聚焦；不要顺手重排大型提示词、OWL 或架构文档。
- 修改行为时新增或更新回归测试，尤其覆盖失败、取消、超时、越权 scope 和并发隔离路径。
- 后端新增用户可见状态时使用结构化 activity/SSE，不要只写日志；日志不得包含凭据或未经裁剪的敏感结果。
- 前端保持 TypeScript strict 模式，不使用 `any` 绕过事件/接口契约。
- 不编辑 `venv/`、`frontend/node_modules/`、`logs/`、`tmp/` 或生成的构建产物。
- 不覆盖工作区中与当前任务无关的未提交修改；先检查 `git status --short`，只提交自己负责的文件。

## 完成标准

交付前应确认：

1. 实现遵守上述语义、代理、SQL、流式和会话边界。
2. 相关测试已运行；外部依赖导致的未运行项已明确说明。
3. API、前端类型、配置样例、Skills 和文档在契约变化时保持同步。
4. 没有提交秘密、本地数据、日志、缓存、虚拟环境或构建产物。
5. 最终说明列出修改内容、验证结果及仍存在的环境限制。
