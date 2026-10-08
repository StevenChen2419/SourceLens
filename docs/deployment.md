# SourceLens manual deployment readiness

This runbook is not an automated provisioner. Commands in the Azure section **create/change resources and role assignments** only when you choose to execute them. No GitHub CD is implemented. Use PowerShell from the repository root. Stop on any nonzero native-command exit code; do not continue with missing IDs or a failed build.

SourceLens was formerly KnowledgeOps. Legacy `KNOWLEDGEOPS_*` settings and existing Search/index names are intentional compatibility identifiers. New image/hosting examples use `sourcelens`; if hosting already exists, use its existing names rather than creating replacement resources solely for branding. Rebuild images to use the new package name.

## Architecture and limits

- Static Web Apps **Free** hosts `frontend/dist`; the browser calls the backend directly over HTTPS.
- Container Apps **Consumption** runs one Python 3.12/Uvicorn worker at 0.5 vCPU, 1 GiB, target port 8000, min replicas 0, max replicas 1. Cold starts are expected.
- Backend uses a system-assigned managed identity with the existing Blob container, Search index and model deployments. No Azure service API keys, SAS, connection strings, developer CLI cache, or credentials go in the image.
- Ingress starts restricted to your public IPv4 `/32`. The frontend calls the backend directly, so restrictions apply to browser visitors. After verifying the public-demo policy, corpus boundary and usage limits on the hosted revision, you may deliberately enable anonymous ingress for that revision. Keep the restriction if any safeguard fails; CORS is not authentication.
- There is no database, volume, VNet, queue, registry in Azure, or new model/Search resource. The optional public GHCR image contains source code/dependencies only. Confirm that publishing the application image is acceptable before pushing it.
- Default Azure HTTPS domains avoid custom-domain/certificate work. Keep `allowInsecure: false`.

Container Apps HTTP ingress has a [240-second timeout](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview). Existing synchronous PDF extraction/embedding/indexing and model retries can exceed it. Test modest PDFs and deployed latency; a 10 MiB compressed-file limit does not bound PDF memory or processing time. A timeout does not prove that a write stopped. Inspect listing/state before retrying; follow the [lifecycle recovery runbook](lifecycle.md#interrupted-operation-recovery).

The catalog's infinite Blob lease survives a killed process. Drain uploads/deletions before revision changes; one replica and Single revision mode do not guarantee that old/new processes never overlap. Never break an active lease. Do not run old ingestion scripts against the normal index. Do not use the controlled evaluation index for the hosted app.

## Production settings

The image defaults to `KNOWLEDGEOPS_ENVIRONMENT=production` and `AZURE_TOKEN_CREDENTIALS=ManagedIdentityCredential`. Production startup additionally requires explicit `APP_MODE=public_demo` and the verified `DEMO_DOCUMENT_ID`. Development or unimplemented authenticated modes are rejected. It rejects missing storage/Search/model settings, non-managed-identity credential selection, HTTP or loopback CORS origins, wildcard origins, URL paths and credentials. This is syntactic validation; deployment names, RBAC, network access, quota and model availability still need explicit live verification.

`KNOWLEDGEOPS_CORS_ORIGINS` is a JSON array of exact origins, e.g. `["https://your-site.azurestaticapps.net"]` (no trailing slash). Set it explicitly in Azure. Development retains `http://localhost:5173` and `http://127.0.0.1:5173`. Only allowlisted GET/POST public operations can pass the API policy; document DELETE and its preflight are blocked. Cookies/credentials remain disabled. Configure CORS in FastAPI only, not also on the platform.

Copy endpoint/deployment values from your working configuration into the ignored deployment JSON described below. The template lists every required setting and preserves 1536 dimensions, batch size 16, top_k 5, context 8000, completion cap 4096 and upload limit 10 MiB for trusted development. Demo-specific limits override public question retrieval/output budgets without changing development/evaluation defaults. Use the current model endpoint URLs ending `/openai/v1/`, including a `services.ai.azure.com` hostname if that is what your deployment already uses. These values are configuration, not credentials.

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
$env:VITE_API_BASE_URL = 'https://sourcelens-api.example.com'
npm run build
Pop-Location
docker build --platform linux/amd64 -t sourcelens-backend:readiness backend
.\backend\.venv\Scripts\python.exe backend/scripts/verify_container.py sourcelens-backend:readiness
```

The verifier runs an ephemeral non-root container with 0.5 CPU/1 GiB, **no network**, no volumes and no host ports. It scans the filesystem for dotenv/PDF/evaluation artifacts and common credential files/private keys, loads `cl100k_base` and `o200k_base` from the baked cache, then starts Uvicorn with syntactically valid example endpoints and verifies `GET /health` returns `200 {"status":"ok"}`. It verifies the image's configured user and command, and removes its container on completion. It never calls Azure. Public CA certificate bundles are legitimate dependencies, not credentials. This filesystem scan plus the context allowlist is not a general-purpose secret scanner for arbitrary future source-code edits.

Runtime command (already the image CMD):

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-proxy-headers
```

`/health` performs no Azure calls. The Docker HEALTHCHECK is useful locally; Container Apps uses the explicit Startup/Readiness/Liveness probes in `containerapp.example.json`, not Docker's HEALTHCHECK. Startup allows about 150 seconds, readiness checks every 10 seconds and liveness every 30 seconds. Internal probes use HTTP to port 8000; public ingress uses HTTPS. A healthy process does not prove inference or storage access. Avoid external always-on pings that defeat scale-to-zero.

## Manual Azure deployment (not executed by this milestone)

First confirm your remaining credit, existing service regions/SKUs, network rules, and a supported Container Apps region. An identity role does not bypass service firewalls. Do not change existing service network rules or create networking infrastructure automatically. Budget alerts are notifications, not a spending cap. The hosted app shares `knowledgeops-chunks` and its catalog with local development: any successful write affects that shared collection.

### 1. Publish the tested image manually

Set `<owner>` and a unique tag. Use `docker login ghcr.io -u <owner>` interactively; enter a GitHub token with package-publishing permission at the password prompt, never in shell history or a file. This is a registry publishing credential, not an Azure application key. Use Docker's credential store and log out afterward.

```powershell
$imageTag = 'ghcr.io/<owner>/sourcelens-backend:readiness-v1'
docker login ghcr.io -u <owner>
docker tag sourcelens-backend:readiness $imageTag
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
$hostingGroup = 'sourcelens-hosting'
$region = '<supported-container-apps-region>'
$staticRegion = '<supported-static-web-apps-region>'
$environmentName = 'sourcelens-env'
$backendName = 'sourcelens-api'
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

Edit **only the ignored copy**. Replace its app name with `$backendName`, location with `$region`, environment ID with `$environmentId`, frontend hostname with `$frontendHost`, image with the published digest, public IP with your actual public IPv4 `/32`, the verified handbook UUID, and existing service URLs/deployment names. Keep `APP_MODE=public_demo`. Obtain your public IP from your router/ISP or a trusted IP-check service. Do not substitute `0.0.0.0/0`. JSON is also valid YAML for Azure CLI's `--yaml` parameter.

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
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role 'Search Index Data Reader' --scope "$searchServiceId/indexes/knowledgeops-chunks"
foreach ($modelResource in @($embeddingResourceId, $generationResourceId) | Select-Object -Unique) {
    az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role 'Cognitive Services OpenAI User' --scope $modelResource
}
$backendHost = az containerapp show --name $backendName --resource-group $hostingGroup --query properties.configuration.ingress.fqdn -o tsv
Invoke-RestMethod "https://$backendHost/health"
Invoke-RestMethod "https://$backendHost/api/config"
```

The demo identity needs no Blob role, Search write role, or Search Service Contributor: no hosted ingestion/catalog operations are allowed. The existing Blob URL remains configured for compatibility but is not accessed by public requests. [Search Index Data Reader can be scoped to one index](https://learn.microsoft.com/en-us/azure/search/search-security-rbac). Model resources still need Cognitive Services OpenAI User, consistent with the existing Entra integration. Keep broad ingestion/schema/storage permissions on the trusted local operator only. Check inherited assignments too. Role assignment changes require operator RBAC administration privileges; never grant them to the runtime identity.

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

Open `https://<frontend-hostname>` from your allowed IP. Verify the Public Demo label, synthetic handbook explanation, three suggested questions, grounded answers/page citations, abstention, friendly failures, and the absence of upload/list/delete controls. Also test blocked routes directly: hiding buttons is insufficient. Confirm unapproved IPs are initially blocked and CORS permits only the exact frontend origin. Follow the security checks below before opening ingress; preserve both evaluation baselines and do not run evaluation automatically.


## Public demo activation and verification

[Public access architecture](public-access.md) documents the full endpoint matrix, settings/defaults, safeguards and future authenticated migration. No authentication or multi-user ownership exists yet. Do not expose development mode publicly.

### 1. Verify the approved document locally

Keep your trusted backend on loopback with `APP_MODE=development` and normal `AZURE_SEARCH_INDEX_NAME=knowledgeops-chunks`. Do not point it at `knowledgeops-eval-chunks`. The clean demo handbook was previously verified through the real application; [recorded responses](demo-results.json) are separate from the preserved evaluation runs.

```powershell
$documents = Invoke-RestMethod http://127.0.0.1:8000/api/documents
$handbooks = @($documents.documents | Where-Object { $_.filename -eq 'employee-handbook.pdf' -and $_.state -eq 'indexed' })
if ($handbooks.Count -ne 1 -or $handbooks[0].page_count -ne 6 -or $handbooks[0].chunk_count -ne 6) {
    throw 'Verify exactly one approved six-page/six-chunk demo handbook before proceeding'
}
$demoDocumentId = $handbooks[0].document_id
```

If missing, inspect the synthetic PDF and upload it through the existing local UI/pipeline as a deliberate operator action, then repeat the listing check. Do not upload another copy if one already exists, rename historical evaluation PDFs, or delete any evaluation documents/indexes. No dedicated index is created by this runbook. Other normal-index documents need not be deleted: the approved UUID filter isolates evidence. Do not allow another operator to modify the selected document during hosting.

### 2. Test the mode on a separate local port

In a separate PowerShell terminal, from `backend/`:

```powershell
$env:APP_MODE = 'public_demo'
$env:DEMO_DOCUMENT_ID = '<UUID verified above>'
$env:KNOWLEDGEOPS_ENVIRONMENT = 'development' # local az login, not hosted configuration
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1 --no-proxy-headers
```

This local command makes no mutations; answer checks use real Azure resources. Point Vite's local `VITE_API_BASE_URL` at port 8001 and restart Vite to inspect the demo UI. The original development terminal remains the full app on port 8000. Do not confuse local credential settings with production, which requires managed identity.

### 3. Backend security checks

Use a local or deployed HTTPS base URL. After the first five admitted answer requests from a peer, wait at least 60 seconds before expecting another successful answer.

```powershell
$api = 'http://127.0.0.1:8001' # later https://<backend-hostname>
Invoke-RestMethod "$api/health"
Invoke-RestMethod "$api/api/config"
# Each of these must return 403, not redirect or execute a handler.
curl.exe -sS -o NUL -w '%{http_code}' -X POST "$api/api/documents"
curl.exe -sS -o NUL -w '%{http_code}' "$api/api/documents"
curl.exe -sS -o NUL -w '%{http_code}' -X DELETE "$api/api/documents/$demoDocumentId"
curl.exe -sS -o NUL -w '%{http_code}' -X POST "$api/api/retrieval"
curl.exe -sS -o NUL -w '%{http_code}' "$api/openapi.json"
curl.exe -sS -o NUL -w '%{http_code}' "$api/docs"
curl.exe -sS -o NUL -w '%{http_code}' "$api/unknown-admin-route"

$questions = @(
  'How many vacation days do full-time employees receive per calendar year?',
  'How many days per week may employees work remotely, and whose approval is required?',
  'What is the capital of Japan?'
)
foreach ($question in $questions) {
  Invoke-RestMethod -Method Post "$api/api/answers" -ContentType 'application/json' -Body (@{question=$question} | ConvertTo-Json) | ConvertTo-Json -Depth 6
}
# Must reject overrides (422) before any Azure call.
$override = @{question='policy'; top_k=20; document_id='other'; index_name='knowledgeops-eval-chunks'} | ConvertTo-Json
curl.exe -sS -i -X POST "$api/api/answers" -H 'Content-Type: application/json' --data-raw $override
# Invalid questions also consume admission budget. Repeated {} requests must become 429.
1..6 | ForEach-Object {
  curl.exe -sS -o NUL -w '%{http_code}' -X POST "$api/api/answers" -H 'Content-Type: application/json' --data-raw '{}'
}
```

Check the first two real answers against the PDF: vacation 15 paid days/year on page 1; remote work up to three days/week with manager approval on page 2. Japan must abstain without citations. Public citations contain filename/page only. Do not publish successful hosted claims until these checks actually pass. Offline concurrency/body/timeout tests supplement these checks without consuming Azure quota. Runtime corpus validation occurs on every answer; health is not evidence that the corpus is ready.

### 4. Hosted configuration and public ingress

Use `docs/containerapp.example.json`, which requires a verified UUID and the conservative defaults listed in `public-access.md`. Keep production environment, `APP_MODE=public_demo`, managed identity, exact frontend HTTPS CORS, 0.5 vCPU/1 GiB, min 0/max 1 replicas and one worker. Keep the IP restriction during initial verification. Deploy the rebuilt frontend with its exact backend HTTPS origin; it obtains capabilities from that backend.

After the hosted checks pass, enabling public ingress is a separate deliberate Azure change. In the Container App's Networking/Ingress settings, keep external HTTPS ingress, target 8000 and insecure traffic disabled; remove the initial owner-only IP rule only for the verified public-demo revision. Verify again from another network/private browser session and directly against blocked API paths. Keep one active revision without traffic splitting. If any route, corpus filter, quota or CORS safeguard cannot be verified, keep the restriction or disable ingress and do not advertise public access.

Uvicorn runs `--no-proxy-headers`. Do not configure trust-all forwarded headers. Until trusted proxy semantics are tested, Container Apps peers may collapse visitors into one shared five-per-minute bucket. This can make the demo busy earlier, which is safer than letting clients forge their rate identity. Global minute/day limits and concurrency still apply. Do not add paid routing/limiting services to solve this without approval.

### 5. Disable and Free Trial considerations

Do not upgrade the subscription to pay-as-you-go for this demo. Check trial eligibility, remaining credits, resource pricing and subscription status before deployment. AI requests consume billable usage/credits while the subscription is active; budget alerts notify you but do not stop usage. A Free Trial spending limit/expiration can disable resources and make the application unavailable. See [Azure spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit) and [budget alerts](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending). The in-memory limits reset on restarts and do not guarantee a hard spending cap.

To take the demo down, disable external ingress in the Container App portal, stop/deactivate active backend revisions, and remove the public demo link from the portfolio. Retain the owner-only restriction if you need private verification. Scale-to-zero alone is not an off switch: incoming requests can restart the app. Frontend files may remain accessible but must show unavailability when the backend is disabled. Do not delete Blob/Search/model/evaluation resources as a shutdown action. Never advertise a permanently hosted demo unless its subscription and operation are maintained.

For future full public RAG, complete token validation, ownership, owner-filtered Search, authorized lifecycle operations and durable per-user quotas from [the migration plan](public-access.md#future-authenticated-public-rag-design-only) before adding another production mode. No login/database infrastructure is part of this implementation.

## Operations, cost and rollback

Initial logging uses platform metrics and on-demand streaming (`az containerapp logs show --name $backendName --resource-group $hostingGroup --follow`), without paid historical log ingestion. Do not enable SDK payload/debug logs. Log streaming is transient; add retained diagnostics deliberately later. Keep Azure budgets/alerts and inspect model consumption. Scale-to-zero can make new hosting compute inexpensive but does not stop existing Search, model provisioned capacity, storage or retention charges. See [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing).

For later updates, retain the previously verified image digest and frontend build. Drain document operations first, apply the new digest/config manually, verify health and a small smoke check, and revert the image digest if needed. A code rollback does not undo Blob/Search changes. Keep the IP restriction on rollback. If the backend crashes during a lifecycle write, use the [stale-lease recovery procedure](lifecycle.md#interrupted-operation-recovery) only after all writers stop.

Taking the demo offline does not authorize deleting data resources. Disable ingress/deactivate backend revisions when finished; do not use scheduled pings to keep replicas awake. If eventually deleting hosting resources, first verify the group contains only hosting. Never delete the existing Blob account, Search service, model resources, catalog or evaluation artifacts as hosting cleanup.

No live provisioning, role assignment, image publication or Azure smoke verification is performed by the offline readiness checks.
