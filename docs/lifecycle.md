# Document lifecycle and recovery

[Project overview](../README.md) | [Development](development.md) | [Lifecycle](lifecycle.md) | [Evaluation](evaluation.md) | [RAG reference](rag.md) | [Deployment](deployment.md)

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

1. Start the backend and frontend using the [local development instructions](development.md#run-the-complete-local-application). In another PowerShell terminal, change to `backend/`. Check the selected index without making Azure calls:

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

