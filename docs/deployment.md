# Manual deployment readiness

This runbook is not an automated provisioner. Commands in the Azure section **create/change resources and role assignments** only when you choose to execute them. No GitHub CD is implemented. Use PowerShell from the repository root. Stop on any nonzero native-command exit code; do not continue with missing IDs or a failed build.

## Architecture and limits

- Static Web Apps **Free** hosts `frontend/dist`; the browser calls the backend directly over HTTPS.
- Container Apps **Consumption** runs one Python 3.12/Uvicorn worker at 0.5 vCPU, 1 GiB, target port 8000, min replicas 0, max replicas 1. Cold starts are expected.
- Backend uses a system-assigned managed identity with the existing Blob container, Search index and model deployments. No Azure service API keys, SAS, connection strings, developer CLI cache, or credentials go in the image.
- Public ingress is restricted to your public IPv4 `/32` from creation. The frontend does not proxy requests: allow the **browser user's IP**, not a frontend server IP. CORS is not authentication. Do not remove the IP restriction for an unrestricted public demo without a separate access-control/abuse-control design.
- There is no database, volume, VNet, queue, registry in Azure, or new model/Search resource. The optional public GHCR image contains source code/dependencies only. Confirm that publishing the application image is acceptable before pushing it.
- Default Azure HTTPS domains avoid custom-domain/certificate work. Keep `allowInsecure: false`.

Container Apps HTTP ingress has a [240-second timeout](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview). Existing synchronous PDF extraction/embedding/indexing and model retries can exceed it. Test modest PDFs and deployed latency; a 10 MiB compressed-file limit does not bound PDF memory or processing time. A timeout does not prove that a write stopped. Inspect listing/state before retrying; follow the lifecycle recovery runbook.

The catalog's infinite Blob lease survives a killed process. Drain uploads/deletions before revision changes; one replica and Single revision mode do not guarantee that old/new processes never overlap. Never break an active lease. Do not run old ingestion scripts against the normal index. Do not use the controlled evaluation index for the hosted app.

## Production settings

The image defaults to `KNOWLEDGEOPS_ENVIRONMENT=production` and `AZURE_TOKEN_CREDENTIALS=ManagedIdentityCredential`. Production startup rejects missing storage/Search/model settings, non-managed-identity credential selection, HTTP or loopback CORS origins, wildcard origins, URL paths and credentials. This is syntactic validation; deployment names, RBAC, network access, quota and model availability still need explicit live verification.

`KNOWLEDGEOPS_CORS_ORIGINS` is a JSON array of exact origins, e.g. `["https://your-site.azurestaticapps.net"]` (no trailing slash). Set it explicitly in Azure. Development retains `http://localhost:5173` and `http://127.0.0.1:5173`. GET/POST/DELETE and Content-Type remain allowed; cookies/credentials remain disabled. Configure CORS in FastAPI only, not also on the platform.

Copy endpoint/deployment values from your working configuration into the ignored deployment JSON described below. The template lists every required setting and preserves 1536 dimensions, batch size 16, top_k 5, context 8000, completion cap 4096 and upload limit 10 MiB. Use the current model endpoint URLs ending `/openai/v1/`, including a `services.ai.azure.com` hostname if that is what your deployment already uses. These values are configuration, not credentials.

Do not copy `.env` into the image or pass your development `.env` to production (it may select development mode). `.env.example` remains placeholders. The Docker context is an allowlist of Python application sources, packaging metadata and locks. The runtime contains an installed wheel, not the repository. No host directories or credentials need mounting. Azure CLI is not installed in the image.

The frontend requires `VITE_API_BASE_URL=https://<backend-hostname>` **at build time**. Builds reject absent/blank, malformed, HTTP, localhost, credential-bearing and path-containing values. Vite variables are public. A changed API origin requires a new frontend build. Local `npm run dev` continues to work with the existing localhost URL.

## Reproducible packaging and local checks

`pyproject.toml` remains the dependency source of truth and setuptools builds the application wheel. `requirements.lock` pins the production Linux dependencies and their package hashes; `build-requirements.lock` pins build tools. Initial runtime versions match the existing tested environment. Docker installs locks with `--require-hashes`, builds without isolated dependency resolution, installs the application without dependencies and runs `pip check`. The Python base image is digest-pinned; update it deliberately for security fixes and rerun checks.

To regenerate the runtime lock without intentionally upgrading existing packages (uv 0.11.9 was used):

```powershell
uv pip compile backend/pyproject.toml --python backend/.venv/Scripts/python.exe --python-platform x86_64-unknown-linux-gnu --constraint backend/requirements.lock --generate-hashes --no-header --no-annotate --output-file backend/requirements.lock
uv pip compile backend/build-requirements.in --python backend/.venv/Scripts/python.exe --python-platform x86_64-unknown-linux-gnu --generate-hashes --no-header --output-file backend/build-requirements.lock
```

For an intentional runtime dependency upgrade, omit the constraint, review the diff and run all tests. Do not hand-edit hashes. The production lock excludes test tools; normal development still uses the `dev` extra. Builds download public dependencies and tokenizer data, but no document contents.

```powershell
$env:TIKTOKEN_CACHE_DIR = Join-Path (Get-Location) '.uv-cache/tiktoken'
Push-Location backend
.\.venv\Scripts\python.exe -m pytest
Pop-Location
Push-Location frontend
npm ci
npm test
npx --no-install tsc --noEmit
$env:VITE_API_BASE_URL = 'https://knowledgeops-api.example.com'
npm run build
Pop-Location
docker build --platform linux/amd64 -t knowledgeops-backend:readiness backend
.\backend\.venv\Scripts\python.exe backend/scripts/verify_container.py knowledgeops-backend:readiness
```

The verifier runs an ephemeral non-root container with 0.5 CPU/1 GiB, **no network**, no volumes and no host ports. It scans the filesystem for dotenv/PDF/evaluation artifacts and common credential files/private keys, loads `cl100k_base` and `o200k_base` from the baked cache, then starts Uvicorn with syntactically valid example endpoints and verifies `GET /health` returns `200 {"status":"ok"}`. It verifies the image's configured user and command, and removes its container on completion. It never calls Azure. Public CA certificate bundles are legitimate dependencies, not credentials. This filesystem scan plus the context allowlist is not a general-purpose secret scanner for arbitrary future source-code edits.

Runtime command (already the image CMD):

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

`/health` performs no Azure calls. The Docker HEALTHCHECK is useful locally; Container Apps uses the explicit Startup/Readiness/Liveness probes in `containerapp.example.json`, not Docker's HEALTHCHECK. Startup allows about 150 seconds, readiness checks every 10 seconds and liveness every 30 seconds. Internal probes use HTTP to port 8000; public ingress uses HTTPS. A healthy process does not prove inference or storage access. Avoid external always-on pings that defeat scale-to-zero.

## Manual Azure deployment — not executed by this milestone

First confirm your remaining credit, existing service regions/SKUs, network rules, and a supported Container Apps region. An identity role does not bypass service firewalls. Do not change existing service network rules or create networking infrastructure automatically. Budget alerts are notifications, not a spending cap. The hosted app shares `knowledgeops-chunks` and its catalog with local development: any successful write affects that shared collection.

### 1. Publish the tested image manually

Set `<owner>` and a unique tag. Use `docker login ghcr.io -u <owner>` interactively; enter a GitHub token with package-publishing permission at the password prompt, never in shell history or a file. This is a registry publishing credential, not an Azure application key. Use Docker's credential store and log out afterward.

```powershell
$imageTag = 'ghcr.io/<owner>/knowledgeops-backend:readiness-v1'
docker login ghcr.io -u <owner>
docker tag knowledgeops-backend:readiness $imageTag
docker push $imageTag
docker logout ghcr.io
docker image inspect $imageTag --format '{{json .RepoDigests}}'
```

In GitHub package settings, make this application image **public**. Copy its `ghcr.io/...@sha256:...` digest into the template, not a mutable tag. Verify `docker pull <digest-reference>` works after logout. No GitHub Actions or CD is involved.

### 2. Create hosting-only resources

These are **live mutating commands**, for you to run after the local checks. Replace placeholders first and stop on any failure. Choose fresh hosting names; never put shared data resources in this group for convenience.

```powershell
az login
az account set --subscription '<subscription-id>'
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.Web --wait
$hostingGroup = 'knowledgeops-hosting'
$region = '<supported-container-apps-region>'
$staticRegion = '<supported-static-web-apps-region>'
$environmentName = 'knowledgeops-env'
$backendName = 'knowledgeops-api'
$frontendName = '<globally-unique-static-app-name>'
az group create --name $hostingGroup --location $region
az containerapp env create --name $environmentName --resource-group $hostingGroup --location $region --enable-workload-profiles --logs-destination none
az staticwebapp create --name $frontendName --resource-group $hostingGroup --location $staticRegion --sku Free
$environmentId = az containerapp env show --name $environmentName --resource-group $hostingGroup --query id -o tsv
$frontendHost = az staticwebapp show --name $frontendName --resource-group $hostingGroup --query defaultHostname -o tsv
```

The environment's default Consumption profile is used; do not add a Dedicated profile. Create a Cost Management budget for the hosting group and review costs on existing shared services separately.

### 3. Fill and apply the Container App configuration

```powershell
New-Item -ItemType Directory -Force .uv-cache/deployment | Out-Null
Copy-Item docs/containerapp.example.json .uv-cache/deployment/containerapp.json
```

Edit **only the ignored copy**. Replace its app name with `$backendName`, location with `$region`, environment ID with `$environmentId`, frontend hostname with `$frontendHost`, image with the published digest, public IP with your actual public IPv4 `/32`, and existing service URLs/deployment names. Obtain your public IP from your router/ISP or a trusted IP-check service. Do not substitute `0.0.0.0/0`. JSON is also valid YAML for Azure CLI's `--yaml` parameter.

Review the completed file; check that the only Search index named is `knowledgeops-chunks`. The template applies the IP restriction **in the same create request as external ingress**, avoiding an unrestricted exposure window.

```powershell
if (Select-String -Path .uv-cache/deployment/containerapp.json -Pattern '<[^>]+>' -Quiet) { throw 'Replace all deployment placeholders first' }
az containerapp create --name $backendName --resource-group $hostingGroup --yaml .uv-cache/deployment/containerapp.json
$principalId = az containerapp show --name $backendName --resource-group $hostingGroup --query identity.principalId -o tsv
```

At this point `/health` can pass, but API data/model operations need the following roles. RBAC propagation can take several minutes.

### 4. Assign the backend identity's existing-resource permissions

Copy exact resource IDs from each resource's Azure Portal Properties/JSON view. The container scope is the Storage account ID plus `/blobServices/default/containers/documents`. The model resource ID is the Cognitive Services account hosting the deployment, not a model name or project URL.

```powershell
$storageContainerId = '<storage-account-resource-id>/blobServices/default/containers/documents'
$searchServiceId = '<search-service-resource-id>'
$embeddingResourceId = '<embedding-host-resource-id>'
$generationResourceId = '<generation-host-resource-id>'
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Contributor' --scope $storageContainerId
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role 'Search Index Data Contributor' --scope "$searchServiceId/indexes/knowledgeops-chunks"
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role 'Search Service Contributor' --scope $searchServiceId
foreach ($modelResource in @($embeddingResourceId, $generationResourceId) | Select-Object -Unique) {
    az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role 'Cognitive Services OpenAI User' --scope $modelResource
}
$backendHost = az containerapp show --name $backendName --resource-group $hostingGroup --query properties.configuration.ingress.fqdn -o tsv
Invoke-RestMethod "https://$backendHost/health"
Invoke-RestMethod "https://$backendHost/api/documents"
```

Search Service Contributor is necessary because current ingestion validates/ensures the schema with SearchIndexClient. It is broader than document access; leave the integration unchanged for this milestone. Role assignments require operator RBAC administration privileges, not just Contributor. Do not give those privileges to the runtime identity. Listing may adopt legacy metadata if the configured catalog has never been initialized; use the already verified normal service/index/container combination.

### 5. Build and publish the frontend manually

```powershell
Push-Location frontend
$env:VITE_API_BASE_URL = "https://$backendHost"
npm ci
npm test
npm run build
Pop-Location
# Narrow Static Web Apps publishing token: never print or commit it.
$env:SWA_CLI_DEPLOYMENT_TOKEN = az staticwebapp secrets list --name $frontendName --resource-group $hostingGroup --query properties.apiKey -o tsv
try {
    npx --yes @azure/static-web-apps-cli deploy frontend/dist --env production
} finally {
    Remove-Item Env:SWA_CLI_DEPLOYMENT_TOKEN -ErrorAction SilentlyContinue
}
```

This token is only for publishing frontend files; it is not an Azure service inference/storage key, is not included in the Vite build, and is not used by the backend. The command downloads Microsoft's SWA CLI. No GitHub deployment workflow is created.

Open `https://<frontend-hostname>` from your allowed IP. Verify list, a grounded answer and an unsupported question; then deliberately use a disposable PDF for upload, duplicate detection, delete cancellation and deletion. These checks incur real usage and the last steps mutate data; do not test deletion against existing documents. Confirm requests from an unapproved IP are blocked and CORS permits only the exact frontend origin. Preserve both evaluation baselines and do not run evaluation automatically.

## Operations, cost and rollback

Initial logging uses platform metrics and on-demand streaming (`az containerapp logs show --name $backendName --resource-group $hostingGroup --follow`), without paid historical log ingestion. Do not enable SDK payload/debug logs. Log streaming is transient; add retained diagnostics deliberately later. Keep Azure budgets/alerts and inspect model consumption. Scale-to-zero can make new hosting compute inexpensive but does not stop existing Search, model provisioned capacity, storage or retention charges. See [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing).

For later updates, retain the previously verified image digest and frontend build. Drain document operations first, apply the new digest/config manually, verify health and a small smoke check, and revert the image digest if needed. A code rollback does not undo Blob/Search changes. Keep the IP restriction on rollback. If the backend crashes during a lifecycle write, use the README's stale-lease recovery procedure only after all writers stop.

Taking the demo offline does not authorize deleting data resources. Disable ingress/deactivate backend revisions when finished; do not use scheduled pings to keep replicas awake. If eventually deleting hosting resources, first verify the group contains only hosting. Never delete the existing Blob account, Search service, model resources, catalog or evaluation artifacts as hosting cleanup.

No live provisioning, role assignment, image publication or Azure smoke verification is performed by the offline readiness checks.
