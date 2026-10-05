# RAG implementation reference

[Project overview](../README.md) | [Development](development.md) | [Lifecycle](lifecycle.md) | [Evaluation](evaluation.md) | [RAG reference](rag.md) | [Deployment](deployment.md)

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

Source-ID validation establishes provenance, not semantic correctness. Whether passages actually support the answer still depends on model judgment; prompts and structured output cannot guarantee factual grounding. Verify the live supported and unsupported examples below, and compare claims with the cited pages. There is no arbitrary Search-score threshold or hardcoded special case for the Japan question. Partial-ingestion and shared-index [lifecycle limitations](lifecycle.md#states-concurrency-and-failure-behavior) also apply to answers.

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

1. `How many vacation days do full-time employees receive?`: expect a supported answer and PDF/page citations.
2. `How many days each week am I allowed to do my job from home?`: expect a supported answer and PDF/page citations.
3. `What is the capital of Japan?`: expect the fixed insufficient-evidence response and no citations, assuming your uploaded policy PDF does not contain this fact.

It prints each full response and exits with code 1 if the expected status/citation behavior fails. It does not know your PDF's correct vacation or remote-work numbers: manually compare both answers and cited pages with the source PDF. If a check fails, use `/api/retrieval` to inspect the supplied evidence. There are no automatic document writes. Calls incur embedding/Search/generation usage. Alternatively, test **POST /api/answers** in `/docs` with `{"question": "How many vacation days do full-time employees receive?"}`.

Normal pytest tests mock retrieval, credentials, and model clients; they do not call live Azure. Tokenizer data must already be cached for fully offline tests. Mocked tests establish application behavior, not the quality of live model answers.

