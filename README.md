# Ontology Data Agent

English | [简体中文](readme-cn.md)

An intelligent, enterprise-grade data analytics system powered by an OpenAI-compatible LLM API (DeepSeek by default), Microsoft Agent Framework (MAF), an OWL business ontology, and a configurable Databricks or MySQL data source.

## 🌟 Features

### Core Capabilities
- **Multi-Agent Architecture**: MasterAgent orchestrates three specialized agents — OntologyAgent, MetadataAgent, and DataInsightAgent — each with domain-specific tools
- **MasterAgent Agentic Loop**: One bounded MAF function loop repeats model → Agent/tool → observation until the model emits a final answer without another tool call
- **Skill System**: Native MAF `SkillsProvider` advertises agent-scoped skills and loads full instructions or indexed resources on demand
- **Data Insight**: DataInsightAgent executes read-only natural-language-to-SQL queries against an Azure Databricks SQL Warehouse
- **Metadata Browsing and Recall**: MetadataAgent uses Unity Catalog table summaries for deterministic candidate recall, batch-fetches candidate details, and retains UC tools for model-driven gap recovery
- **Skill- and Ontology-Guided Analytics**: OntologyRouter checks governed Skills; ordinary enabled requests use deterministic, question-driven OWL evidence, UC physical verification, and a dynamically loaded planning Skill so the primary model derives SQL at runtime
- **Session-Scoped Ontology Mode**: Each chat session independently enables or disables ontology enrichment; failures are shown in the thinking panel and fall back to the standard metadata-driven workflow
- **User-Authored Business Layer**: Business users edit a workspace semantic document in the UI (terminology, metric definitions, reporting rules); it is stored under `data/`, injected into every analytical request, takes effect without a restart, and complements the OWL ontology in both ontology modes
- **Multi-turn Conversations**: Context-aware dialogue with MAF in-memory thread store; each browser session gets an isolated thread
- **Concurrent Sessions**: Each thread has independent messages, loading state, MAF history, cancellation, and can run alongside other threads
- **Stop & Session Cache**: Stop cancels only the active thread; exact repeated questions can reuse a completed answer from the same session without external calls
- **Streaming SSE Responses**: FastAPI streams `thinking`, `text`, `answer_reset`, `thinking_done`, `stopped`, `done`, and `error`

### Technology Stack
- **LLM routing**: `OPENAI_MODEL` for Master, Ontology routing/recovery, and DataInsight; `OPENAI_SMALL_MODEL` for Metadata discovery/verification. Defaults: DeepSeek V4 Pro and V4 Flash
- **Agent Framework**: Microsoft Agent Framework 1.11 — `OpenAIChatCompletionClient`
- **Primary Frontend**: React + TypeScript (Vite, port 3000)
- **Backend API**: FastAPI with Server-Sent Events (port 8000)
- **Data Analytics**: Azure Databricks Unity Catalog plus SQL Warehouse through the Databricks SQL connector
- **Ontology Runtime**: Owlready2 with read-only recursive OWL loading; optional HermiT reasoning is disabled by default
- **Ontology Management**: Online upload/edit/validate/activate with immutable published releases (see [docs/ONTOLOGY-MANAGEMENT.md](docs/ONTOLOGY-MANAGEMENT.md)); MySQL schema-draft generation included
- **Observability**: Structured activity streaming and rotating application logs, suitable for external evaluation pipelines
- **Agent Skills**: Extend the agent’s capabilities using agent skills, enabling the agent to analyze data based on real-world business rules.
- **Unified Data Platform**: Combines verified physical metadata with an existing OWL business ontology while preserving their separate authority boundaries.

## 🏗️ Architecture

```mermaid
flowchart TD
    User(["👤 User"])

    subgraph UI["Frontend"]
        direction LR
        React(["React + TypeScript\nport 3000"])
    end

    subgraph Backend["FastAPI Backend · port 8000"]
        API["SSE /chat/stream"]
    end

    subgraph Skills["Skill System"]
        direction LR
        SP["MAF SkillsProvider"]
        FS["FileSkillsSource"]
        FS --> SP
    end

    subgraph AgentLayer["Agent Layer — Microsoft Agent Framework · OpenAI-compatible API"]
        MA(["🧠 MasterAgent\nBounded agentic loop"])
        OA(["OntologyRouter + OntologyAgent"])
        DIA(["📊 DataInsightAgent"])
        META(["🗂️ MetadataAgent"])
        MA --> OA & DIA & META
    end

    subgraph ModelServices["Model Services"]
        AOAI["☁️ DeepSeek / OpenAI-compatible API\nprimary + small models"]
        AIF["Azure AI Foundry\nOptional external evaluation"]
    end

    subgraph Databricks["Azure Databricks"]
        SQLW["⚡ SQL Warehouse"]
        UC["📚 Unity Catalog"]
        SQLW --- UC
    end

    User --> React
    React -->|SSE stream| API
    API --> MA

    SP -.->|agent-scoped skills| OA & DIA & META

    OA --> OWL[("Ontology/*.owl")]
    DIA --> SQLW
    META --> UC & SQLW
    MA & OA & DIA & META --> AOAI
    API -.->|exported logs, when configured externally| AIF
```

## 📁 Project Structure

```
Data-Insight-Agent-With-Ontology/
├── src/
│   ├── agents/
│   │   ├── master_agent.py      # Orchestration agent; tools: delegate_metadata,
│   │   │                        #   delegate_data_analysis
│   │   ├── data_insight_agent.py# Databricks SQL; execute_sql + bounded
│   │   │                        #   context recovery; governed + dynamic planning Skills
│   │   ├── metadata_agent.py    # Unity Catalog schema; tools: list_schemas,
│   │                            #   list_tables, get_table_details, search_tables;
│   │                            #   native Skill: metadata-mapping
│   │   ├── ontology_agent.py    # Owlready2 semantic entity/property/path tools
│   │   └── maf_runtime.py       # MAF 1.11 client/session/stream adapter
│   ├── ontology/
│   │   └── service.py            # Read-only OWL loading, indexing, and graph queries
│   ├── metadata_catalog.py       # Unity Catalog SDK access, object cache,
│   │                            #   candidate detail batch fetch, SQL identifier checks
│   ├── query_engine.py          # Request-scoped MasterAgent observations
│   │                            #   and streaming context
│   ├── api/
│   │   └── main.py              # FastAPI server: SSE /chat/stream + REST endpoints
│   ├── prompts/                 # Per-agent system prompts:
│   │   └── master.py · ontology.py · data_insight.py · metadata.py
│   ├── config/
│   │   └── settings.py          # OpenAIConfig, AzureAIFoundryConfig,
│   │                            #   DatabricksConfig, OntologyConfig, AppConfig
│   ├── skills_provider.py       # Agent-scoped native MAF SkillsProvider factory
│   ├── business_layer.py        # Workspace business semantic document store (data/)
│   └── utils/
│       └── logger.py            # Logging utilities
├── skills/
│   ├── analytics-spec/          # Skill: data analytics query patterns
│   │   ├── SKILL.md             # Intent routing + resource index
│   │   └── references/
│   │       └── highest-spending-customer.sql
│   ├── sql-planning/   # Skill: dynamic OWL + UC SQL planning method
│   │   └── SKILL.md
│   └── metadata-mapping/        # Skill: Unity Catalog metadata conventions
│       └── SKILL.md
├── Ontology/
│   └── aw_ontology.owl          # Read-only business ontology and defined classes
├── frontend/                    # React + TypeScript (Vite)
│   ├── src/
│   │   ├── App.tsx              # Main chat UI + activity panel
│   │   ├── services/api.ts      # SSE client connecting to FastAPI backend
│   │   ├── types.ts             # Chat/session/runtime TypeScript definitions
│   │   └── types/activity.ts    # Activity stream definitions
│   ├── package.json
│   └── vite.config.ts
├── data/                        # Local data sources (incl. business_layer.md, git-ignored)
├── tmp/                         # Temporary files
├── logs/                        # Application logs (application_YYYYMMDD.log)
├── run.sh                       # Launcher: full-stack, backend, frontend
├── stop.sh                      # Stops locally launched backend/frontend processes
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variables template
└── README.md                    # This file
```

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- Node.js 18.18+ (for React frontend tooling)
- Java 11+ only when `ONTOLOGY_ENABLE_REASONER=true`; explicit OWL queries do not require startup reasoning
- An OpenAI-compatible API key (DeepSeek is the default provider)
- An Azure AI Foundry project only if logs are exported to an external evaluation workflow
- One analytical data source (optional, for data insight):
    - Azure Databricks with Unity Catalog SQL Warehouse (`DATA_SOURCE_TYPE=databricks`, default)
    - MySQL 8.0+ (`DATA_SOURCE_TYPE=mysql`)

### Installation

```bash
# Install all Python and Node.js dependencies
./run.sh install
```

Or manually:
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your model and data-source credentials
```

Minimum required variables (see `.env.example` for full list):
```
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=<your-key>
OPENAI_MODEL=deepseek-v4-pro
OPENAI_SMALL_MODEL=deepseek-v4-flash
```

### Data Source Selection

The backend talks to exactly one analytical data source, selected at startup:

```
DATA_SOURCE_TYPE=databricks   # default; also used when unset or invalid
DATA_SOURCE_TYPE=mysql        # MySQL 8.0+
```

**MySQL mode** — add:
```
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=readonly_user
MYSQL_PASSWORD=********
MYSQL_DATABASES=sales,reporting   # allowlist; first entry is the default database
```

MySQL connection settings can also be entered from the **MySQL 配置** button in the chat header. The backend verifies the connection, activates it immediately, and saves it to its local `.env` file. Active queries must finish before a configuration change; existing chat sessions start fresh on their next question. The password is never returned to the browser, and leaving its field empty keeps the existing password.

Rules enforced in MySQL mode:

- Table references must be two-part `database.table` and every database must be inside `MYSQL_DATABASES`; bare table names and three-part `catalog.database.table` forms are rejected.
- Queries are read-only: a blocklist/sqlglot validation layer plus a per-session `SET SESSION TRANSACTION READ ONLY` defense-in-depth on every pooled connection.
- SQL is planned in the MySQL dialect — `QUALIFY` is unavailable.
- Generic policy vars (`DATA_MAX_ROWS`, `DATA_QUERY_TIMEOUT`, `DATA_METADATA_CACHE_TTL_SECONDS`) fall back to the legacy `DATABRICKS_*` names, so existing `.env` files keep working.

Recommended read-only MySQL account:
```sql
CREATE USER 'readonly_user'@'%' IDENTIFIED BY '********';
GRANT SELECT ON sales.* TO 'readonly_user'@'%';
GRANT SELECT ON reporting.* TO 'readonly_user'@'%';
-- Plus metadata visibility:
GRANT SELECT ON information_schema.TABLES TO 'readonly_user'@'%';
GRANT SELECT ON information_schema.COLUMNS TO 'readonly_user'@'%';
```

Limitations in MySQL mode:

- `analytics-spec` governed templates that hard-code three-part `catalog.schema.table` names will be blocked by scope validation; author MySQL-specific specs with two-part names or rely on dynamic `sql-planning`.
- The shipped AdventureWorks OWL ontology carries Databricks mapping candidates; they remain *candidate claims* that MetadataAgent must verify, and `ONTOLOGY_DIR` can point at a MySQL-specific ontology when available.

### Running the Application

```bash
./run.sh             # Full stack: FastAPI (port 8000) + React (port 3000)
./run.sh backend     # FastAPI only
./run.sh frontend    # React dev server only
./stop.sh            # Stop locally launched backend/frontend processes
```

Access the React UI at `http://localhost:3000`.

## 💡 Usage

### Chat Interface (React)

1. **Ask Questions**: Type your question and press Enter or click Send
2. **New Conversation**: Click "New Session" to start a fresh MAF session
3. **Streaming Responses**: Working steps arrive through SSE. Final answers are released after evidence and output-format checks; raw tool protocol is never an answer.

### Analysis reliability

- An unmatched or empty ontology lookup falls back to metadata discovery, including the scoped `metadata-mapping` Skill. Loading an OWL file alone does not establish relevant business semantics.
- DataInsight uses `execute_sql` with `exploration`, `analysis`, or `diagnostic` purpose. Successful queries receive request-local evidence IDs; unchanged successful queries can reuse results within that request.
- `complete_analysis` submits the answer, evidence references, and gaps within the existing bounded loop. Outcomes are `completed`, `partial`, `insufficient`, or `failed`. Missing rules or mappings must remain visible; co-occurrence alone does not establish a violation.
- `done.analysis_status` and activity metrics expose the outcome. Only completed, otherwise eligible answers enter the session cache. Old cache entries without a completion status are not reused.
- Function and model limits remain unchanged. The last available function slot is reserved for completion; diagnostics do not start another model loop. Logs distinguish invocation, validation, database execution, reuse, and termination without adding business rows.
- Reaching a fetch cap, showing only a sample, or using SQL LIMIT conservatively marks evidence as limited. A complete aggregate or an explicitly stated limited scope is required. These checks verify execution evidence, not the truth of every model-authored business interpretation.
4. **Ontology mode**: Use the sidebar switch to enable ontology enrichment for the current session only
5. **Business Layer Doc**: Click the button in the chat header to edit the workspace semantic document (terminology, metric definitions, reporting conventions). It is shared by every session and applies from your next question — no restart required. Prefer recording what the OWL ontology does *not* already define, and note that verified Databricks schema always takes precedence.

### Question Types

| Type | Example | Routed To |
|------|---------|-----------|
| Data analytics | "按地区比较订单数、销量、销售额和平均客单价" | Data analysis pipeline |
| Schema discovery | "What tables are available in the silver schema?" | MetadataAgent |

### Feature Toggles

Ontology is initialized from `.env` and then controlled per frontend session:

| Flag | Default | Effect |
|------|---------|--------|
| `DEFAULT_ENABLE_ONTOLOGY` | `true` | Initial Ontology switch value for each new chat session |

With Ontology enabled, analytical questions start in `OntologyRouter` on the primary deployment. A confirmed governed template match skips Owlready2 and MetadataAgent, then DataInsightAgent loads the named Skill and indexed resource. Otherwise code calls the question-driven OWL composite lookup and defined-class lookup directly; only weak results escalate to the full OntologyAgent tool loop. Metadata then recalls and batch-fetches UC candidates before its small-model verification turn, and DataInsightAgent loads `sql-planning` to choose analytical roles, grain, comparisons, and SQL.

With Ontology disabled or unavailable, analytical questions run `MetadataAgent (progressively loads metadata-mapping) → DataInsightAgent`. Every non-governed DataInsight request loads `sql-planning`; without Ontology it applies the same dynamic method using the original question and verified metadata only, without inventing semantic evidence. MasterAgent, OntologyRouter/OntologyAgent, and DataInsightAgent use `OPENAI_MODEL`; both Metadata modes use `OPENAI_SMALL_MODEL`.

## ⚙️ Configuration Reference

All configuration classes are in `src/config/settings.py`:

- `OpenAIConfig` — OpenAI-compatible base URL, API key, primary model, and small model
- `AzureAIFoundryConfig` — optional connection-string placeholder for deployment-specific integrations
- `DataSourceConfig` — active data-source type (`DATA_SOURCE_TYPE=databricks|mysql`, default `databricks`)
- `DatabricksConfig` — workspace host, token, SQL warehouse HTTP path, Unity Catalog allowlist, query limits, metadata cache, recall index/candidate bounds, and small-schema fallback bound
- `MySQLConfig` — host, port, user, password, charset, `MYSQL_DATABASES` allowlist, and SQLAlchemy pool sizing/recycle
- `DataSourcePolicyConfig` — source-agnostic `DATA_MAX_ROWS` / `DATA_QUERY_TIMEOUT` / `DATA_METADATA_CACHE_TTL_SECONDS` (falling back to legacy `DATABRICKS_*` names)
- `OntologyConfig` — OWL directory/glob, local-only loading, optional reasoner, query limits, fuzzy threshold, escalation confidence, and agent timeout
- `AppConfig` — log level, MAF function-loop budgets, feature flag defaults, directory paths

## 📊 Evaluation

The application does not currently register an Azure AI Foundry or Application Insights exporter. Export logs from `logs/` into your evaluation system for:
- Groundedness, relevance, coherence metrics
- A/B testing: ontology enrichment on/off

## 🗺️ Product Backlog

This backlog records planned work only; none of the items below should be treated as implemented. GitHub Issues are the execution record, while this section remains the public roadmap summary.

Status: `Planned` · `Ready` · `In progress` · `Blocked` · `Done`

| ID | Priority | Status | Backlog item | Definition of done | Depends on |
|---|---|---|---|---|---|
| [`ODA-001`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/1) | P0 | Planned | **Authentication and tenant isolation** | Add user login, backend token validation, user/tenant ownership checks for every session and run, role-based access to governed SQL, and authorization tests proving one user cannot read, stop, or delete another user's work. | — |
| [`ODA-002`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/2) | P0 | Planned | **Durable sessions and multi-worker readiness** | Move MasterAgent session metadata, conversation history, response cache, and active-run state out of process-local dictionaries; support multiple workers or pods without losing routing, history, or stop requests. | `ODA-001` |
| [`ODA-003`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/3) | P0 | Planned | **Per-question run tracking and trace UI** | Assign every question an immutable `run_id`; persist its route, ontology mode, agent stages, tool calls, sanitized inputs/outputs, SQL/query ID, timings, result status, and errors; add a dedicated UI tab for inspecting each run. | `ODA-001`, `ODA-002` |
| [`ODA-004`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/4) | P0 | Planned | **End-to-end observability** | Add OpenTelemetry-compatible traces, metrics, and structured logs across API, MasterAgent, child agents, tools, the configured LLM API, and Databricks; correlate all telemetry by `run_id`, `thread_id`, and user/tenant while redacting secrets and sensitive data. | `ODA-003` |
| [`ODA-005`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/5) | P0 | Planned | **Code-enforced run safety and cancellation** | Enforce at most one `delegate_data_analysis` call per turn in code, make client disconnect set the cancellation event, propagate cancellation to child tasks and Databricks statements, and make retryable operations idempotent. | `ODA-002`, `ODA-003` |
| [`ODA-006`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/6) | P1 | Planned | **User-configured governed question + SQL** | Provide an authenticated UI/API for users to create, test, version, enable, and retire question-to-SQL rules; validate read-only, allowlisted, fully qualified SQL; record ownership and audit history; route matched rules through a governed contract rather than executing arbitrary text directly. | `ODA-001`, `ODA-002`, `ODA-003` |
| [`ODA-007`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/7) | P1 | Planned | **Chart generation for suitable answers** | Return a typed visualization specification alongside tabular results when a chart is useful; render supported chart types in the UI with accessible table fallback, preserve units/labels, and avoid inventing dimensions or series absent from the SQL result. | `ODA-003` |
| [`ODA-008`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/8) | P1 | Planned | **User-scoped persistent memory** | Persist user-approved preferences, business terminology, recurring analysis settings, and compact conversation summaries; isolate memory by user/tenant, expose inspect/edit/delete controls, track provenance, and never treat memory as verified Unity Catalog or ontology evidence. | `ODA-001`, `ODA-002` |
| [`ODA-009`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/9) | P1 | Planned | **Reliable multi-turn answer continuity** | Make follow-up questions reliably reference prior DataInsight results by storing a bounded structured result summary in MasterAgent-visible history; keep cached responses and the MAF session consistent instead of allowing their histories to diverge. | `ODA-002`, `ODA-003` |
| [`ODA-010`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/10) | P1 | Planned | **Bounded inter-agent evidence contracts** | Version and validate the Ontology→Metadata→DataInsight payload schemas, apply configurable token/character budgets, preserve provenance for retained evidence, and explicitly report lossy pruning. | `ODA-003` |
| [`ODA-011`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/11) | P2 | Planned | **Concurrent Databricks execution** | Replace the process-wide SQL connection and global execution lock with bounded connection pooling or request-scoped connections; preserve query cancellation, timeout, retry, and per-user limits under concurrency. | `ODA-001`, `ODA-004` |
| [`ODA-012`](https://github.com/tianputao/Data-Insight-Agent-With-Ontology/issues/12) | P2 | Planned | **Durable cross-process run recovery** | Checkpoint completed pipeline stages so another worker can safely resume an interrupted run without repeating completed ontology/metadata work or duplicating side effects; use leases and idempotency keys to prevent double recovery. | `ODA-002`, `ODA-003`, `ODA-005` |

### Backlog Guardrails

- OWL remains authoritative for formal business semantics; Unity Catalog remains authoritative for physical tables, columns, types, and permissions.
- User-configured SQL must pass the same read-only and catalog/schema enforcement as model-generated SQL, with stricter ownership and approval controls where required.
- Tracking, observability, and memory must redact credentials, tokens, sensitive rows, and unapproved model reasoning before persistence.
- Persistent memory is advisory context, not an automatic source of truth, and users must be able to review and delete it.
- Chart specifications must be derived from executed result data and must always retain a readable tabular fallback.

## ✅ Validation

```bash
ONTOLOGY_ENABLE_REASONER=false venv/bin/python -m pytest test_script -q -p no:cacheprovider
npm --prefix frontend run lint
npm --prefix frontend run build
venv/bin/python -m pip check
npm --prefix frontend audit
```


## 📝 Logging

Logs in `logs/application_YYYYMMDD.log`:
- Agent decisions, tool calls, ontology lookups, SQL executions, errors


## 📄 License

This project is provided as-is for enterprise use.

## 🤝 Contributing

For questions or contributions, please contact the development team.

---

**Built with ❤️ using Azure AI, Microsoft Agent Framework, and Azure Databricks**
