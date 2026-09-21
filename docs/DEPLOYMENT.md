# Deployment Guide

## 🚀 Production Deployment Guide

This guide covers deploying the Ontology Data Agent to production. The system consists of two deployable components:

- **FastAPI backend** (`src/api/main.py`) — Python, serves SSE streaming and REST endpoints
- **React frontend** (`frontend/`) — TypeScript/Vite, communicates with the backend over HTTP

## 📋 Pre-Deployment Checklist

### External Resources
- [ ] OpenAI-compatible model API with primary and Metadata tool-capable models (DeepSeek by default)
- [ ] External log/evaluation destination if required (not wired by this repository)
- [ ] Azure Databricks workspace with Unity Catalog SQL Warehouse (optional, for DataInsight)
- [ ] Model API key stored in the deployment platform's secret store

### Application
- [ ] `.env` configured with all required production values
- [ ] Python 3.10+ and Node.js 18.18+ available on deployment target
- [ ] All Python and Node.js dependencies installable
- [ ] `logs/`, `tmp/`, `data/` directories writable
- [ ] `data/business_layer.md` persisted on durable storage if the business layer document must survive redeploys (it is git-ignored and node-local)
- [ ] Network connectivity to all Azure services verified
- [ ] Security review of credential rotation policy

## 🌐 Deployment Options

### Option 1: Azure App Service (Recommended for backend)

Deploy the FastAPI backend to Azure App Service and serve the React build as static files or via a CDN.

#### 1a. Build the React frontend

```bash
cd frontend
npm install
npm run build
# Build output: frontend/dist/
```

#### 1b. Create and configure App Service

```bash
az webapp create \
  --resource-group <your-rg> \
  --plan <your-plan> \
  --name <your-app-name> \
  --runtime "PYTHON:3.10"

az webapp config set \
  --resource-group <your-rg> \
  --name <your-app-name> \
  --startup-file "uvicorn src.api.main:app --host 0.0.0.0 --port 8000"
```

#### 1c. Set environment variables

```bash
az webapp config appsettings set \
  --resource-group <your-rg> \
  --name <your-app-name> \
  --settings \
    OPENAI_BASE_URL="https://api.deepseek.com" \
    OPENAI_API_KEY="<secret>" \
    OPENAI_MODEL="deepseek-v4-pro" \
    OPENAI_SMALL_MODEL="deepseek-v4-flash" \
    DATABRICKS_HOST="<value>" \
    DATABRICKS_TOKEN="<value>" \
    DATABRICKS_HTTP_PATH="<value>" \
    DATABRICKS_CATALOG="<value>" \
    DATABRICKS_SCHEMAS="<comma-separated-schemas>"
```

#### 1d. Deploy code

```bash
az webapp up \
  --resource-group <your-rg> \
  --name <your-app-name> \
  --runtime "PYTHON:3.10"
```

#### 1e. Frontend static hosting

Serve `frontend/dist/` from Azure Static Web Apps or a CDN. Set the backend URL in the frontend environment:

```bash
# frontend/.env.production
VITE_API_BASE_URL=https://<your-app-name>.azurewebsites.net
```

Rebuild: `npm run build`

### Option 2: Azure Container Apps

#### 2a. Create a Dockerfile

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Install build tools for any native packages
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy runtime assets. Supply secrets through the hosting platform, never the image.
COPY src/ ./src/
COPY skills/ ./skills/
COPY Ontology/ ./Ontology/
RUN mkdir -p data logs tmp

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### 2b. Build and push the backend image

```bash
az acr build \
  --registry <your-acr> \
  --image ontology-data-agent-backend:latest \
  --file Dockerfile .
```

#### 2c. Create a Dockerfile for the React frontend

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

```bash
az acr build \
  --registry <your-acr> \
  --image ontology-data-agent-frontend:latest \
  --build-arg VITE_API_BASE_URL=https://<backend-fqdn> \
  --file Dockerfile.frontend .
```

#### 2d. Deploy to Container Apps

```bash
# Backend
az containerapp create \
  --resource-group <your-rg> \
  --name ontology-data-agent-backend \
  --image <your-acr>.azurecr.io/ontology-data-agent-backend:latest \
  --target-port 8000 \
  --ingress external \
  --env-vars \
    OPENAI_BASE_URL="https://api.deepseek.com" \
    OPENAI_MODEL="deepseek-v4-pro" \
    OPENAI_SMALL_MODEL="deepseek-v4-flash"
  # Add the remaining non-secret settings and use Container Apps secrets/secretref
  # for API keys, SAS tokens, and Databricks credentials.

# Frontend
az containerapp create \
  --resource-group <your-rg> \
  --name ontology-data-agent-frontend \
  --image <your-acr>.azurecr.io/ontology-data-agent-frontend:latest \
  --target-port 80 \
  --ingress external
```

The static frontend calls the backend from the user's browser, so the backend must be externally
reachable in this example. To keep backend ingress internal, add a server-side reverse proxy in the
frontend container and keep `VITE_API_BASE_URL=/api` instead of compiling an internal FQDN into the
browser bundle.

## 🔐 Security Hardening

### Model API Key Security

Store `OPENAI_API_KEY` as an App Service or Container Apps secret, expose it only to the backend, and rotate it regularly. Never compile it into the frontend image or commit it to `.env` templates.

### Databricks Token Security

Use short-lived tokens or OAuth M2M credentials instead of long-lived PATs:
```bash
# Rotate PAT before expiry; update the DATABRICKS_TOKEN app setting
az webapp config appsettings set \
  --resource-group <your-rg> \
  --name <your-app-name> \
  --settings DATABRICKS_TOKEN="<new-token>"
```

### Restrict Network Egress

- Allow outbound traffic only to the configured model endpoint and required data services
- Deploy the App Service / Container App inside a VNet when the data source requires private connectivity

## 📊 Monitoring Setup

### Application Insights / Foundry integration

This repository currently emits rotating local logs and SSE activity events; it does not register
an Application Insights or Azure AI Foundry exporter. Add deployment-level log collection or
instrument the application with Azure Monitor OpenTelemetry before claiming live telemetry.

### Azure Monitor Alerts

Configure alerts for:
- Backend HTTP error rate > 5%
- Response latency P95 > 10s
- Model API throttling (429 responses)
- Databricks query timeout rate

## 🔄 CI/CD Pipeline (GitHub Actions)

```yaml
name: Deploy to Azure

on:
  push:
    branches: [ main ]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.10'

    - name: Install Python dependencies
      run: pip install -r requirements.txt

    - name: Run Python tests
      run: ONTOLOGY_ENABLE_REASONER=false python -m pytest test_script -q -p no:cacheprovider

    - name: Set up Node.js
      uses: actions/setup-node@v4
      with:
        node-version: '20'

    - name: Build React frontend
      run: |
        cd frontend
        npm ci
        VITE_API_BASE_URL=${{ secrets.BACKEND_URL }} npm run build

    - name: Deploy backend to Azure Web App
      uses: azure/webapps-deploy@v3
      with:
        app-name: '<your-app-name>'
        publish-profile: ${{ secrets.AZURE_WEBAPP_PUBLISH_PROFILE }}
```

## 🧪 Testing in Production

### Backend health check

```bash
curl https://<your-backend>/health
# Expected: {"status": "ok", ...}

curl https://<your-backend>/skills
# Expected: list of registered skills
```

### Smoke test via SSE

```bash
curl -N -X POST https://<your-backend>/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "thread_id": "test-1", "enable_ontology": true}'
```

## 📈 Scaling Considerations

### Backend (FastAPI + Uvicorn)
- Scale out App Service plan horizontally (min 2 instances for HA)
- SSE connections are long-lived; tune App Service timeout settings
- Consider Azure Container Apps with auto-scaling on HTTP request length

### Conversation Thread Storage
- Current: in-memory dict (lost on restart/scale-out)
- For production with multiple replicas: migrate to Azure Cosmos DB or Azure Cache for Redis

### Databricks SQL Warehouse
- Use auto-stop enabled warehouse to avoid idle costs
- Scale warehouse up if SQL query latency is high

## 🔧 Configuration Management

### Environment-Specific Configs

Use App Service deployment slots or separate resources per environment:
- `dev` — development with a separate low-privilege API key
- `staging` — pre-production with a separate key and representative data
- `prod` — production with a secret-store-managed key and restricted network egress

## 📝 Post-Deployment Tasks

1. **Verify functionality**: Test each question type (data insight with ontology on/off, metadata)
2. **Check skill loading**: `GET /skills` returns `analytics-spec`, `sql-planning`, and `metadata-mapping`
3. **Monitor logs**: `az webapp log tail --resource-group <your-rg> --name <your-app-name>`
4. **Set up Azure Monitor alerts**
5. **Document internal service endpoints** for the team

## 🆘 Troubleshooting Production Issues

**Backend not starting** — Check startup command, verify all required env vars are set, review App Service logs.

**401/403 from the model API** — Verify `OPENAI_BASE_URL`, rotate `OPENAI_API_KEY`, and confirm the selected model is enabled for the account.

**DataInsight tools report configuration errors** — Verify `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and `DATABRICKS_HTTP_PATH`; then check that the SQL warehouse is running.

**Slow cold start** — Pre-warm the app using App Service "Always On" setting or health-check pings.

## 📞 Support

For production issues:
1. Check application logs: `logs/application_YYYYMMDD.log`
2. Review Azure Monitor or Application Insights only when your deployment has configured that integration
3. Contact Azure Support for service-level issues

---

**Always test in a staging environment before deploying to production!**
