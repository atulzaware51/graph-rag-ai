# GraphSheet AI

GraphSheet AI is a local-first spreadsheet analysis MVP built with FastAPI, SQLite, Neo4j, Pandas, Gemini, and LangGraph. It ingests CSV/XLSX files, profiles and previews their data, builds a graph projection of sheets, columns, evidence chunks, and conversation history, retrieves graph-backed evidence for answers, and generates chat-scoped read-only SQL.

## Features

- Upload CSV, XLS, and XLSX files (maximum 10 MB) with server-generated temporary filenames and validated spreadsheet structure.
- Store each dataset, its column metadata, and searchable text chunks in SQLite.
- List datasets, inspect column profiles, and preview up to 100 rows.
- Ask evidence-backed questions using keyword-ranked chunks and Gemini.
- Generate SQL and execute bounded, read-only queries against only the sheets selected in the active chat (up to five).
- Build dataset, profiled column, and evidence-chunk nodes, with candidate cross-dataset relationships based on similar column names and shared values.
- Show a timestamped graph preview for each uploaded sheet in Overview, limited to the sheet and its entity/column nodes.
- Delete a dataset with its graph inputs, searchable chunks, and dataset-linked conversation/query history; deleting a chat in the web interface removes only that chat and its local history.
- Persist chat messages, selected-sheet links, SQL query events, evidence references, and query results in SQLite. When configured, selected-sheet graph scopes are synchronized to and read back from Neo4j Aura; SQLite remains the source of truth for full spreadsheet tables and the local fallback.
- Use chat-scoped RAG and SQL endpoints that supply selected-sheet graph context and recent chat history to generation; retrieval and SQL access are restricted to the chat's selected sheets.
- Chat RAG retrieves its source evidence from selected-sheet `Evidence` nodes in the graph projection, guided by matching column nodes and their candidate relationship neighbors. The graph is rebuilt from SQLite when Neo4j is not configured. Answers report source chunk IDs and matched columns; candidate relationships remain heuristic.
- Text-to-SQL receives question-matched columns and selected-sheet candidate relationships from the graph, then runs only against the selected SQLite tables using the read-only SQL safety layer.
- Use the framework-free web interface at `/app/` or the OpenAPI interface at `/docs`.

## Architecture and limitations

- FastAPI routes are in `backend/app/api/`; persistence, ingestion, retrieval, profiling, SQL, and graph logic are in `backend/app/services/`.
- SQLite creates its metadata tables when the first dataset is stored. The default database path is `data/graphsheet.db`; run commands from the repository root.
- RAG retrieval is deterministic keyword overlap, not vector search. Retrieval splits underscored column names and normalizes common plurals so natural-language questions can match spreadsheet headers. Uploaded rows remain in SQLite tables; their chunked text and source-row metadata are projected as graph `Evidence` nodes linked to their `Dataset` nodes. Gemini receives only retrieved evidence as the basis for factual answers.
- Selected-sheet graphs include schema profiles, candidate relationships, and (for RAG) the selected sheets' chunk text and source-row metadata as `Evidence` nodes. When Neo4j is configured, those selected evidence chunks are synchronized to the configured Neo4j database; otherwise the graph is rebuilt locally from SQLite. Conversation messages, SQL, execution records, and result references remain persisted in the SQLite conversation graph and are synchronized to Neo4j. Full spreadsheet tables remain in SQLite, where the read-only SQL engine executes queries.
- The Aura URI, username, and password are blank in `.env.example`; the app cannot connect to the user's instance until they are filled in locally. `AURA_INSTANCEID` and `AURA_INSTANCENAME` are optional dashboard metadata and are not used to authenticate.
- Relationships are heuristic **candidates**, not verified foreign keys or approved relationships. They require matching names and overlapping values.
- Schema profiling computes per-column distinct counts; large/complex datasets may take time even within the upload limits.
- There is no authentication, authorization, or multi-user isolation. Run locally and do not expose the development server to an untrusted network.

## Windows PowerShell setup

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` and set `GEMINI_API_KEY` to your own key. To use Neo4j Aura, set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and (optionally) `NEO4J_DATABASE` from the Aura connection details; the database defaults to `neo4j`. Keep credentials only in the ignored local `.env`; do not commit them or put them in source code. `AURA_INSTANCEID` and `AURA_INSTANCENAME` are optional. `GEMINI_MODEL` defaults to `gemini-2.5-flash` and can be changed if your account supports another model. Gemini credentials are needed for RAG answers and Text-to-SQL; upload, preview, profiling, graph, and relationship APIs do not require a Gemini key. Spreadsheet uploads accept `.csv`, legacy Excel `.xls`, and `.xlsx` files.

The `.venv` directory is ignored by Git. If it does not exist, create it with `py -m venv .venv` before installing dependencies. Dependency versions are listed in `requirements.txt`.

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

- Web interface: <http://127.0.0.1:8000/app/>
- API documentation: <http://127.0.0.1:8000/docs>
- Liveness check: <http://127.0.0.1:8000/health>
- Neo4j readiness check: <http://127.0.0.1:8000/ready/neo4j>

`/health` reports whether the API process is running and does not probe Neo4j.
Use `/ready/neo4j` to check the configured Neo4j connection separately; it
returns a structured 503 diagnostic when Neo4j is not configured or cannot be
reached.

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

Create a chat, select up to five sheets, and ask a grounded question:

```powershell
$body = @{ query = "Which products have the highest sales?" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/datasets/<dataset-id>/ask" `
  -ContentType "application/json" -Body $body
```

Update the selected sheets and run RAG or SQL within that chat:

```powershell
$chat = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/conversations/" `
  -ContentType "application/json" `
  -Body (@{ title = "Sales analysis" } | ConvertTo-Json)
$chatId = $chat.conversation.conversation_id

$selection = @{ dataset_ids = @("<dataset-id-1>", "<dataset-id-2>") } | ConvertTo-Json
Invoke-RestMethod -Method Patch `
  -Uri "http://127.0.0.1:8000/conversations/$chatId/datasets" `
  -ContentType "application/json" -Body $selection

$body = @{ query = "Compare sales across both sheets" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/conversations/$chatId/ask" `
  -ContentType "application/json" -Body $body

$body = @{ question = "What is the total revenue?" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/conversations/$chatId/sql" `
  -ContentType "application/json" -Body $body
```

Other endpoints include `GET /datasets/{dataset_id}/schema`, `GET /datasets/{dataset_id}/search`, `GET /graph/`, and `GET /relationships/`.

Conversation history and its persisted graph can be accessed with
`GET /conversations/`, `GET /conversations/{conversation_id}`, and
`GET /conversations/{conversation_id}/graph`. Chat RAG uses
`POST /conversations/{conversation_id}/ask`; chat-scoped SQL uses
`POST /conversations/{conversation_id}/sql`. Update the selected sheets with
`PATCH /conversations/{conversation_id}/datasets` and a
`{"dataset_ids": ["<dataset-id>"]}` JSON body. Both endpoints use recent
same-chat history and graph context; RAG uses evidence from selected sheets,
and SQL execution rejects any table outside the selection. Candidate joins
remain heuristic and are not asserted as verified foreign keys.

Delete one uploaded dataset and its associated backend data:

```powershell
Invoke-RestMethod -Method Delete `
  -Uri "http://127.0.0.1:8000/datasets/<dataset-id>"
```

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
The backend test bootstrap also disables local `.env` loading and clears
provider settings so tests cannot contact or mutate configured Gemini or Neo4j
instances. Tests that exercise provider code use explicit fake credentials and
mock clients. Passing these tests verifies mocked behavior, not live Neo4j
connectivity.

Run the React/Vite checks from the repository root:

```powershell
npm --prefix frontend run lint
npm --prefix frontend run build
```
