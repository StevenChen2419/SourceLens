# Evaluation methodology and commands

[Project overview](../README.md) | [Development](development.md) | [Lifecycle](lifecycle.md) | [Evaluation](evaluation.md) | [RAG reference](rag.md) | [Deployment](deployment.md)

## Recorded results and interpretation

[Published results summary](evaluation-results.json) contains the original metrics, denominators, selected answer examples, run timestamps, dataset hash, and raw-artifact SHA-256 hashes. It was extracted from the two preserved September 29, 2026 live Azure runs; full artifacts remain in the ignored `backend/evaluation/results/` directory. The published summary is not a substitute for rerunning the evaluation.

Both runs used dataset v1.0: 26 synthetic handbook cases (18 supported, 8 unsupported), six handbook sections, and top_k=5. Baseline v1 used a shared index containing duplicate/overlapping documents. Controlled Corpus v1 used a separate index with one ingestion of the six-page handbook. Retrieval/model settings, prompts, chunking, dataset and scoring were not tuned between these experiments.

The controlled corpus improved Hit@3 from 16/18 to 17/18 and expected-page recall from 17/18 to 18/18. Classification improved from 25/26 to 26/26; the combined citation check improved from 21/26 to 26/26. Four baseline answers passed answer checks but failed citation checks against the expected source set. A fifth case missed the expected evidence and abstained. These are distinct checks, not contradictory answer-accuracy claims.

Corpus isolation supports the diagnosis that duplicates/overlap crowded retrieval and affected source selection; it is not a broad benchmark or proof that every improvement is solely caused by deduplication. The lifecycle duplicate guard was added afterward. A separate top-10 diagnostic put handbook-18's expected page 6 at rank 4 in the controlled index; Hit@3 still has room to improve. No ranking tuning has been performed.

Retrieval metrics use the 18 supported cases. Classification, combined citation checks and answer checks use all 26. Citation precision/page recall use expected filename/page sets; provenance checks map citations to supplied chunks. Deterministic answer criteria check expected facts/phrases and abstention behavior, not every possible semantic error. A 100% result on this small synthetic dataset does not establish production accuracy or generalization. There are no latency/load benchmarks or production-user claims.

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

