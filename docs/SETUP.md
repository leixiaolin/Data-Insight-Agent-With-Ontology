# Quick Start Guide

## 📋 Prerequisites Checklist

Before starting, ensure you have:

- ✅ Python 3.10 or higher installed
- ✅ Node.js 18.18+ installed (for the React frontend)
- ✅ Java 11+ installed (for optional HermiT startup reasoning)
- ✅ Azure subscription with active resources
- ✅ Azure OpenAI service with a primary tool-capable GPT deployment and a small Metadata deployment
- ✅ (Optional) Azure Databricks workspace with Unity Catalog SQL Warehouse
- ✅ Network access to all Azure services

## 🎯 5-Minute Setup

### Step 1: Install Dependencies

```bash
# Installs Python venv + pip packages AND Node.js packages for the React frontend
./run.sh install
```

Or manually:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### Step 2: Configure Azure Services

1. **Copy environment template**:
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` with your credentials**:
   ```bash
   nano .env
   ```

3. **Required variables** (minimum set):

   | Variable | Description |
   |----------|-------------|
   | `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint URL |
   | `AZURE_OPENAI_AUTH_MODE` | `auto` \| `key` \| `aad` (default: `auto`) |
   | `AZURE_OPENAI_API_KEY` | API key — required for `AUTH_MODE=key`; optional in `auto`, which otherwise uses AAD |
   | `AZURE_OPENAI_GPT_DEPLOYMENT` | Primary deployment for Master, Ontology routing/recovery, and DataInsight |
   | `AZURE_OPENAI_GPT_SMALL_DEPLOYMENT` | Smaller tool-capable deployment for Metadata discovery/verification |

4. **Auth mode selection**:

   - **API Key** (default if key is set):
     ```
     AZURE_OPENAI_AUTH_MODE=key
     AZURE_OPENAI_API_KEY=<your-key>
     ```
   - **AAD / Entra ID** (when key-based auth is disabled on the resource):
     ```
     AZURE_OPENAI_AUTH_MODE=aad
     # Leave AZURE_OPENAI_API_KEY blank or remove it
     # Run 'az login' with an identity that has the
     # "Cognitive Services OpenAI User" role on the resource
     ```

### Step 3: Configure Ontology Runtime

Owlready2 recursively loads repository OWL files without modifying them. Defaults:

```env
DEFAULT_ENABLE_ONTOLOGY=true
ONTOLOGY_DIR=Ontology
ONTOLOGY_FILE_GLOB=**/*.owl
ONTOLOGY_ONLY_LOCAL=true
ONTOLOGY_ENABLE_REASONER=false
ONTOLOGY_REASONER=hermit
ONTOLOGY_MAX_RESULTS=25
ONTOLOGY_MAX_DEPTH=5
ONTOLOGY_MAX_PATHS=10
ONTOLOGY_MAX_NODES=250
ONTOLOGY_FUZZY_THRESHOLD=0.62
ONTOLOGY_AGENT_TIMEOUT_SECONDS=90
ONTOLOGY_ESCALATION_MIN_CONFIDENCE=0.5
```

`DEFAULT_ENABLE_ONTOLOGY` initializes each new React session independently. The active session's switch is sent with each request and does not affect other sessions.

HermiT startup reasoning is disabled by default; enable it only with `ONTOLOGY_ENABLE_REASONER=true` and Java 11+. Explicit classes, properties, restrictions, inverse relations, and multi-hop graph queries remain available without startup reasoning. When Ontology is enabled, OntologyRouter first progressively matches governed Skills. A confirmed governed match skips OWL and Metadata; otherwise code executes the question-driven composite OWL lookup and defined-class lookup directly. Weak results escalate to the full OntologyAgent tool loop. A skill-free Metadata verifier then checks recalled UC candidates before DataInsightAgent loads `sql-planning`. When Ontology is disabled or fails, Metadata discovery progressively loads `metadata-mapping`.

The normal analytics handoff is linear and does not return to the Master LLM between sub-agents. If DataInsightAgent detects an unexpectedly missing or incomplete handoff, its own MAF loop may recover Metadata or enabled Ontology context once before continuing. Disabled Ontology and known upstream Ontology failures are never retried.

### Step 4: (Optional) Configure Databricks

For the DataInsightAgent and MetadataAgent:
```
DATABRICKS_HOST=https://adb-XXXX.XX.azuredatabricks.net/
DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<warehouse-id>
DATABRICKS_CATALOG=<your-unity-catalog-name>
DATABRICKS_SCHEMAS=silver  # Add comma-separated schemas only when they actually exist
DATABRICKS_METADATA_CACHE_TTL_SECONDS=900
METADATA_INDEX_MAX_TABLES=500
METADATA_CANDIDATE_MAX_TABLES=12
METADATA_SNAPSHOT_MAX_TABLES=40
```

When the three connection values are absent, `DatabricksConfig.is_configured()` returns `False`; agents still initialize, but UC and SQL tools report configuration errors when invoked.

Metadata recall lists table summaries up to `METADATA_INDEX_MAX_TABLES`, scores them against question/ontology terms, and batch-fetches at most `METADATA_CANDIDATE_MAX_TABLES` candidate details before the model turn. If recall finds no candidate and the exposed schema has no more than `METADATA_SNAPSHOT_MAX_TABLES`, it falls back to the complete schema; larger schemas use MetadataAgent tools for discovery. Object caches are process-local and keyed by catalog/schema list or fully qualified table. Set the TTL to `0` for a process-lifetime object cache.

### Step 5: (Optional) Author the Business Layer Document

Business users can describe semantics the OWL ontology does not define — terminology, metric definitions, and reporting conventions. Open the React UI and click **Business Layer Doc** in the chat header, or call the API directly:

```bash
curl http://localhost:8000/business-layer
curl -X PUT http://localhost:8000/business-layer \
  -H 'Content-Type: application/json' \
  -d '{"content": "# Metric definitions\n- Revenue uses the order total, not the subtotal\n"}'
```

The text is stored at `data/business_layer.md` (git-ignored), shared by every session, and read on each request, so edits apply to the next question without restarting. It is advisory: verified Unity Catalog schema always wins, and the document is never treated as instructions. No configuration is required — when the file is absent the feature stays inert.

### Step 6: Run the Application

```bash
./run.sh             # Full stack: FastAPI (port 8000) + React (port 3000)
./run.sh backend     # FastAPI only
./run.sh frontend    # React dev server only
```

Open your browser to `http://localhost:3000`.

## 🔍 Verify Installation

### Test configuration

```bash
source venv/bin/activate
python -c "from src.config import validate_config; validate_config(); print('Config OK')"
```

### Test ontology loading

```bash
source venv/bin/activate
python -c "from src.ontology import OntologyService; s=OntologyService(enable_reasoner=False).load(); print(s.health())"
curl http://localhost:8000/config
```

### Test agent initialization

```python
from src.agents import MasterAgent, MetadataAgent

master = MasterAgent(metadata_agent=MetadataAgent())
print("All agents initialized")
```

### Test backend health endpoint

```bash
# With FastAPI running:
curl http://localhost:8000/health
curl http://localhost:8000/skills
```

## 🐛 Troubleshooting

### "Missing required configuration values"

Check `.env` has all required variables:
```bash
grep -v '^#' .env | grep '=' | head -20
```

### "Error code: 403 - AuthenticationTypeDisabled"

Key-based auth is disabled on your Azure OpenAI resource. Set:
```
AZURE_OPENAI_AUTH_MODE=aad
```
Then run `az login` and ensure your identity has the *Cognitive Services OpenAI User* role.

### DataInsight/Metadata tools report configuration errors

Set the three required Databricks variables:
```
DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_HTTP_PATH
```

### React frontend cannot connect to backend

1. Confirm FastAPI is running: `curl http://localhost:8000/health`
2. Check CORS origins in `src/api/main.py` include `http://localhost:3000`
3. For a separately hosted backend, set `VITE_API_BASE_URL=http://localhost:8000`; local Vite development normally uses the `/api` proxy

## 📚 Next Steps

1. Test example questions via the React UI
2. Review logs in `logs/` for debugging
3. Customize agent prompts in the per-agent modules under `src/prompts/` (`master.py`, `ontology.py`, `data_insight.py`, `metadata.py`)
4. Add domain skills to `skills/` directory

## 🔐 Security Notes

- Never commit `.env` to version control (it is in `.gitignore`)
- Use `AZURE_OPENAI_AUTH_MODE=aad` with Managed Identity for production
- Scope Databricks PAT tokens to minimum required permissions

## 📊 Monitoring

```bash
tail -f logs/application_$(date +%Y%m%d).log
```

## 🎓 Learning Resources

- [Microsoft Agent Framework Documentation](https://learn.microsoft.com/en-us/agent-framework/)
- [Azure OpenAI Service Documentation](https://learn.microsoft.com/en-us/azure/ai-services/openai/)
- [Azure Databricks Unity Catalog](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/)

---

**Need Help?** Check the main `README.md`, `ARCHITECTURE.md`, or the application logs for detailed error messages.
