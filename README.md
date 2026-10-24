# GraphSheet AI

GraphSheet AI is a local-first spreadsheet analysis MVP built with FastAPI, SQLite, Pandas, Gemini, and LangGraph. It ingests CSV/XLSX files, profiles and previews their data, retrieves supporting spreadsheet evidence for answers, generates dataset-scoped read-only SQL, and builds a schema-level graph with heuristic relationship candidates.

## Features

- Upload CSV and XLSX files (maximum 10 MB) with server-generated temporary filenames and validated spreadsheet structure.
- Store each dataset, its column metadata, and searchable text chunks in SQLite.
- List datasets, inspect column profiles, and preview up to 100 rows.
- Ask evidence-backed questions using keyword-ranked chunks and Gemini.
- Generate SQL through the existing LangGraph workflow and execute bounded, read-only queries against only the selected dataset.
- Build dataset/column nodes and candidate cross-dataset relationships based on similar column names and shared values.
- Use the framework-free web interface at `/app/` or the OpenAPI interface at `/docs`.

## Architecture and limitations

- FastAPI routes are in `backend/app/api/`; persistence, ingestion, retrieval, profiling, SQL, and graph logic are in `backend/app/services/`.
- SQLite creates its metadata tables when the first dataset is stored. The default database path is `data/graphsheet.db`; run commands from the repository root.
- RAG retrieval is deterministic keyword overlap, not vector search. Gemini receives the question and retrieved evidence for RAG answers; Text-to-SQL sends the question and selected dataset schema.
- Graphs are assembled from stored schemas on demand; there is no graph database or separately persisted graph.
- Relationships are heuristic **candidates**, not verified foreign keys or approved relationships. They require matching names and overlapping values.
- Schema profiling computes per-column distinct counts; large/complex datasets may take time even within the upload limits.
- There is no authentication, authorization, or multi-user isolation. Run locally and do not expose the development server to an untrusted network.

## Windows PowerShell setup

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` and set `GEMINI_API_KEY` to your own key. Do not commit `.env` or put a real key in source code. `GEMINI_MODEL` defaults to `gemini-2.5-flash` in `.env.example` and can be changed if your account supports another model. Gemini credentials are needed for RAG answers and Text-to-SQL; upload, preview, profiling, graph, and relationship APIs do not require a Gemini key.

The `.venv` directory is ignored by Git. If it does not exist, create it with `py -m venv .venv` before installing dependencies. Dependency versions are listed in `requirements.txt`.

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

- Web interface: <http://127.0.0.1:8000/app/>
- API documentation: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

The SQLite database is initialized lazily on the first successful upload. No migration command is currently required.

## API examples

Upload a spreadsheet:

```powershell
curl.exe -F "file=@C:\path\to\sales.csv" http://127.0.0.1:8000/documents/upload
```

List datasets and retrieve a bounded preview:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/datasets/
Invoke-RestMethod "http://127.0.0.1:8000/datasets/<dataset-id>/preview?limit=10"
```

Ask a grounded question:

```powershell
$body = @{ query = "Which products have the highest sales?" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/datasets/<dataset-id>/ask" `
  -ContentType "application/json" -Body $body
```

Ask a SQL question:

```powershell
$body = @{ question = "What is the total revenue?" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/datasets/<dataset-id>/ask-sql" `
  -ContentType "application/json" -Body $body
```

Other endpoints include `GET /datasets/{dataset_id}/schema`, `GET /datasets/{dataset_id}/search`, `GET /graph/`, and `GET /relationships/`.

## Security notes

- Upload parsing checks supported extensions, file size, CSV/XLSX structure, headers, and spreadsheet row/column limits. XLSX macro-enabled content is rejected and formulas are read as cached values rather than executed.
- SQL uses SQLite read-only URI mode, a restrictive authorizer, dataset table registration checks, a five-second execution limit, a 500-row maximum, and a response-size bound. Dataset-scoped queries cannot read the registry or other tables.
- Keep `.env`, database files, and uploaded data private. Gemini-backed requests transmit the selected question and, depending on the workflow, retrieved evidence or the selected dataset schema to the configured Google provider.
- The application does not provide user accounts, access control, or protection against a malicious local process with access to the SQLite file.

## Tests

Run the backend suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Automated tests mock Gemini integrations and do not require paid API calls.
