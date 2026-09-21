# Architecture Documentation

## 🏛️ System Architecture

### Overview

The Ontology Data Agent uses a **multi-agent orchestration pattern** built on the Microsoft Agent Framework (MAF). The system combines read-only OWL business semantics (Owlready2) and structured data analytics from one configured backend—Azure Databricks or MySQL—through a MasterAgent plus three specialized agents, a plugin-based skill system, and a streaming FastAPI backend.

## 📊 Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                React + TypeScript Frontend (Vite, port 3000)     │
└──────────────────────────────┬───────────────────────────────────┘
                               │  SSE / REST  (FastAPI, port 8000)
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                FastAPI Backend  (src/api/main.py)                │
│   POST /chat/stream   POST /threads/new   GET /skills  …         │
│   MAF FileSkillsSource discovery; per-request SSE stream         │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                  MasterAgent  (primary LLM model)                │
│  ┌──────────────────┐  ┌────────────────┐                        │
│  │delegate_metadata │  │delegate_data   │  SkillsProvider scopes │
│  │                  │  │_analysis       │  skills per sub-agent  │
│  └──────────────────┘  └────────────────┘                        │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ Specialized agents                                               │
│ OntologyAgent │ MetadataAgent │ DataInsightAgent                 │
│ Owlready2     │ Active source │ Read-only SQL                    │
│                                                                  │
│ Analytics: OntologyRouter? → OWL lookup → Metadata → DataInsight │
│ Ontology failure: visible fallback → MetadataAgent → DataInsight │
└──────────────────────────────┬───────────────────────────────────┘
                               │
              ┌────────────────┴──────────────────┐
              ▼                                   ▼
    ┌──────────────────┐            ┌──────────────────────┐
    │ Ontology/**/*.owl│            │ Active data source   │
    │ read-only local  │            │ Databricks or MySQL  │
    └──────────────────┘            └──────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Model / evaluation services                │
│  ┌───────────────────┐  ┌───────────────────────────────────┐    │
│  │ OpenAI-compatible │  │  Azure AI Foundry                 │    │
│  │ API (DeepSeek by  │  │  Optional external evaluation     │    │
│  │ default)          │  └───────────────────────────────────┘    │
│  └───────────────────┘                                           │
└──────────────────────────────────────────────────────────────────┘
```

## 🧩 Component Details

### 1. Frontend Layer

**React + TypeScript** (Vite, `frontend/`)
- Chat interface with streamed working activities and validated final answers
- "Thinking" step panel showing live agent reasoning
- Per-session conversation threads via `/threads/new` REST call
- Per-session Ontology switch initialized from the backend environment default
- Workspace **Business Layer Doc** editor in the chat header

### 2. FastAPI Backend (`src/api/main.py`)

**Endpoints**:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat/stream` | SSE streaming chat (main endpoint) |
| `POST` | `/threads/new` | Create a new MAF conversation thread |
| `GET` | `/threads` | List all active threads |
| `GET` | `/threads/{id}/history` | Message history for a thread |
| `DELETE` | `/threads/{id}` | Delete a thread |
| `POST` | `/threads/{id}/stop` | Cancel the active run of one thread only |
| `GET` | `/business-layer` | Read the workspace business semantic document |
| `PUT` | `/business-layer` | Save the workspace business semantic document |
| `GET` | `/skills` | List registered skills |
| `GET` | `/config` | Non-sensitive runtime, ontology, and active data-source status |
| `GET` | `/health` | Health check, including `data_source_type` and source capability |

**SSE event types** streamed to frontend:

| Type | Payload | Meaning |
|------|---------|---------|
| `thinking` | `{"message": "..."}` | Agent reasoning step |
| `text` | `{"content": "..."}` | Response token chunk |
| `answer_reset` | `{}` | Retract text when a following tool call proves it was working narration |
| `done` | `{"content": "<final answer>", "analysis_status": "completed\|partial\|insufficient\|failed"}` | Stream complete; authoritative final answer and optional completion status |
| `stopped` | `{"message": "..."}` | Current thread task was cancelled by the user |
| `error` | `{"message": "..."}` | Error description |

**Lifecycle**: A single `MasterAgent` is created at startup, while each browser thread owns an isolated MAF `AgentSession`. Each user turn creates a request-local `QueryEngineContext`; `ContextVar` propagation keeps tool outcomes, original intent, and the SSE sink isolated across concurrent sessions. Agent-scoped native `SkillsProvider` instances advertise and load repository Skills.

**Session isolation and concurrency**:
- Frontend messages, loading state, and `AbortController` are keyed by `thread_id`; switching sessions never redirects an in-flight stream into another session.
- Ontology mode is keyed by `thread_id` in the frontend and captured in each chat request; one session cannot change another session's workflow.
- Backend `active_runs` permits one active task per thread while different MAF `AgentSession` objects execute concurrently.
- `POST /threads/{thread_id}/stop` cancels only that thread's MAF run and signals cooperative cancellation to delegated-agent worker waits.

**Memory and cache**:
- MAF `AgentSession` retains conversation history for contextual follow-up questions within one thread. A new thread starts with a separate history and memory state.
- The application also keeps a process-local, thread-scoped exact response cache. A normalized repeated question within `SESSION_RESPONSE_CACHE_TTL_SECONDS` returns the completed answer only when its ontology mode also matches.
- Cache entries never cross thread boundaries and are cleared when the backend process restarts or the thread is deleted.

### 3. MasterAgent (`src/agents/master_agent.py`)

**Framework**: MAF 1.11 `OpenAIChatCompletionClient.as_agent()` with in-memory `AgentSession`

**LLM connection**: `OpenAIConfig` supplies `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, and `OPENAI_SMALL_MODEL` to MAF's `OpenAIChatCompletionClient`. DeepSeek is the default OpenAI-compatible endpoint; another compatible provider can be selected entirely through environment variables.

**Tools registered**:

| Tool | Description |
|------|-------------|
| `delegate_metadata` | Streams an active-source schema question to MetadataAgent |
| `delegate_data_analysis` | Runs progressive Skill routing first in enabled mode; governed analytics can skip Metadata, while non-Skill requests use property-first Ontology → physical Metadata verification → DataInsight |

**Input routing logic**: MasterAgent uses `MASTER_AGENT_PROMPT` to decide which tool to call. Questions requiring data analytics or schema discovery are delegated. Data Skills are advertised only inside their assigned sub-agents.

**Agentic loop ownership**: A user turn invokes `MasterAgent` exactly once. Its MAF `OpenAIChatCompletionClient` owns the bounded function-invocation loop: it streams a model response, executes requested Agent/tools, appends each function result as an observation, and calls the model again. The loop exits when the model emits no further function call, reaches the configured model-roundtrip/function-call limit, or reaches the consecutive-error limit. There is no second answer judge, hidden feedback turn, or fixed 180-second MasterAgent timeout.

**Validated analytical completion**: DataInsight keeps a request-local execution ledger and returns an `AnalysisCompletion` through an internal result sink. `complete_analysis(status, answer, evidence, gaps)` checks current-request successful SQL references, analysis-purpose evidence, pending diagnostics, and explicit coverage limits. Only an accepted `completed` result marks the analytical pipeline successful. Partial evidence and insufficiency are delivered with visible limitations; missing completion is a failure. Master receives status rather than inferring success from text or `[STREAMED]`. SSE delivers only the validated analytical answer, ignoring subsequent Master paraphrases. Protocol-looking output without a valid completion is rejected before delivery and cache insertion.

**Bounded exploration**: MAF chat/function middleware counts native Skills and SQL/recovery/completion calls against the existing limits. It reserves the final call for `complete_analysis` and never re-enables tools disabled by MAF. Required diagnostics stay in the same loop. Successful SQL output can be reused only in the same request using a dialect-aware AST fingerprint plus source, row cap and purpose; failed/blocked SQL is not reusable. Logs carry request/thread/run correlation and execution stages, without storing additional result rows. Completion checks are factual guards, not an additional semantic judge.

**Ontology evidence**: Shared validation rejects empty, failed and `no_match` business contexts. Recovery selects the highest-confidence successful tool evidence, breaking ties by recency, and retains other evidence and unresolved gaps. No usable context triggers metadata discovery and `ontology_applied=false`; partial valid semantics remain qualified. Full-schema recall indicates physical candidates, not proof of business mappings.

This matches the central Claude QueryEngine control path while retaining MAF's native function-call protocol. Request-local `ToolOutcome` records are observability data only; they do not trigger a second `agent.run()`.

### 4. OntologyAgent (`src/agents/ontology_agent.py`)

**Backend**: Owlready2 with a dedicated in-memory `World`. The service recursively loads `Ontology/**/*.owl` in read-only mode and builds normalized entity and graph indexes. HermiT startup reasoning is disabled by default; enable it with `ONTOLOGY_ENABLE_REASONER=true` and Java 11+.

**Runtime path**: `OntologyRouter` uses the primary model only to evaluate the progressively disclosed `analytics-spec` governed Skill. If no governed route matches, code calls `get_business_context` and `list_defined_classes` directly. A non-`ok` result or confidence below `ONTOLOGY_ESCALATION_MIN_CONFIDENCE` escalates to the full OntologyAgent tool loop. This keeps normal semantic lookup deterministic while preserving model-driven recovery for weak results.

**Tools**: `search_entities`, `describe_entity`, `expand_neighbors`, `find_paths`, `find_related_by_type`, `get_schema_mapping`, `get_join_paths`, `get_lineage`, `get_semantic_candidates`, `list_defined_classes`, and `get_business_context`.

`describe_entity` also returns explicit `disjoint_with` classes and non-label annotation values, and `list_defined_classes` enumerates equivalentClass-defined business concepts (for example a threshold-defined order class) together with their rendered definitions.

Entity resolution combines exact IRI/name, multilingual labels, normalized tokens, type constraints, and fuzzy candidates. Query tools return stable JSON envelopes with evidence, confidence, attempted strategies, warnings, and unresolved concepts. Physical joins remain empty unless an OWL annotation explicitly grounds them.

`OntologyService` is deliberately analysis-neutral. It returns role-neutral OWL properties with labels, comments, domains/ranges, class hierarchy and restrictions, plus ordered semantic relationships and paths. It does not select measures or dimensions and does not define baselines, decompositions, or SQL formulas. Business aliases belong in the OWL rather than Python lookup dictionaries.

HermiT may reject ontology datatypes outside its OWL 2 datatype map. A reasoner failure is exposed as capability status while asserted OWL facts remain queryable. If OntologyAgent still cannot produce usable context for a request, MasterAgent emits a visible fallback activity and continues with the ordinary metadata-driven workflow.

### 5. DataInsightAgent (`src/agents/data_insight_agent.py`)

**Backend**: selected once per process by `DATA_SOURCE_TYPE` (default `databricks`). `src/data_sources/` exposes a common `DataSource` contract. Databricks uses `databricks-sql-connector`; MySQL uses SQLAlchemy Core with the PyMySQL driver and a bounded `QueuePool`.

**Tools**:

| Tool | Description |
|------|-------------|
| `execute_sql` | Runs a read-only SQL query against the active data source; returns rows as JSON |
| `recover_metadata_context` | Exceptional, once-per-request MetadataAgent recovery when the schema handoff is missing or incomplete |
| `recover_ontology_context` | Exceptional, once-per-request OntologyAgent recovery when ontology was enabled but context is unexpectedly missing |
| Native Skills | `SkillsProvider` advertises governed templates plus `sql-planning` on demand |

**Configuration**: `DataSourceConfig` selects the backend. `DataSourcePolicyConfig` owns source-neutral row, timeout, and metadata-cache limits. `DatabricksConfig` contains warehouse and Unity Catalog settings; `MySQLConfig` contains connection, database allowlist, character set, and pool settings. The agent is instantiated at startup and reports a source-specific configuration error when the selected backend is incomplete.

Before execution, a dialect-aware `sqlglot` guard requires `catalog.schema.table` for Databricks or `database.table` for MySQL and rejects objects outside the configured allowlist. A keyword guard rejects data-definition and data-modification statements. MySQL connections additionally initialize as transaction read-only, and production deployments should use a database account granted only `SELECT`.

### 6. MetadataAgent (`src/agents/metadata_agent.py`)

**Backend**: a `MetadataProvider` selected with the active data source. Databricks reads Unity Catalog through `databricks-sdk` with SQL fallback. MySQL uses parameterized `information_schema` queries on the same SQLAlchemy engine and connection pool as query execution.

**Tools**:

| Tool | Description |
|------|-------------|
| `list_schemas` | Lists configured schemas/databases that exist in the active source |
| `list_tables` | Lists tables within a schema |
| `get_table_details` | Returns column names, types, and comments for a table |
| `search_tables` | Fuzzy-matches table names by keyword |
| Native Skill | `SkillsProvider` advertises and loads `metadata-mapping` on demand |

MetadataAgent remains necessary after adding OntologyAgent: ontology semantics identify business concepts and paths first, while active-source metadata is authoritative for executable table names, columns, keys, join cardinality, and availability. MasterAgent owns the canonical Ontology artifact and passes it directly to DataInsightAgent; MetadataAgent receives a bounded verification projection and cannot replace or discard the semantic context.

For analytics, code first lists cached table summaries (bounded by `METADATA_INDEX_MAX_TABLES`), scores them against the question and ontology terms, and batch-fetches up to `METADATA_CANDIDATE_MAX_TABLES` details with a bounded thread pool. Identifier-based join closure may add bridge tables. When recall finds nothing, schemas no larger than `METADATA_SNAPSHOT_MAX_TABLES` use a complete snapshot; larger schemas leave discovery to the MetadataAgent tools. The snapshot is a candidate subset, so the model can still call `search_tables` or `get_table_details` to close a named gap.

MetadataAgent then performs one discovery/verification turn: ontology-disabled discovery progressively loads `metadata-mapping`; ontology-enabled verification has no Skill provider. It returns selected tables, verified joins, Skill-only business mappings, rejected candidates, and unresolved terms without copying raw columns. DataInsightAgent receives that decision JSON plus every authoritative raw metadata payload. Process-local TTL caches are keyed by catalog, schema table list, and fully qualified table detail.

### 7. Skill System

**SkillsProvider factory** (`src/skills_provider.py`): Creates agent-scoped native MAF providers backed by `FileSkillsSource`. OntologyRouter receives governed template routing Skills; DataInsightAgent receives those governed Skills plus `sql-planning`; Metadata discovery receives `metadata-mapping`; Ontology recovery, Metadata verification, and MasterAgent receive no data Skills.

MAF applies progressive disclosure: advertise Skill metadata, load `SKILL.md` on demand, then optionally read resources or execute approval-gated scripts.

**Current skills**:

| Skill | Purpose |
|-------|---------|
| `analytics-spec` | Governed highest-spending-customer SQL template and matching contract |
| `sql-planning` | Dynamic planning and SQL engineering method for every non-governed query; uses OWL when available and verified active-source metadata in all modes |
| `metadata-mapping` | Unity Catalog metadata field mapping rules |

### 8. Business Semantic Layer (`src/business_layer.py`)

A workspace-level document that business users author in the UI (**Business Layer Doc** in the chat header) to record terminology, metric definitions, and reporting conventions that the OWL ontology does not define.

- **Storage**: a plain data file at `data/business_layer.md`, never imported or executed as code. `load_business_layer()` / `save_business_layer()` are the only entry points, so moving to object storage or a database changes just those two functions.
- **Delivery**: `chat_stream` reads the document on every request and threads it through `QueryEngineContext` to DataInsightAgent, which wraps it as `<business_layer_context>`. Because it is read per request rather than baked into a system prompt, edits take effect on the next question with no restart.
- **Precedence**: advisory only. It ranks below `<schema_context>`, cannot override verified Unity Catalog objects, and is treated as reference data — the prompt instructs the model to ignore any embedded instruction, and the SELECT-only plus catalog/schema guards in `execute_sql` remain the enforcing control.
- **Scope**: applies whether Ontology is enabled or disabled, and is empty (block omitted entirely) until someone saves content.

## 🔄 Data Flow

### Data Analytics Flow

```
User Question (analytics intent detected)
      ↓
MasterAgent → delegate_data_analysis()
      ↓
Ontology enabled for this session?
  ├── No  → MetadataAgent resolves question-relevant physical objects
      └── Yes → OntologyAgent resolves role-neutral properties, restrictions, semantic paths, and lineage
              ├── success → MetadataAgent verifies the ontology-derived physical candidates
              └── failure → emit visible fallback, then run ordinary MetadataAgent lookup
      └── deterministic recall batch-fetches candidates; Metadata tools may close named gaps
      ↓
DataInsightAgent receives canonical ontology evidence (when available) plus independent authoritative schema context and reconciles both before SQL
      ↓
The workspace business layer document, when non-empty, is attached as <business_layer_context> in both ontology modes
      ↓
DataInsightAgent.query_stream()
      ├── governed route → load template Skill + indexed SQL resource
      ├── ordinary ontology route → load_skill("sql-planning")
      │      └── primary model dynamically selects metric, grain, comparisons, and SQL
      ├── recover_* only if an expected context handoff is missing/incomplete
  ├── execute_sql(sql)              → active Databricks/MySQL backend
  └── Stream results back
      ↓
MasterAgent: relay thinking + text events to SSE queue
      ↓
Frontend: render tabular / prose summary
```

## 🎛️ Configuration Architecture

All configuration is centralized in `src/config/settings.py` and loaded from `.env`:

```
OpenAIConfig
  ├── BASE_URL, API_KEY
  └── MODEL, SMALL_MODEL

AzureAIFoundryConfig
  └── CONNECTION_STRING

DataSourceConfig
  └── TYPE = databricks | mysql

DataSourcePolicyConfig
  └── MAX_ROWS, QUERY_TIMEOUT, METADATA_CACHE_TTL_SECONDS

DatabricksConfig
  ├── HOST, TOKEN, HTTP_PATH
  ├── CATALOG, SCHEMAS (comma-separated list)
  ├── METADATA_INDEX_MAX_TABLES, METADATA_CANDIDATE_MAX_TABLES
  ├── METADATA_SNAPSHOT_MAX_TABLES
  └── is_configured() → bool

MySQLConfig
  ├── HOST, PORT, USER, PASSWORD, CHARSET
  ├── DATABASES (allowlist; first entry is default)
  ├── POOL_SIZE, POOL_MAX_OVERFLOW, POOL_RECYCLE_SECONDS
  └── is_configured() → bool

OntologyConfig
      ├── DIRECTORY, FILE_GLOB, ONLY_LOCAL
      ├── ENABLE_REASONER, REASONER
      └── query limits, FUZZY_THRESHOLD, ESCALATION_MIN_CONFIDENCE, AGENT_TIMEOUT_SECONDS

AppConfig
  ├── LOG_LEVEL
  ├── QUERY_ENGINE_* function-loop budgets
  ├── DEFAULT_ENABLE_ONTOLOGY
  └── LOG_DIR, TMP_DIR, DATA_DIR
```

`validate_config()` raises `ValueError` when the OpenAI-compatible base URL, API key, or model names are missing.

## 🔒 Security Architecture

### LLM Authentication

The configured OpenAI-compatible endpoint uses a bearer API key from `OPENAI_API_KEY`; `DEEPSEEK_API_KEY` is accepted as a DeepSeek-specific fallback alias. Keys remain in the ignored local `.env` or deployment secret store.

### Best Practices
- All secrets in `.env` only (`.gitignore`-d)
- No credentials in source code or logs
- Databricks PAT scoped by Unity Catalog RBAC
- MySQL account restricted to `SELECT` on allowlisted databases; application sessions are transaction read-only
- SQL parsing, single-statement enforcement, fully qualified names, and source-specific allowlists are enforced before execution

## 📊 Monitoring & Observability

### Logging
- File: `logs/application_YYYYMMDD.log`
- Content: agent decisions, tool calls, ontology lookups, SQL queries, errors, startup events
- Level controlled by `LOG_LEVEL` env var

### External evaluation
- `AZURE_AI_PROJECT_CONNECTION_STRING` is retained as a configuration placeholder; this repository does not currently register a Foundry or Application Insights exporter.
- Rotating logs can be exported by a deployment-specific pipeline for groundedness, relevance, and coherence evaluation.
- The session ontology switch supports A/B testing ontology-enriched versus metadata-only analysis.

## 🔄 Extension Points

### Adding a New Agent

1. Create `src/agents/my_agent.py` implementing `_create_tools()` and `_create_agent()`
2. Add to `src/agents/__init__.py`
3. Create a corresponding `delegate_my_agent` tool in `MasterAgent._create_tools()`
4. Instantiate and pass into `MasterAgent.__init__()` in `src/api/main.py`

### Adding a New Skill

1. Create `skills/my-skill/SKILL.md` with YAML frontmatter (`name`, `description`, `tags`)
2. Assign the directory name to the intended agent in `src/skills_provider.py`
3. MAF `SkillsProvider` advertises it on the next startup and loads it on demand

## 🎯 Design Principles

1. **Modularity**: Agents, tools, prompts, config, and skills are fully separated
2. **Streaming-first**: User-visible progress and answers flow through SSE; delegated workers use bounded waits and cooperative cancellation
3. **Configurable auth**: `AUTH_MODE` supports both API key and AAD without code changes
4. **Skill injection**: Domain expertise is externalized to `skills/` Markdown files
5. **Separate authority boundaries**: OWL semantics propose, verified active-source metadata decides
6. **Pluggable data layer**: Databricks remains the backward-compatible default; MySQL is selected entirely through configuration

---

**For implementation details, see source code in `src/`.**
