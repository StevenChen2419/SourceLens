# Local development and configuration

[Project overview](../README.md) | [Development](development.md) | [Lifecycle](lifecycle.md) | [Evaluation](evaluation.md) | [RAG reference](rag.md) | [Deployment](deployment.md)

## Set up the backend

Install Python 3.12 with pip. Run the following from the repository root.

Windows PowerShell:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

These commands use the virtual environment directly; activation is not required. Runtime and test dependencies are declared in `backend/pyproject.toml`.

## Configuration

Pydantic settings load values from `backend/.env`; process environment variables take precedence. The health endpoint and unit tests do not need Azure configuration. Document ingestion requires Blob, embedding, and Search settings plus a working Azure identity.

| Variable | Default | Purpose |
| --- | --- | --- |
| `KNOWLEDGEOPS_APP_NAME` | `SourceLens` | Nonempty application title shown in API documentation |
| `AZURE_STORAGE_ACCOUNT_URL` | unset | Required for uploads: HTTPS Blob service URL without a container path, query string, or credentials |
| `AZURE_STORAGE_CONTAINER` | `documents` | Existing private container; the application does not create it |
| `MAX_UPLOAD_SIZE_MB` | `10` | Positive integer file size limit, measured as MiB (1,048,576 bytes) |

Copy `backend/.env.example` to `backend/.env` if you have not already done so, and set `AZURE_STORAGE_ACCOUNT_URL` to your account's Blob service endpoint. The example intentionally contains no real account URL. Keep `.env` local; it is ignored by Git. Never add keys, connection strings, SAS tokens, or other credentials. Restart the backend after changing configuration.

### Azure authentication for development

The storage account and private container must already exist. The identity used by the backend needs the **Storage Blob Data Contributor** role on the account or container. Anonymous access and account-key access are not required. The account's network rules must permit access from your development machine.

Install Azure CLI and sign in from a terminal under the same OS user that will run FastAPI:

```powershell
az login
az account show
```

Use the tenant/account that has the Blob data role. `DefaultAzureCredential` tries its credential chain, including `AzureCliCredential`, which can obtain a token through the existing CLI login. Earlier available credentials can take precedence. If needed, set `$env:AZURE_TOKEN_CREDENTIALS = "AzureCliCredential"` in the development terminal to restrict the chain to CLI authentication. This is an optional process setting, not a secret. Azure CLI must be on the backend process's PATH. See [Microsoft's credential-chain guidance](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/credential-chains) and [Blob upload permissions](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python).

## Run the backend

From `backend/`, on Windows:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

On macOS/Linux:

```bash
.venv/bin/python -m uvicorn app.main:app --reload
```

Open [the health endpoint](http://127.0.0.1:8000/health). Expected response:

```json
{"status": "ok"}
```

Interactive API documentation is available at [Swagger UI](http://127.0.0.1:8000/docs). The health endpoint reports that the application is responding; it does not check external services.

## Upload a PDF and verify storage

1. Configure `backend/.env`, run `az login`, and start FastAPI as above.
2. Open [Swagger UI](http://127.0.0.1:8000/docs), expand `POST /api/documents`, and click **Try it out**.
3. In the multipart `file` field, use the file chooser to select one small text-based `.pdf` file. Click **Execute**.
4. Expect `201 Created` with `document_id`, `filename`, `page_count`, and `chunk_count`. The page count includes empty pages; empty pages generate no chunks.
5. In Azure Portal, open your storage account, then **Storage browser / Blob containers / documents** (or **Data storage / Containers / documents**). Refresh and find `<document_id>.pdf` using the returned ID.
6. Check that its size matches the original file. Download it using your signed-in identity and open it to confirm the original PDF is intact. The container stays private; an anonymous browser URL is not a valid access test.

You can also verify the blob through Azure CLI, replacing the placeholders:

```powershell
az storage blob show --account-name <your-account-name> --container-name documents --name "<document_id>.pdf" --auth-mode login
```

The backend checks extension, content type (`application/pdf` or `application/octet-stream`), PDF signature, actual file size, and extractability. It then generates a UUID, extracts and chunks locally, and uploads the unchanged original bytes. Blob names use only the generated UUID. The original filename is preserved in the response and percent-encoded UTF-8 Blob metadata (`original_filename`); `filename_encoding` documents that encoding. Uploads use `overwrite=False`.

Chunks and their vectors are indexed in Azure AI Search after embedding generation and original-Blob storage. Re-uploading identical PDF bytes returns 409 without creating another document, Blob, embedding, or Search chunk. Different bytes under the same filename are accepted. See the [lifecycle consistency limitations](lifecycle.md#states-concurrency-and-failure-behavior) before retrying a failed ingestion.

| Status | Meaning |
| --- | --- |
| `201` | PDF processed, original bytes stored, and every chunk write acknowledged by Search |
| `413` | File exceeds the configured limit |
| `415` | Non-PDF extension/type/signature, empty file, or unsuitable filename |
| `422` | Missing/multiple files, malformed PDF, encrypted PDF, or no extractable text |
| `503` | Blob, embedding, or Search configuration/API/schema/indexing failure; partial data may remain |

Error responses intentionally omit SDK internals. For a `503`, check service settings, CLI login, the required data roles, and network access. Run the [explicit ensure-index command](rag.md#schema-and-lifecycle) to diagnose schema incompatibility. If Blob storage succeeded and indexing then failed, the error detail includes the generated `document_id` for inspection. The API does not report ingestion success on embedding or indexing failure.

The size check bounds the bytes read into the processing service; multipart parsing can already have spooled the request to temporary disk before the route runs. This is a local development endpoint, not an ingress-wide request-size control. PDF expansion and extraction costs are also not bounded by compressed file size.

## Run tests

From `backend/`, on Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

On macOS/Linux:

```bash
.venv/bin/python -m pytest
```

Tests verify the health response, configuration, PDF extraction, chunk boundaries, metadata, stable identifiers, document uploads, and failure cases. Route tests replace the Blob integration; integration tests mock the Azure SDK and credential constructors. PDF fixtures are generated in memory using the development-only ReportLab dependency. No live Azure account or resources are required. A passing unit suite does not verify your live Azure permissions or connectivity; use the Swagger procedure above for that check.

## Run the complete local application

Complete [backend setup](#set-up-the-backend), [Azure authentication](#azure-authentication-for-development), and the [embedding, Search, and generation configuration](rag.md) first. Install Node.js 22.12+ (or 24 LTS) with npm. From the repository root, use two PowerShell terminals:

Terminal 1 (backend):

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Terminal 2 (frontend, first-time setup and startup):

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

On subsequent runs, only `npm run dev` is needed in the frontend terminal. Use `npm ci` for a clean install from the committed lockfile. On macOS/Linux, use `cp .env.example .env` and the backend Python path in [backend setup](#set-up-the-backend).

Open [SourceLens](http://127.0.0.1:5173). Select or drop one text-based PDF and click **Upload document**. The UI shows an indeterminate loading state during upload and processing; **Document indexed** appears only after the backend confirms success. Its page and chunk counts come from the backend. Then ask a question and inspect the answer's filename/page citations. These references identify pages in your original PDF; they are not public download links. Unsupported questions display the backend's insufficient-evidence response without sources.

Previously indexed documents remain available to questions. **Your documents** lists the backend's document catalog, refreshes after upload attempts, and provides confirmed deletion. The upload receipt and current answer are temporary browser state; deleting a document does not rewrite an answer already displayed. Upload failures can leave stored data behind; incomplete records remain visible for cleanup.

`frontend/.env` supports `VITE_API_BASE_URL=http://127.0.0.1:8000` (also the default). Restart Vite after changing it. All `VITE_` variables are public browser configuration: **never put credentials in them**. Azure authentication stays exclusively in the backend. Local `.env` files are ignored by Git.

FastAPI allows cross-origin GET, POST, and DELETE requests only from `http://127.0.0.1:5173` and `http://localhost:5173`, without cookies/credentials. Vite uses a fixed port and fails if 5173 is occupied rather than silently switching to an unapproved origin. This is a local development allowlist, not production deployment configuration. CORS is not authentication.

### Frontend checks

From `frontend/`:

```powershell
npm test
$env:VITE_API_BASE_URL = 'https://sourcelens-api.example.com' # Build-only placeholder; use real HTTPS origin for deployment.
npm run build
```

Tests mock HTTP requests and require no Azure resources. The build performs TypeScript checking and produces ignored `frontend/dist/` output. For a manual check, upload a PDF, ask a supported and unsupported question, then stop the backend and verify that an actionable connection error appears. Check keyboard navigation and the layout on a narrow browser window.

