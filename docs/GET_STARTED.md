# Ontology Data Agent — Getting Started

## ✨ Project Overview

**Project Name**: Ontology Data Agent  
**Location**: repository root
**Status**: active application; validate your target environment before production deployment

---

## 🎯 What You Have

### 🤖 Multi-Agent System (MasterAgent + 3 Specialized Agents)

| Agent | Purpose | Key Tools |
|-------|---------|-----------|
| **MasterAgent** | Orchestration and routing | `delegate_metadata`, `delegate_data_analysis` |
| **OntologyAgent** | Governed Skill routing, deterministic OWL business context, and model-driven weak-result recovery | `get_business_context`, `list_defined_classes`, and related Owlready2 recovery tools |
| **DataInsightAgent** | Databricks Unity Catalog SQL analytics | `execute_sql`; bounded context recovery; governed template Skills plus native `sql-planning` |
| **MetadataAgent** | Unity Catalog candidate recall and physical verification | deterministic table-summary recall and batch detail fetch; UC tools for gap recovery; native `metadata-mapping` Skill in discovery mode |

Agents use Azure OpenAI through Microsoft Agent Framework and `OpenAIChatCompletionClient`: Master, Ontology routing/recovery, and DataInsight use the primary deployment; Metadata discovery/verification uses the configured small deployment.

### 🔌 Skill System

Plugin-based skills in `skills/`:
- **`analytics-spec`** — governed highest-spending-customer matching contract and SQL resource
- **`sql-planning`** — dynamic OWL + verified UC query-planning method
- **`metadata-mapping`** — Unity Catalog metadata field mapping

MAF `SkillsProvider` discovers `SKILL.md` files, advertises only the Skills assigned to each agent, and registers native load/resource/script tools. Read-only loading is trusted; script execution remains approval-gated.

### 🖥️ Run Modes

| Mode | Command | Port | Notes |
|------|---------|------|-------|
| Full Stack (React) | `./run.sh` | 3000 (UI) + 8000 (API) | Streaming SSE, all 4 agents |
| Backend only | `./run.sh backend` | 8000 | FastAPI |
| Frontend only | `./run.sh frontend` | 3000 | React dev server |

### 🗂️ Key Files

```
src/
├── agents/
│   ├── master_agent.py       # Orchestration (2 delegation tools)
│   ├── data_insight_agent.py # Databricks SQL + governed/dynamic Skill provider
│   ├── metadata_agent.py     # Unity Catalog + metadata-mapping provider
│   ├── ontology_agent.py     # Owlready2 business context tools
│   └── maf_runtime.py        # MAF client/session/stream adapter
├── ontology/service.py       # Recursive read-only OWL loading and graph queries
├── api/main.py               # FastAPI backend + SSE streaming
├── config/settings.py        # All config classes (OpenAI, Databricks, Ontology, App)
├── skills_provider.py        # Native MAF SkillsProvider factory/API adapter
├── business_layer.py         # Workspace business semantic document store (data/business_layer.md)
└── prompts/                  # Per-agent system prompts (master, ontology, data_insight, metadata)
skills/
├── analytics-spec/
│   ├── SKILL.md
│   └── references/highest-spending-customer.sql
├── sql-planning/SKILL.md
└── metadata-mapping/SKILL.md
frontend/src/
├── App.tsx                   # Chat UI + activity panel
├── services/api.ts           # SSE client
├── types.ts
└── types/activity.ts
Ontology/*.owl                # Read-only business ontologies
run.sh                        # Launcher script
```

### 🏗️ Enterprise Features

- ✅ **Streaming responses** — SSE with activity, text, reset, completion, stop, and error events
- ✅ **AUTH_MODE** — `auto | key | aad` controls API key vs. AAD/`DefaultAzureCredential` auth
- ✅ **Optional Databricks capability** — the application starts without Databricks, while UC/SQL tools report configuration errors if invoked
- ✅ **Ontology-guided SQL** — session-selectable role-neutral OWL properties, restrictions, lineage, and semantic paths before UC verification and model-driven planning
- ✅ **Visible fallback** — Ontology failures are shown in the thinking panel before standard metadata-driven analysis continues
- ✅ **User-authored business layer** — business users edit a workspace semantic document in the UI; it is read per request, so edits apply without restarting the agent
- ✅ **Comprehensive logging** — `logs/application_YYYYMMDD.log`

---

## 🚀 Quick Start (3 Steps)

### Step 1: Install Dependencies
```bash
./run.sh install
```

### Step 2: Configure Environment
```bash
cp .env.example .env
nano .env   # Fill in your Azure credentials
```

Minimum required:
```
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_AUTH_MODE=aad          # or: key (if key-based auth is enabled)
AZURE_OPENAI_API_KEY=               # required only when AUTH_MODE=key
AZURE_OPENAI_GPT_DEPLOYMENT=<primary-deployment>
AZURE_OPENAI_GPT_SMALL_DEPLOYMENT=<small-tool-capable-deployment>
```

### Step 3: Run
```bash
./run.sh            # React UI at http://localhost:3000
```

---

## 🔑 Key Configuration Details

### Authentication (`AZURE_OPENAI_AUTH_MODE`)

| Value | Behaviour |
|-------|-----------|
| `key` | Uses `AZURE_OPENAI_API_KEY` (fails if key-based auth disabled on resource) |
| `aad` | Uses `DefaultAzureCredential` (requires `az login` + *Cognitive Services OpenAI User* role) |
| `auto` | Uses key if `AZURE_OPENAI_API_KEY` is set, otherwise AAD |

### Feature Flags

| Variable | Default | Effect |
|----------|---------|--------|
| `DEFAULT_ENABLE_ONTOLOGY` | `true` | Initial Ontology switch value for each new session |

The React Ontology switch belongs to the active session. Switching it does not affect other sessions or already-running requests.

### Databricks (Optional)

When these three are set, `DatabricksConfig.is_configured()` returns `True` and UC/SQL tools can execute:
```
DATABRICKS_HOST=https://adb-XXXX.XX.azuredatabricks.net/
DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXX
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<warehouse-id>
DATABRICKS_CATALOG=<catalog-name>
DATABRICKS_SCHEMAS=silver  # Add comma-separated schemas only when they actually exist
```

---

## 🎓 Understanding the System

### Agent Workflow (Data Analytics)

```
User Question
       ↓
MasterAgent (primary GPT deployment, main AgentSession + bounded agentic loop)
       ↓
  Data?    → delegate_data_analysis
                      Ontology on? → OntologyRouter progressively matches governed Skills
                             match → DataInsightAgent loads Skill + governed SQL resource
                             no match → deterministic question-driven OWL lookup
                                      → weak-result Ontology recovery when needed
                                      → deterministic UC candidate recall → Metadata verifier
                                      → DataInsightAgent loads sql-planning
                      Ontology off/fallback? → UC candidate recall → MetadataAgent loads metadata-mapping → DataInsightAgent
              → Databricks SQL
  Schema?  → delegate_metadata       → MetadataAgent   → Unity Catalog
       ↓
Stream answer tokens immediately; MAF exits when no further Agent/tool call is requested
       ↓
Stream: thinking → text chunks → thinking_done → done
       ↓
Frontend: render tabular / prose summary
```

---

## ✅ Test Checklist

```bash
# 1. Config validation
venv/bin/python -c "from src.config import validate_config; validate_config(); print('OK')"

# 2. Backend health
curl http://localhost:8000/health

# 3. Skills loaded
curl http://localhost:8000/skills

# 4. Ontology capability
curl http://localhost:8000/config
```

---

## 📊 Evaluation & Monitoring

- Logs: `logs/application_YYYYMMDD.log`
- A/B test ontology enrichment via the per-session Ontology switch or `DEFAULT_ENABLE_ONTOLOGY`
- Export logs through a deployment-specific pipeline for quality metrics; the repository does not currently register a Foundry exporter

---

## 🔐 Security Checklist

- ✅ API credentials stored in `.env` only (git-ignored)
- ✅ `AZURE_OPENAI_AUTH_MODE=aad` supported for keyless auth
- ✅ No hardcoded secrets in source code
- 🔲 **Production**: Use Managed Identity (`aad` mode) + Private Link
- 🔲 **Production**: Add user authentication layer in front of the React app

---

## 🆘 Common Issues & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `403 AuthenticationTypeDisabled` | Key auth disabled on AOAI resource | Set `AZURE_OPENAI_AUTH_MODE=aad`, run `az login` |
| DataInsight/Metadata tool configuration error | Databricks not configured | Set required `DATABRICKS_*` variables |
| Frontend can't reach backend | CORS or wrong URL | Check `src/api/main.py` CORS origins; frontend uses port 8000 |

---

## 📚 Documentation Index

| File | Content |
|------|---------|
| `README.md` | Feature overview, project structure, quick start |
| `docs/SETUP.md` | Detailed setup with all config options |
| `ARCHITECTURE.md` | Component deep dive, data flows, extension points |
| `docs/DEPLOYMENT.md` | Production deployment to App Service / Container Apps |
| `docs/GET_STARTED.md` | This file — project snapshot and quick reference |

---

**🎊 Your Ontology Data Agent is ready!**

```bash
./run.sh   # Start the full stack
```

React chat UI → `http://localhost:3000`  
