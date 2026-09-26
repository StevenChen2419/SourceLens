# KnowledgeOps

KnowledgeOps is a portfolio enterprise AI knowledge and support platform, built to demonstrate understandable, testable software engineering and applied AI engineering.

The current implementation is the Python 3.12 FastAPI backend foundation for milestone 1. It provides `GET /health`, typed configuration, and tests. Document ingestion, RAG, Azure integrations, and the frontend are not implemented yet.

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

No environment variables or Azure credentials are required to run the foundation. Pydantic settings load optional values from `backend/.env`; process environment variables take precedence.

| Variable | Default | Purpose |
| --- | --- | --- |
| `KNOWLEDGEOPS_APP_NAME` | `KnowledgeOps` | Nonempty application title shown in API documentation |

The example file contains a commented placeholder. Uncomment and replace it only if you want to override the default. Keep `.env` local and never commit credentials. Add future settings and their example placeholders only when their features are implemented.

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

## Run tests

From `backend/`, on Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

On macOS/Linux:

```bash
.venv/bin/python -m pytest
```

Tests verify the health response, configuration precedence, and invalid configuration. They run locally without live Azure calls.

## Project guidance

See [AGENTS.md](AGENTS.md) for architecture decisions, development rules, and the milestone sequence. Keep work focused on the current milestone.
