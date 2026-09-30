# KnowledgeOps

Production readiness is implemented for Static Web Apps Free and Container Apps Consumption. **No Azure deployment or CD is configured.** See [the manual deployment runbook](docs/deployment.md) for local image checks, managed identity/RBAC, exact CORS, probes, IP restrictions, and the commands to deploy manually. Existing evaluation artifacts and RAG behavior are unchanged.

KnowledgeOps is a portfolio enterprise AI knowledge and support platform, built to demonstrate understandable, testable software engineering and applied AI engineering.

The current implementation connects PDF ingestion, Azure AI Search hybrid retrieval, and grounded GPT-5-mini answers with citations. A React + TypeScript frontend provides a PDF upload panel and a question workspace. The backend provides `POST /api/answers`, independent retrieval debugging through `POST /api/retrieval`, `GET /health`, typed configuration, and tests. Semantic ranking, agents, conversation memory, and user accounts are not implemented yet.

## Run the complete local application

Complete the backend setup and Azure configuration below first. Install Node.js 22.12+ (or 24 LTS) with npm. From the repository root, use two PowerShell terminals:

Terminal 1 — backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Terminal 2 — frontend (first-time setup and startup):

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

On subsequent runs, only `npm run dev` is needed in the frontend terminal. Use `npm ci` for a clean install from the committed lockfile. On macOS/Linux, use `cp .env.example .env` and the backend Python path shown below.

Open [KnowledgeOps](http://127.0.0.1:5173). Select or drop one text-based PDF and click **Upload document**. The UI shows an indeterminate loading state during upload and processing; **Document indexed** appears only after the backend confirms success. Its page and chunk counts come from the backend. Then ask a question and inspect the answer's filename/page citations. These references identify pages in your original PDF; they are not public download links. Unsupported questions display the backend's insufficient-evidence response without sources.

Previously indexed documents remain available to questions. **Your documents** lists the backend's document catalog, refreshes after upload attempts, and provides confirmed deletion. The upload receipt and current answer are temporary browser state; deleting a document does not rewrite an answer already displayed. Upload failures can leave stored data behind; incomplete records remain visible for cleanup.

`frontend/.env` supports `VITE_API_BASE_URL=http://127.0.0.1:8000` (also the default). Restart Vite after changing it. All `VITE_` variables are public browser configuration: **never put credentials in them**. Azure authentication stays exclusively in the backend. Local `.env` files are ignored by Git.

FastAPI allows cross-origin GET, POST, and DELETE requests only from `http://127.0.0.1:5173` and `http://localhost:5173`, without cookies/credentials. Vite uses a fixed port and fails if 5173 is occupied rather than silently switching to an unapproved origin. This is a local development allowlist, not production deployment configuration. CORS is not authentication.

### Frontend checks

From `frontend/`:

```powershell
npm test
$env:VITE_API_BASE_URL = 'https://knowledgeops-api.example.com' # Build-only placeholder; use real HTTPS origin for deployment.
npm run build
```

Tests mock HTTP requests and require no Azure resources. The build performs TypeScript checking and produces ignored `frontend/dist/` output. For a manual check, upload a PDF, ask a supported and unsupported question, then stop the backend and verify that an actionable connection error appears. Check keyboard navigation and the layout on a narrow browser window.

## Document lifecycle and duplicate prevention

The application hashes the exact PDF bytes with SHA-256, independently of the filename. Identical bytes under the same or a different filename return `409` without additional embeddings, original-Blob writes, or Search indexing. Two different PDFs with the same filename are separate documents with different UUIDs. PDFs rewritten with different bytes, even if their extracted text is similar, are not considered identical. This does not remove existing duplicates or overlapping documents automatically.

No database or new package is required. A JSON catalog lives in the existing private Blob container at `catalogs/<Search-service-SHA256>/<index-name>.json`. It records document UUIDs, content hashes, filenames, counts, chunk keys, ownership, and lifecycle states. It is scoped to the configured Search service/index; normal application and controlled-corpus indexes do not share a catalog. Existing UUIDs and citation metadata are never rewritten.

| API | Behavior |
| --- | --- |
| `POST /api/documents` | Same single-PDF multipart contract and four-field `201` response. `409` with `detail.code=duplicate_document`, existing `document_id`, filename, and state for duplicate bytes. A lease conflict is `409` with `detail.code=document_operation_busy`. Invalid uploads retain their existing 413/415/422 behavior; infrastructure/partial failures return 503. |
| `GET /api/documents` | Returns `{"documents": [...]}` with UUID, filename, page/chunk counts, `state`, and `original_retained_on_delete`. Includes incomplete operations and omits deleted tombstones. The first access adopts pre-catalog records. |
| `DELETE /api/documents/{document_id}` | UUID only. Returns `{"document_id": "...", "status": "deleted", "original_retained": false}` after required work succeeds. Legacy originals produce `original_retained: true`. Unknown UUID: 404; malformed UUID: 422; busy: 409; incomplete deletion: 503. Repeating a successful deletion is safe and rechecks saved Search keys. |

### States, concurrency, and failure behavior

Catalog-changing operations hold an [Azure Blob lease](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-lease-python) for the entire operation. The lease has no automatic expiry so a slow embedding request cannot accidentally allow a second writer. Another process receives a conflict rather than running concurrently. Reads of an already initialized catalog do not need the lease. This deliberately serializes uploads/deletions for a small shared collection; it is not a background ingestion system.

Uploads persist an `indexing` record, including all expected chunk keys, **before** embedding/Blob/Search writes. Once indexing and catalog saving finish, the record becomes `indexed`. An ordinary failure records `failed`; a crash or failed final catalog write can leave `indexing`. Both reserve the hash, so retrying the upload cannot silently add a second document. Use the visible incomplete record's deletion action to clean up, then upload again. A lost successful response normally becomes a duplicate conflict on retry, not duplicate indexing.

Deletion persists `deleting`, removes the union of cataloged chunk keys and currently enumerated keys for that UUID in batches, checks every Search result, and verifies that no chunks for that UUID are query-visible. Only then does it delete an owned original PDF. Missing originals are safe on retry. Successful completion saves a `deleted` tombstone and releases the content hash for a future new upload. Tombstones retain keys so repeated deletion requests can also clean up late-visible writes.

If Search deletion fails or its visibility lags, the original remains. If Blob deletion fails after Search removal, the record stays `deleting`. If the final catalog save fails, no success is claimed; retry the same UUID. Deleted documents disappear from listing only after completion. Incomplete counts describe the planned document, not a claim that all chunks exist. Legacy originals that cannot be read have `page_count: null`, displayed as **Page count unavailable**.

Blob Storage and Search do not provide a shared transaction. Partial chunks can still appear in retrieval during failed/in-progress ingestion or deletion; retrieval behavior has intentionally not been changed to filter lifecycle states. In-flight questions can also retain evidence retrieved before deletion. A timed-out request can have an uncertain remote outcome. Wait for Azure operations to settle before recovery, and use the same UUID to retry deletion. No automatic rollback, purge of Blob versions/snapshots, or background cleanup is performed. Azure soft-delete/version-retention policies still apply to originals removed from the active namespace.

### Existing documents and deployment assumptions

On first lifecycle access, the service enumerates existing Search metadata and reads each UUID-named original PDF. It preserves every existing document ID, hashes readable PDFs, and compares their expected chunk keys/metadata with the existing records. Complete matches are `indexed`; incomplete/missing/unreadable originals are `failed`. Existing identical copies are all retained and listed; deleting one does not release the hash while another registered copy remains. If any active legacy document's hash cannot be established, new uploads fail closed until that incomplete record is removed. Original PDFs absent from Search before catalog adoption are not discoverable as documents and remain untouched.

Legacy Blob metadata does not prove exclusive ownership by a particular Search index. Therefore legacy deletion removes Search chunks but leaves the original untouched, and the UI explains that before confirmation. `original_retained: true` means no Blob deletion was attempted; it does not assert that a missing legacy blob exists. New managed documents have explicit catalog ownership and their originals are deleted after Search removal.

Stop older backend processes before first use and allow outstanding writes to finish. After catalog initialization, route normal uploads through `POST /api/documents`; do not write normal-index records using old scripts or Azure Portal. Listing reflects the managed catalog and cannot reconcile arbitrary external edits. Lifecycle metadata enumeration supports up to 10,000 chunks and fails explicitly rather than adopting a truncated corpus. Non-UUID synthetic Search-only smoke records are not valid PDF documents; inspect/remove those separately before adopting a normal index. Controlled evaluation continues to use its existing isolated ingestion and verification commands, without changing its dataset, scoring, or corpus.

### Manual live verification using only disposable documents

No live operations run in unit tests. Keep the application configured for `knowledgeops-chunks`; do **not** point it at `knowledgeops-eval-chunks`. Existing Entra authentication needs Blob read/write/delete/lease permissions and Search read/write/delete permissions (the existing Storage Blob Data Contributor and Search Index Data Contributor roles cover the data operations). No new keys or secrets are used. These checks consume embedding/Search/storage usage; they do not call GPT.

1. Start the backend and frontend using the instructions above. In another PowerShell terminal, change to `backend/`. Check the selected index without making Azure calls:

```powershell
.\.venv\Scripts\python.exe -c "from app.config import Settings; print(Settings().azure_search_index_name)"
```

Expect `knowledgeops-chunks`. Read the current listing with `Invoke-RestMethod http://127.0.0.1:8000/api/documents`. First access creates/adopts the catalog but does not alter existing Search records or original PDFs. Do not continue if listing fails; investigate its configuration/metadata error first.

2. Generate new disposable PDFs in an ignored directory. The random text keeps them distinct from real documents and previous checks. The renamed copy has exactly the same bytes; the second directory has different content with the same filename:

```powershell
@'
from pathlib import Path
from uuid import uuid4
from reportlab.pdfgen import canvas

root = Path('.uv-cache/lifecycle-check')
(root / 'other').mkdir(parents=True, exist_ok=True)
for path, label in [(root / 'lifecycle-check.pdf', 'First'), (root / 'other/lifecycle-check.pdf', 'Second')]:
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 740, f'Disposable lifecycle check {label}: {uuid4()}')
    pdf.showPage()
    pdf.save()
(root / 'renamed.pdf').write_bytes((root / 'lifecycle-check.pdf').read_bytes())
'@ | .\.venv\Scripts\python.exe -
```

3. Upload the first PDF and retain its returned UUID. Stop on a non-201/error; do not guess an ID from a filename:

```powershell
$createdJson = curl.exe --fail-with-body -sS -F "file=@.uv-cache/lifecycle-check/lifecycle-check.pdf;type=application/pdf" http://127.0.0.1:8000/api/documents
if ($LASTEXITCODE -ne 0) { throw "Upload failed: inspect the document list before proceeding." }
$created = $createdJson | ConvertFrom-Json
$created
```

Expect one page and one chunk. Repeat the upload using the renamed identical copy:

```powershell
$duplicateStatus = curl.exe -sS -o .uv-cache/lifecycle-check/duplicate.json -w "%{http_code}" -F "file=@.uv-cache/lifecycle-check/renamed.pdf;type=application/pdf" http://127.0.0.1:8000/api/documents
$duplicate = Get-Content .uv-cache/lifecycle-check/duplicate.json -Raw | ConvertFrom-Json
if ($duplicateStatus -ne '409' -or $duplicate.detail.document_id -ne $created.document_id) { throw "Unexpected duplicate response; inspect before continuing." }
$duplicate
```

Expect `duplicate_document`, the same UUID, the original filename, and `indexed`. Upload the different PDF with the same filename:

```powershell
$otherJson = curl.exe --fail-with-body -sS -F "file=@.uv-cache/lifecycle-check/other/lifecycle-check.pdf;type=application/pdf" http://127.0.0.1:8000/api/documents
if ($LASTEXITCODE -ne 0) { throw "Second upload failed; inspect before proceeding." }
$other = $otherJson | ConvertFrom-Json
if ($other.document_id -eq $created.document_id) { throw "Expected separate identities." }
(Invoke-RestMethod http://127.0.0.1:8000/api/documents).documents | Format-Table document_id,filename,state,page_count,chunk_count
```

The list must contain both newly returned UUIDs and no extra record for the renamed copy. In the frontend, refresh **Your documents**, inspect the state/counts, click Delete for a disposable entry, and cancel: it should remain. Confirming deletion is the destructive step; use only a disposable entry. If identical displayed filenames are ambiguous, use the captured UUID procedure below instead of guessing.

4. Delete only the UUIDs returned by these successful test uploads. This removes the two test originals and their Search chunks; it does not delete any handbook or whole index:

```powershell
foreach ($documentId in @($created.document_id, $other.document_id)) {
    $verifiedId = [guid]::Parse($documentId)
    Invoke-RestMethod -Method Delete "http://127.0.0.1:8000/api/documents/$verifiedId"
}
```

Expect `deleted` and `original_retained: false`. If a request returns 503 due to indexing visibility or an incomplete operation, wait briefly, refresh the list, then repeat deletion for that **same UUID**. Do not re-upload as a cleanup strategy. A second successful DELETE remains safe. Refresh the UI and verify those entries disappear.

5. Optional read-only Azure confirmation for the exact IDs (from `backend/`, same PowerShell session):

```powershell
foreach ($documentId in @($created.document_id, $other.document_id)) {
    .\.venv\Scripts\python.exe -c "import sys; from uuid import UUID; from app.config import Settings; from app.integrations.search import SearchStore; from app.integrations.blob import BlobStore; s=Settings(); d=UUID(sys.argv[1]); assert not SearchStore(s).document_chunks(d), 'Chunks remain'; assert BlobStore(s.azure_storage_account_url,s.azure_storage_container).read_pdf(d) is None, 'Original remains'; print('Verified absent:',d)" "$documentId"
}
```

Re-uploading the first test PDF after successful cleanup should now return 201 with a new UUID. If you do this optional check, remove that new disposable UUID too. To verify legacy handling without deleting real data, inspect `original_retained_on_delete: true` and the confirmation text on existing documents, then cancel. Actual legacy deletion should be a separate deliberate choice.

### Interrupted-operation recovery

Normally the catalog lease is released in `finally`. If the process crashes or lease release fails, operations can remain busy. **Never break a lease while any backend writer may still be running.** Stop all backend instances, wait for outstanding Azure requests to settle, and inspect the catalog/last operation. Do not delete or reset the catalog: it contains ownership, duplicate reservations, and deletion keys.

Print the exact catalog blob path without accessing Azure:

```powershell
.\.venv\Scripts\python.exe -c "from app.config import Settings; from app.integrations.catalog import CatalogStore; print(CatalogStore(Settings()).blob_name)"
```

Only after confirming the lease is stale, use Azure Portal's **Break lease** action on that exact catalog blob (not the container or a source PDF). Restart one backend instance, list documents, and retry deletion of the incomplete UUID before re-uploading. Unknown request outcomes and retained legacy originals require operator review; there is no automatic destructive recovery.

## Milestone 2: Evaluation baseline

The evaluation framework measures the existing pipeline without changing its prompts, chunking, query, context budget, or configured `top_k`. There is no LLM judge or additional evaluation dependency. Run it from `backend/`; it calls application services directly, so neither FastAPI nor Vite needs to be running.

### Dataset and prerequisites

`backend/evaluation/handbook.json` contains 26 version-controlled cases: 18 supported questions (one direct, paraphrased, and semantic/intent question per handbook section) and eight abstention cases. Abstention cases include both general-knowledge questions and plausible policy questions whose details are absent from the handbook. `handbook-reference.md` preserves the extracted source text and PDF SHA-256 for review. Expectations were taken from the existing six-page `knowledgeops-retrieval-test.pdf`, not from model responses.

Each case has an ID, category, section, question, expected status, required filename/page references, a human-readable answer criterion, and optional required/forbidden regex checks. Page numbers are one-based physical PDF pages. All required patterns must match, case-insensitively; any forbidden match fails. Supported cases without required patterns remain unscored for deterministic answer correctness and require manual review. The supplied dataset has checks for all 18 supported cases.

Before running, ensure that exact handbook has already been ingested under its original filename and that `backend/.env` and `az login` still work. Evaluation does not upload anything, create indexes, or modify indexed records. Keep the indexed corpus stable between comparisons and record any corpus changes alongside your results. Other documents can legitimately answer an intended unsupported question and invalidate that expectation; review the retrieved passages before calling this a model failure. Filename/page matching is exact, while citation provenance is checked against actual retrieved document/chunk metadata. Re-upload duplicates do not increase page-based scores.

### Explicit live commands and usage implications

**Do not run these as part of pytest.** A full run makes approximately:

| Mode | Question embedding requests | Search queries | GPT generation requests |
| --- | ---: | ---: | ---: |
| `retrieval` | 26 | 26 | 0 |
| `answers` | 26 | 26 | Up to 26 |

Answer mode observes the retrieval already performed by `answer_question`; it does not retrieve a second time. GPT is skipped when no passages survive the existing evidence selection. SDK retries can add requests; the GPT client permits up to two retries. With current defaults, 26 GPT calls allow up to about 208,000 passage-context tokens plus instructions/questions/message overhead, and up to 106,496 completion tokens (including reasoning). These are budget ceilings, not predicted usage; this small handbook will normally use less. Dollar cost depends on actual tokens, deployment pricing, and retries; check your Azure resource's billing rates. Search also uses the existing service's provisioned capacity. The runner does not estimate dollar cost or collect token usage from clients that do not expose it through the current service interface.

Optional one-case smoke run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m evaluation.run --live --mode answers --limit 1
```

Full baseline, including retrieval and answer metrics in the **same run**:

```powershell
cd backend
.\.venv\Scripts\python.exe -m evaluation.run --live --mode answers
```

Independent retrieval-only baseline (skip this extra run if answer-mode retrieval metrics suffice):

```powershell
.\.venv\Scripts\python.exe -m evaluation.run --live --mode retrieval
```

`--live` is mandatory. `--dataset PATH` accepts another validated dataset; `--output PATH` chooses a new results file and refuses to overwrite an existing one. `--limit N` is for small smoke checks, not a balanced baseline. Configured `RETRIEVAL_TOP_K` must be at least three to measure Hit@3; the runner refuses an incompatible configuration rather than changing it.

### Metrics and diagnosis

| Metric | Definition and denominator |
| --- | --- |
| Hit@1 / Hit@3 | Fraction of supported cases with any expected filename/page among the first 1 / 3 returned chunks. Measures whether relevant evidence ranks near the top. |
| Expected-page recall | Per supported case, fraction of required pages found across all returned chunks; averaged across supported cases. `all_expected_pages_retrieved` additionally requires every expected page. |
| Expected pages supplied | Fraction of supported cases whose expected pages survived the existing generation context selection. |
| Citation precision / page recall | Per supported case, fraction of unique cited pages that are expected / fraction of expected pages cited. Missing citations score zero. Both are macro-averages across supported cases. |
| Citation provenance | Citations' source IDs and document ID, filename, page, chunk ID, and chunk index must match the actual supplied sources. Supported cases require a citation; abstention cases require none. |
| Citations correct | Supported response must cite exactly the required page set with valid provenance; unsupported cases must abstain with no citations and the canonical message. |
| Classification accuracy | Fraction of all cases whose returned `supported` / `insufficient_evidence` status matches the expectation. Operational errors score false. |
| Answer-check accuracy | Fraction passing the configured deterministic fact checks; unsupported cases must return the application's canonical abstention message and no citations. Unsupported text such as an outside-knowledge answer fails even if the status says insufficient evidence. |
| Answer checks given expected evidence | Same supported-answer checks, restricted to cases where every expected page survived context selection. Helps isolate answer failures from retrieval misses. |

Every aggregate includes its denominator. Unsupported questions are excluded from retrieval/correct-page metrics: Search can return irrelevant chunks for them, and the generator must abstain. Errors remain in applicable denominators rather than being silently dropped. Retrieval-only mode reports answer metrics as N/A.

Per-case diagnoses distinguish `retrieval_miss`, `context_selection_miss`, `generation_check_failure`, and `abstention_failure`, plus operational errors by stage. Expected-page presence is a proxy for evidence availability: a large page could contain the relevant fact in a different chunk. Likewise, regex checks can reject valid paraphrases or accept contradictory answers containing the right words. A passing check is **not** proof of factual correctness or entailment. Review the case criterion, retrieved text, supplied sources, answer, and citations together before attributing a failure or tuning anything. In particular, inspect unsupported abstentions and answers that include extra claims. Do not change ground truth simply to improve scores.

### Results and offline tests

The console prints per-case diagnoses and aggregate scores. Timestamped JSON artifacts go to ignored `backend/evaluation/results/`. They include dataset/code hashes, SDK versions, nonsecret deployment/configuration identifiers, elapsed time, actual retrieved chunks in order, selected sources, answers, citations, errors, and computed metrics. **Artifacts contain document text and questions; keep them local and review before sharing.** Endpoints, credentials, vectors, and raw SDK exception messages are not recorded.

Completed cases are saved after each request; interrupted reports retain `complete: false`. Exit code 1 indicates operational failures, 2 indicates invalid invocation/configuration/output, and 0 indicates a completed evaluation without operational errors, not perfect quality. Quality scores and diagnoses remain in the report; no arbitrary acceptance threshold is imposed for this first baseline. No live baseline scores are checked into the repository.

Offline framework tests and the complete backend suite:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py
.\.venv\Scripts\python.exe -m pytest
```

The framework tests mock the embedding, Search, and generation integrations, including failures. They exercise actual application orchestration without live Azure calls.

### Controlled Corpus v1 experiment

Baseline v1 is `backend/evaluation/results/20260929T195615009500Z-answers.json`, SHA-256 `667d2aef7abd8aa1e699e96eb7de75a8ecd712373e4b41b7806eea11a5bf51aa`. Keep it unchanged. The controlled experiment uses the same dataset, scoring, prompts, model settings, chunking, and retrieval settings, with a different corpus in a separate Search index.

**Isolation:** keep `AZURE_SEARCH_INDEX_NAME=knowledgeops-chunks` in your existing backend configuration. Do not set a shell environment override or point the running application at the evaluation index. `--index-name` changes only an in-memory settings copy for that command. Evaluation names must start with `knowledgeops-eval-`; commands reject both `knowledgeops-chunks` and the currently configured application index. They expose no delete/reset/recreate operation. Index creation/validation uses the existing `SearchStore.ensure_index()` and fails on incompatible schemas. Ingestion calls the existing `upload_document()` pipeline, so the original PDF is also stored as a new UUID-named blob in the configured private container. Existing blobs and the application Search index are not modified.

Keep this evaluation index exclusive to these commands, with no concurrent writers. The exact source PDF filename and SHA-256 must match the original handbook recorded in `handbook-reference.md`. An empty-index check and an exclusively created local receipt prevent repeated ingestion attempts from this checkout, even during Search visibility delays. Receipts live in ignored `backend/evaluation/results/corpora/`, keyed by Search service hash and index name. **Do not remove receipts to retry ingestion.** They are local safeguards, not a cross-machine lock. Verification checks the currently visible index contents; Azure indexing visibility can lag and external writers cannot be prevented by this CLI.

Run these steps manually in PowerShell from the repository root. Use your existing `az login` and backend `.env`; no servers need to run. Stop if any command exits nonzero.

```powershell
cd backend
$handbookPath = Join-Path $env:USERPROFILE 'Downloads\knowledgeops-retrieval-test.pdf'
```

1. Ensure the separate index exists with the existing schema:

```powershell
.\.venv\Scripts\python.exe -m evaluation.controlled ensure --live --index-name knowledgeops-eval-chunks
```

2. Ingest exactly one copy through the existing pipeline:

```powershell
.\.venv\Scripts\python.exe -m evaluation.controlled ingest --live --index-name knowledgeops-eval-chunks --pdf "$handbookPath"
```

The command refuses a nonempty index or any previous ingestion receipt. A failed/interrupted attempt may leave a blob or partial chunks; it is deliberately not retried. Use a fresh `knowledgeops-eval-...` index name consistently across all subsequent commands for a new attempt. No cleanup is performed against either index. Do not use the normal upload endpoint for this experiment: it continues writing to `knowledgeops-chunks`.

3. Verify the exact corpus:

```powershell
.\.venv\Scripts\python.exe -m evaluation.controlled verify --live --index-name knowledgeops-eval-chunks --pdf "$handbookPath"
```

Expect one document ID, six pages, six chunks, and pages `[1, 2, 3, 4, 5, 6]` with the existing defaults. Counts are derived from the real PDF with the existing extraction/chunking services. Verification compares all chunk IDs, document IDs, filename/page/chunk metadata, and text against that output, and checks finite embedding values with the configured dimensions. It requests one more record than expected to detect extra records; it does not depend on an approximate count. Inspection uses the SDK's [match-all Search query](https://learn.microsoft.com/en-us/python/api/azure-search-documents/azure.search.documents.searchclient?view=azure-python), separate from question retrieval. If indexing is not yet query-visible, wait briefly and rerun **verify**, never **ingest**. A successful upload alone does not establish the corpus check.

4. Run all 26 cases and save a separate **Controlled Corpus v1** artifact:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run --live --mode answers --index-name knowledgeops-eval-chunks --corpus-pdf "$handbookPath" --output evaluation/results/controlled-corpus-v1-answers.json
```

The runner verifies the corpus before sending questions and records its identity/counts alongside the existing run settings and scores. The same configured `RETRIEVAL_TOP_K` (5 in Baseline v1) is used. Existing output files, including Baseline v1, are never overwritten. If rerunning, choose a new artifact filename. Setup/verification failures return nonzero; they do not trigger deletion or reingestion.

5. Diagnose `handbook-18` independently with a larger result set:

```powershell
.\.venv\Scripts\python.exe -m evaluation.controlled diagnose --live --index-name knowledgeops-eval-chunks --pdf "$handbookPath" --top-k 10
```

This verifies the corpus, loads the unchanged question from `handbook.json`, and calls the existing retrieval service with a request-local `top_k=10`. It prints rank, filename, page, Search score, and the observed rank(s) of page 6. It makes no GPT calls, changes no stored configuration, and does not affect the 26-case evaluation's `top_k`. A six-chunk corpus returns at most six results even when ten are requested. A missing page 6 is reported explicitly rather than assigned an invented rank.

Live usage: setup performs schema requests; ingestion performs the normal embedding batch request(s), Blob upload, and Search writes for the handbook. Verification adds bounded read-only Search queries. The complete evaluation still uses 26 question embeddings, 26 hybrid queries, and up to 26 GPT calls, plus retries and the corpus preflight query. The focused diagnostic adds one question embedding and one hybrid query, plus verification. None of these live commands runs automatically in pytest.

Compare `controlled-corpus-v1-answers.json` against Baseline v1 without relabeling or editing either artifact. Check the four citation-set mismatches (01, 02, 04, 05), case 18's retrieval and classification, and overall metrics. This experiment changes corpus composition; generation remains nondeterministic, so one comparison indicates an effect but does not prove all score changes were caused by the corpus. Preserve the diagnostic output separately if needed; it is not a replacement baseline.

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
| `KNOWLEDGEOPS_APP_NAME` | `KnowledgeOps` | Nonempty application title shown in API documentation |
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

Chunks and their vectors are indexed in Azure AI Search after embedding generation and original-Blob storage. Re-uploading identical PDF bytes returns 409 without creating another document, Blob, embedding, or Search chunk. Different bytes under the same filename are accepted. See the consistency limitations below before retrying a failed ingestion.

| Status | Meaning |
| --- | --- |
| `201` | PDF processed, original bytes stored, and every chunk write acknowledged by Search |
| `413` | File exceeds the configured limit |
| `415` | Non-PDF extension/type/signature, empty file, or unsuitable filename |
| `422` | Missing/multiple files, malformed PDF, encrypted PDF, or no extractable text |
| `503` | Blob, embedding, or Search configuration/API/schema/indexing failure; partial data may remain |

Error responses intentionally omit SDK internals. For a `503`, check service settings, CLI login, the required data roles, and network access. Run the explicit ensure-index command below to diagnose schema incompatibility. If Blob storage succeeded and indexing then failed, the error detail includes the generated `document_id` for inspection. The API does not report ingestion success on embedding or indexing failure.

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

## Local PDF extraction and chunking

From a Python session in `backend/` using the virtual environment:

```python
from pathlib import Path

from app.services.pdf import extract_pdf_pages
from app.services.chunking import ChunkingOptions, chunk_pages

pages = extract_pdf_pages(Path("handbook.pdf").read_bytes())
chunks = chunk_pages(
    pages,
    document_id="handbook-v1",  # Reuse this identity when reprocessing this document.
    filename="handbook.pdf",
    options=ChunkingOptions(max_tokens=500, overlap_tokens=75),
)
print(chunks[0].model_dump())
```

Extraction uses `pypdf` with strict parsing and returns a typed `PDFPage` for every page. Page numbers are one-based physical PDF positions, not printed page labels. Empty pages remain in the extracted result so later pages keep their original numbers. Chunking skips empty or whitespace-only pages. Within nonempty pages, all text (including whitespace) is preserved.

Chunking uses `tiktoken`'s `cl100k_base` encoding with configurable defaults of 500 tokens per chunk and 75 overlapping source tokens. These are local tokenizer counts, not GPT generation token counts. No embedding model is called. Each chunk stays within one page, and its independently tokenized text does not exceed the configured limit. Windows preserve complete Unicode characters; overlap can be slightly smaller to maintain valid text and forward progress. Extremely small token limits that cannot fit a character raise `ValueError`. Source strings resembling tokenizer special tokens are treated as ordinary text.

Every `DocumentChunk` contains `document_id`, `filename`, `page_number`, `chunk_id`, `chunk_index`, and `text`. Chunk indices are zero-based across the document in supplied page order. IDs are SHA-256 hashes of the document ID, page number, chunk index, and exact chunk text. Identical input, document identity, tokenizer, and options produce identical IDs. Changing content or chunk boundaries can change IDs; filenames are display metadata and do not affect IDs.

Errors are explicit:

- `MalformedPDFError`: invalid, truncated, or unreadable PDF structure/text. Strict parsing may reject PDFs that a viewer can repair.
- `NoExtractableTextError`: no page has extractable text. Image-only, scanned, and entirely blank PDFs are unsupported; OCR is not implemented.
- `UnsupportedPDFError`: encrypted PDFs are unsupported. `NoExtractableTextError` is also a subclass of this error.
- Pydantic validation errors: invalid chunk options, including nonpositive sizes or overlap outside `0 <= overlap < max_tokens`.

Extraction does not reconstruct table structure or guarantee reading order for complex layouts. In mixed text/image PDFs, only extractable text is processed; image-only pages are empty. All processing currently happens in memory and is intended for local, modest-size documents.

`tiktoken` downloads its public encoding data on first use and caches it locally. This requires network access once; subsequent runs can work offline with the cache present. For a restricted environment, prepopulate the cache and set `TIKTOKEN_CACHE_DIR` to that directory. No document text is sent during this download. See the [tiktoken source](https://github.com/openai/tiktoken) and [pypdf extraction documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) for library details.

## Embedding generation

Deploy **text-embedding-3-small** in Azure Foundry. Open the deployed model's details / **Consume** or **View code** panel. Copy the Azure OpenAI resource endpoint from its Python OpenAI example, using the v1 base URL ending in `/openai/v1/`. Do not use the Foundry project URL (`/api/projects/...`), Blob endpoint, or the full `/embeddings` operation URL. A typical URL is `https://<resource-name>.openai.azure.com/openai/v1/`; some Foundry resources use `https://<resource-name>.services.ai.azure.com/openai/v1/`.

Add these settings to your existing local `backend/.env` (replace both placeholders):

```dotenv
AZURE_EMBEDDING_ENDPOINT=https://<your-resource-name>.openai.azure.com/openai/v1/
AZURE_EMBEDDING_DEPLOYMENT=<your-deployment-name>
EMBEDDING_DIMENSIONS=1536
EMBEDDING_BATCH_SIZE=16
```

The deployment value is the **deployment name** shown on that deployment's details page, not necessarily its model name. Confirm its underlying model is `text-embedding-3-small`. This service rejects a different returned model. The dimension setting is a local choice, not an Azure credential: 1,536 is the default full vector size; values from 1 through 1,536 are allowed. Batch size is also local, from 1 through 32. No API version variable is needed for the v1 endpoint, and no keys are used.

Your signed-in identity needs **Cognitive Services OpenAI User** on the Azure OpenAI resource (Azure Portal → resource → **Access control (IAM)**). The Blob data role alone does not grant model inference access. Authenticate with `az login` under the same OS user running Python. The integration uses `DefaultAzureCredential` and a renewable Entra token provider with the OpenAI Python client, following [Microsoft's Azure embedding guidance](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/embeddings).

Missing endpoint/deployment settings raise `EmbeddingConfigurationError` when generation is invoked. Invalid URLs, dimensions, or batch sizes fail Pydantic settings validation. Embedding configuration is now required for successful document ingestion, but not for health checks.

### Call the service with existing chunks

```python
from app.config import Settings
from app.services.embeddings import embed_chunks

embedded_chunks = embed_chunks(chunks, Settings())
# Each result has .chunk (all existing metadata), .vector, .dimensions,
# .deployment, and .model. Results stay in the original chunk order.
```

The service validates all inputs before making requests, rejecting blank text and inputs above 8,192 `cl100k_base` tokens. It submits batches of 16 by default, with a maximum batch size of 32 to remain below the aggregate request token limit even for maximum-length inputs. It sends the configured dimensions explicitly and verifies every returned vector against that size. Response counts, indices, model identity, and finite numeric values are checked. Provider response order does not change chunk association.

Authentication, timeout, rate-limit, and API failures raise `EmbeddingAPIError` with a safe message. Invalid responses raise `EmbeddingResponseError`. The SDK has a 30-second timeout and up to two retries for retryable failures. If any batch fails, the call raises without returning partial results; earlier batches may already have incurred usage. The service does not log source text, vectors, or raw SDK exceptions. Avoid enabling SDK debug/body logging for private documents.

The embedding service returns vectors in memory; the ingestion service subsequently indexes them. Keep the model and dimension configuration consistent for every document in an index. Future query embeddings must use that same model and dimension count. Changing either requires re-embedding existing documents into an appropriate index.

### Manual Azure smoke test (separate from pytest)

From `backend/` in PowerShell, install updated dependencies and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
az login
.\.venv\Scripts\python.exe scripts/smoke_embeddings.py
```

On macOS/Linux use `.venv/bin/python` instead. This command makes a real, billable embedding request using one short fixed sentence. Success prints the model name, validated dimension count, and the first five numeric vector values, then exits with code 0. Failure prints a safe configuration/API error and exits with code 1. It does not upload a document or create any search resources.

The normal pytest suite mocks credentials and the embedding client, requires no Azure deployment, and does not call Azure. As with chunking tests, tokenizer data must be cached for fully offline operation. A passing mocked suite does not verify a real deployment; run the smoke test after deploying and configuring it.

## Azure AI Search indexing

Add these values to your existing `backend/.env`:

```dotenv
AZURE_SEARCH_ENDPOINT=https://<your-search-service-name>.search.windows.net
AZURE_SEARCH_INDEX_NAME=knowledgeops-chunks
```

Copy the endpoint from the Azure AI Search resource's **Overview → URL** in Azure Portal. The index name is an application choice; the default above is suitable. Keep `EMBEDDING_DIMENSIONS=1536` with the current deployment. No API key or connection string is needed. Search must allow Microsoft Entra/RBAC data-plane access and your development machine's network access.

Use `az login` as before. **Search Service Contributor** permits index definition management; **Search Index Data Contributor** permits document writes and readback. Both capabilities are needed for the ingestion and smoke-test workflows. The integration uses `DefaultAzureCredential`; it does not create an Azure Search service or modify RBAC.

### Schema and lifecycle

| Field | Azure type | Behavior |
| --- | --- | --- |
| `chunk_id` | `Edm.String` | Key, retrievable |
| `document_id` | `Edm.String` | Filterable, retrievable |
| `filename` | `Edm.String` | Filterable, retrievable |
| `page_number` | `Edm.Int32` | Filterable, retrievable; one-based PDF page |
| `chunk_index` | `Edm.Int32` | Retrievable; zero-based document-wide position |
| `text` | `Edm.String` | Searchable, retrievable |
| `embedding` | `Collection(Edm.Single)` | Vector searchable, retrievable; configured embedding dimensions |

The vector field uses `chunks-vector-profile` and `chunks-hnsw`: HNSW with cosine distance, `m=4`, `efConstruction=400`, and `efSearch=500`. These are ordinary starting values for approximate nearest-neighbor indexing, without compression, semantic ranking, or integrated vectorization. See [Azure vector index guidance](https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-create-index).

From `backend/`, explicitly ensure the index before the first upload:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
az login
.\.venv\Scripts\python.exe scripts/ensure_search_index.py
```

The command creates a missing index or validates an existing one. Ingestion also performs this check before generating embeddings. Existing incompatible fields, dimensions, or vector configuration fail clearly. No existing index is deleted, overwritten, or automatically migrated. Select a new index name or plan an explicit migration when the schema changes. Service-managed defaults are ignored in compatibility checks; HNSW tuning values may differ while the required profile and cosine/HNSW algorithm must match.

Index uploads batch up to 100 chunks per call and use a conservative 8 MB estimated JSON-payload ceiling. Every result must report success with exactly the expected keys and result count. A partial result causes failure, and subsequent batches are not submitted. Stable chunk keys allow SDK retries to upsert the same records; the managed HTTP upload reserves a PDF content hash before writing, so an identical repeat returns the existing identity instead of generating duplicate Search records.

### Ingestion ordering and consistency

The managed sequence is validation → extraction → chunking → catalog lease/hash reservation → ensure index → embeddings → Blob upload → Search indexing → catalog completion. The existing four-field upload response remains unchanged. `201` means Search acknowledged all chunk writes and the indexed catalog state was saved; search visibility can lag briefly behind write acknowledgment. The isolated evaluation ingestion entrypoint retains its original sequence and its own single-ingestion guard.

- Schema or embedding failure: no original PDF or chunk writes; a failed catalog reservation and newly created empty index may remain.
- Blob failure: Search document indexing is not attempted. An ambiguous network failure can still leave a committed Blob.
- Search failure after Blob success: return `503` with `document_id`. Keep the original Blob and any successful chunk writes for inspection; there is no automatic rollback or deletion.
- Search success followed by original-Blob failure cannot happen in this order. A lost HTTP response after success returns an existing-document conflict on identical retry. A failed final catalog save can leave `indexing` visible even though chunks exist.

There is no cross-service transaction or background cleanup. The durable catalog tracks state and duplicate reservations, but partial chunks may be visible in Search even though the API reported failure. Use listing and retryable deletion to remove an incomplete managed document before re-uploading. The development retrieval endpoint reads the shared index, so it can include partial ingestion records and leftover synthetic smoke records. Keep the evaluation corpus controlled when assessing retrieval quality. A same-dimensional change to the embedding model/deployment also needs deliberate re-embedding; the schema cannot detect that semantic change.

### Manual Search smoke test

Run separately from pytest:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_search.py
```

This authenticates, creates/validates the index, writes one uniquely named `smoke-...` chunk, and polls direct key lookup for up to roughly ten seconds to account for indexing visibility delay. It verifies all metadata and the vector readback. It uses a synthetic nonzero 1,536-dimensional vector, so it tests Search independently without calling the embedding deployment or Blob Storage. This is inspection by key, not user-question retrieval.

Success prints the key and dimension count. The synthetic record intentionally remains for inspection; remove that exact record after verification, or use a separate disposable index name for the smoke test. No script deletes indexes. To exercise the complete pipeline, start FastAPI and upload a small text PDF through `/docs`; expect `201` only after all three services succeed.

Normal tests mock Azure clients and application dependencies. They do not create live indexes or consume Azure resources. A passing suite does not establish that your live RBAC, quotas, endpoint, or service connectivity works.

## Hybrid retrieval

`POST /api/retrieval` accepts a JSON question and optional `top_k`. Questions must contain non-whitespace text, fit within 4,000 characters, and stay within the embedding model's token limit. `top_k` must be an integer from 1 through 20. If omitted, it uses the existing settings system with this optional `backend/.env` value:

```dotenv
RETRIEVAL_TOP_K=5
```

The question uses the same embedding deployment and `EMBEDDING_DIMENSIONS=1536` as indexed documents. One Azure Search request includes `search_text`, `search_fields=["text"]`, and a `VectorizedQuery` over `embedding`. The vector branch contributes up to 50 candidates; `top_k` controls the final result count. Azure combines keyword and vector ranks through reciprocal rank fusion (RRF). The backend preserves that order without semantic reranking. See [Microsoft's hybrid-search overview](https://learn.microsoft.com/azure/search/hybrid-search-overview).

Each result includes `document_id`, `filename`, one-based `page_number`, `chunk_id`, zero-based `chunk_index`, `text`, and nullable `search_score`. This is the combined `@search.score`, not a probability, confidence percentage, or raw cosine similarity. Separate keyword/vector scores are not requested. Vectors are omitted from the response. No answer, generated citation IDs, or LLM interpretation is produced.

No matches returns `200` with `results: []`. Invalid questions/counts return `422`; embedding or Search failures return a safe `503`. A result is not proof that the document answers the question: vector search can return weakly related passages even for unanswerable questions. Use this endpoint to inspect evidence when evaluating generated answers. The endpoint performs no index creation or document writes and uses the same Entra credentials and Azure settings as ingestion. Existing Search Index Data Contributor access supports querying; Search Index Data Reader is sufficient for a query-only identity, alongside embedding inference permission.

### Manual retrieval test against an uploaded PDF

Keep your existing Blob, embedding, and Search settings. From the repository root in PowerShell:

```powershell
cd backend
az login
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In another PowerShell terminal, enter a question about the PDF you uploaded:

```powershell
$question = Read-Host 'Question about your uploaded PDF'
$body = @{ question = $question; top_k = 5 } | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/retrieval' -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$result | ConvertTo-Json -Depth 6
```

Alternatively, open `/docs`, expand **POST /api/retrieval**, click **Try it out**, enter `{"question": "Your question about the PDF", "top_k": 5}`, and execute.

Check that the top results contain the expected passages and that the filename, document ID, and page numbers match your PDF. Try a paraphrase as well as an exact phrase, then an unrelated question to inspect weak matches. This makes a real embedding and Search request; it does not call GPT or modify the index. Normal pytest tests mock Azure and do not depend on this manual check.

## Grounded GPT-5-mini answers

`POST /api/answers` accepts the same question and optional `top_k` as retrieval. It calls the existing retrieval service, selects unique complete passages within an evidence token budget, and assigns request-local IDs (`S1`, `S2`, ...). Only passage text and source IDs are supplied to GPT-5-mini as evidence; filenames, page numbers, scores, and other citation metadata stay in application code. Duplicate retrieved document/chunk pairs share one ID. Oversized passages are skipped, not cut mid-sentence. The evidence budget counts serialized passage data using `o200k_base`, excluding the short system instructions, question, and output budget.

The system instructions require evidence-only answers, treat questions and document instructions as untrusted data, prohibit answering from general knowledge, and require abstention when the evidence is incomplete or irrelevant. Strict JSON output contains only `status`, `answer`, and `source_ids`. The application rejects unknown IDs and supported answers without text or citations, then constructs and deduplicates citations from trusted retrieval metadata. Each citation includes source ID, document ID, filename, one-based page number, chunk ID, and chunk index. No source-download endpoint exists yet, so citation links are not invented.

Responses contain `question`, `status`, `answer`, and `citations`. Status is `supported` or `insufficient_evidence`. No retrieval results, no usable passages within the budget, or a model insufficient-evidence decision produces:

```json
{
  "question": "What is the capital of Japan?",
  "status": "insufficient_evidence",
  "answer": "The retrieved documents do not contain enough evidence to answer this question.",
  "citations": []
}
```

This text is constructed by the application; model text is discarded on abstention. Unknown source IDs, malformed JSON, truncated output, refusal, or invalid supported-answer structure return a safe `502`. Azure/configuration/retrieval failures return `503`, and invalid questions return `422`. These operational errors are distinct from insufficient evidence.

Source-ID validation establishes provenance, not semantic correctness. Whether passages actually support the answer still depends on model judgment; prompts and structured output cannot guarantee factual grounding. Verify the live supported and unsupported examples below, and compare claims with the cited pages. There is no arbitrary Search-score threshold or hardcoded special case for the Japan question. Partial-ingestion and shared-index limitations described above also apply to answers.

### Generation configuration

Add to your existing local `backend/.env`:

```dotenv
AZURE_GENERATION_ENDPOINT=https://<your-gpt-resource-name>.openai.azure.com/openai/v1/
AZURE_GENERATION_DEPLOYMENT=<your-existing-gpt-5-mini-deployment-name>
GENERATION_CONTEXT_TOKENS=8000
GENERATION_MAX_COMPLETION_TOKENS=4096
```

In Foundry, open your existing **GPT-5-mini deployment → Consume / View code**. Copy its Azure OpenAI v1 base URL (some resources use `.services.ai.azure.com/openai/v1/`) and exact deployment name. Do not use the project URL or the embedding deployment name. If both deployments share a resource, their endpoint values can be identical. The last two settings are local application defaults, not Azure values to look up. Preserve your existing embedding and Search settings; no real values belong in `.env.example`.

Generation reuses the installed OpenAI SDK with `DefaultAzureCredential` and a renewable Entra token provider. Run `az login` as the same OS user as FastAPI; your identity needs **Cognitive Services OpenAI User** on the generation resource. No key or API-version setting is needed. Missing generation configuration is checked only when a model call is needed, so independent retrieval remains usable.

The request uses Chat Completions with strict JSON schema, low reasoning effort, and a 4,096-token completion cap (including reasoning tokens). No temperature or tools are sent. The client uses a 60-second timeout and up to two retries for retryable failures. A token-budget truncation is an error, never a partial answer. The application does not log question/passage contents or raw SDK exceptions; avoid SDK debug-body logging with private documents. See the [GPT-5-mini model documentation](https://developers.openai.com/api/docs/models/gpt-5-mini) and [structured-output guidance](https://developers.openai.com/api/docs/guides/structured-outputs).

### Live answer smoke test

Start or restart FastAPI from `backend/` after configuring `.env`:

```powershell
az login
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In a second terminal, from `backend/`:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_answers.py
```

The script sends these exact questions to the running local API, using real Azure services:

1. `How many vacation days do full-time employees receive?` — expect a supported answer and PDF/page citations.
2. `How many days each week am I allowed to do my job from home?` — expect a supported answer and PDF/page citations.
3. `What is the capital of Japan?` — expect the fixed insufficient-evidence response and no citations, assuming your uploaded policy PDF does not contain this fact.

It prints each full response and exits with code 1 if the expected status/citation behavior fails. It does not know your PDF's correct vacation or remote-work numbers: manually compare both answers and cited pages with the source PDF. If a check fails, use `/api/retrieval` to inspect the supplied evidence. There are no automatic document writes. Calls incur embedding/Search/generation usage. Alternatively, test **POST /api/answers** in `/docs` with `{"question": "How many vacation days do full-time employees receive?"}`.

Normal pytest tests mock retrieval, credentials, and model clients; they do not call live Azure. Tokenizer data must already be cached for fully offline tests. Mocked tests establish application behavior, not the quality of live model answers.

## Project guidance

See [AGENTS.md](AGENTS.md) for architecture decisions, development rules, and the milestone sequence. Keep work focused on the current milestone.
