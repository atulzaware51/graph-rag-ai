# GraphSheet AI

GraphSheet AI is a spreadsheet analysis application built with FastAPI, SQLite or PostgreSQL, SQLAlchemy, Neo4j, ChromaDB, Pandas, Gemini/Groq/Mistral, and LangGraph. It ingests CSV/XLSX files, profiles and previews their data, persists dataset knowledge graphs and conversation history, retrieves graph-backed evidence for answers, and generates chat-scoped read-only SQL.

## ARCHITECTURE
![alt text](image.png)
## Features

- Upload CSV, XLS, and XLSX files (maximum 10 MB) with server-generated temporary filenames and validated spreadsheet structure.
- Expose each Excel worksheet as a separately selectable dataset; reuse a previously stored dataset when its normalized sheet-content fingerprint matches.
- Store dataset rows, schema, searchable text chunks, and an embedding cache in SQLite; index evidence embeddings in a persistent local ChromaDB collection.
- List datasets, inspect column profiles, and preview up to 100 rows.
- Ask evidence-backed questions using hybrid local-vector, keyword, and knowledge-graph retrieval with configured LLM providers; calculation, filter, ranking, and ordering questions use selected-sheet read-only SQL even when evidence chunks match.
- Return validated structured tables for supported deterministic counts, sums, averages, rankings, and record lookups, as well as SQL-assisted Analyst answers.
- Generate SQL and execute bounded, read-only queries against only the sheets selected in the active chat (up to five).
- Generate multiple independent, read-only SQL result tables directly in AI Analyst from one natural-language request; each result shows its SQL, status, and rows in the same persistent chat.
- Generate SQL code, including DDL, separately from SQL analysis; generated code is displayed for review and copying and is never executed against uploaded sheets.
- Build dataset, profiled column, and evidence-chunk nodes, with candidate cross-dataset relationships based on similar column names and shared values.
- Show a timestamped graph preview for each uploaded sheet in Overview, limited to the sheet and its entity/column nodes.
- Delete a dataset with its graph inputs, searchable chunks, and dataset-linked conversation/query history; deleting a chat in the web interface removes only that chat and its local history.
- Persist chat messages, selected-sheet links, SQL query events, evidence references, and query results in SQLite. Dataset graph nodes and directed edges use Neo4j when configured, with the SQL graph store as an availability fallback; otherwise they use the database selected by `DATABASE_URL` (SQLite by default, PostgreSQL supported).
- Use chat-scoped RAG and SQL endpoints that supply selected-sheet graph context and recent chat history to generation; retrieval and SQL access are restricted to the chat's selected sheets.
- Preserve selected-sheet conversational context, including prior SQL results, for follow-up questions; exclude past answers tied to sheets that are no longer selected.
- Chat RAG retrieves selected-sheet evidence from ChromaDB and uses graph `Evidence` nodes, matching column nodes, and candidate relationship neighbors as context. ChromaDB is a vector index, not a graph database. The knowledge graph is stored in Neo4j when configured and rebuilt from SQLite dataset records; if Neo4j is unavailable, the configured SQL graph store is used as a fallback. Answers report source chunk IDs and matched columns; candidate relationships remain heuristic.
- Text-to-SQL receives question-matched columns and selected-sheet candidate relationships from the graph, then runs only against the selected SQLite tables using the read-only SQL safety layer.
- SQL code generation uses the active chat's selected-sheet schemas as context, but does not execute generated statements. SQL analysis remains limited to independently validated, read-only SELECT queries.
- Multi-query Analyst SQL analysis is stored with the existing conversation messages and bounded result snapshots; no schema migration is required. Each query executes separately, so a failed query does not discard successful sibling results. Heuristic joins require explicit approval; declared SQLite foreign keys are verified automatically.
- Use the framework-free web interface at `/app/` or the OpenAPI interface at `/docs`.

## Architecture and limitations
<img width="3644" height="4228" alt="image" src="https://github.com/user-attachments/assets/3b0fbdc7-8585-4a49-96f0-cce22d390a4f" />

- FastAPI routes are in `backend/app/api/`; persistence, ingestion, retrieval, profiling, SQL, and graph logic are in `backend/app/services/`.
- SQLite creates its metadata tables when the first dataset is stored. The default database path is `data/graphsheet.db`; run commands from the repository root.
- RAG retrieval combines ChromaDB cosine similarity over locally generated sentence embeddings with keyword overlap and matching graph columns. Vectors are stored in the persistent collections named `graphsheet_evidence_<dimensions>` under `CHROMA_PERSIST_DIRECTORY` (default `data/chroma`), filtered by selected dataset IDs. SQLite retains the source chunks and a float32 embedding cache so Chroma can be rebuilt if needed. Older datasets are indexed in Chroma on first selected-sheet retrieval. The default model is `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions); configure `EMBEDDING_MODEL` to use another compatible local sentence-transformers model. First use may download the model; afterward its Hugging Face cache can be used offline. If embeddings cannot be generated or searched, the operation reports an error rather than silently reverting to keyword-only retrieval. Uploaded rows remain in SQLite tables, and chunk text, source rows, columns, and file/sheet labels are represented as graph `Evidence` nodes linked to their `Dataset` nodes.
- Selected-sheet graphs include schema profiles, candidate relationships, and evidence provenance. When Neo4j is configured, dataset graph nodes, edges, and build markers are stored there and graph reads prefer Neo4j; the database selected by `DATABASE_URL` is used when Neo4j is not configured or unavailable. Existing datasets are rebuilt into Neo4j when graph endpoints or chat retrieval next build the graph. Conversation messages, SQL, executions, and result references remain in SQLite. Full spreadsheet tables remain in SQLite, where the read-only SQL engine executes queries.
- AI Analyst SQL generation uses `LLM_PROVIDER` followed by `LLM_FALLBACK_PROVIDERS`; missing provider keys are skipped. Gemini, Groq, and Mistral calls have a 30-second timeout and at most two attempts per configured provider for transient rate limits, temporary provider errors, and transport timeouts. Provider quota exhaustion, authentication failure, model unavailability, and malformed responses are reported separately without returning credentials or raw provider response bodies. Generated SQL is independently validated and executed with the existing read-only safety layer. Conversation and graph writes use SQLite WAL, a bounded busy timeout, short transactions, and bounded retries for SQLite lock contention. Conversation event writes also use request idempotency keys so retrying a completed chat request does not regenerate the answer.
- The Aura URI, username, and password are blank in `.env.example`; the app cannot connect to the user's instance until they are filled in locally. `AURA_INSTANCEID` and `AURA_INSTANCENAME` are optional dashboard metadata and are not used to authenticate.
- Relationships are heuristic **candidates**, not verified foreign keys or approved relationships. They require matching names and overlapping values.
- Schema profiling computes per-column distinct counts; large/complex datasets may take time even within the upload limits.
- There is no authentication, authorization, or multi-user isolation. Run locally and do not expose the development server to an untrusted network.
## WORKING OF THE PROJECT S
<img width="8228" height="10656" alt="image" src="https://github.com/user-attachments/assets/7b5b0579-8265-4173-97ad-787c901ebc04" />

## Windows PowerShell setup

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` and set one or more provider API keys. The default order is Gemini, then Groq, then Mistral; providers without keys are skipped. Set `LLM_PROVIDER` to choose the primary provider and `LLM_FALLBACK_PROVIDERS` to change the fallback order. Models can be overridden with `GEMINI_MODEL`, `GROQ_MODEL`, and `MISTRAL_MODEL`. Keep real keys in the untracked local `.env` file; `.env.example` contains placeholders only. Failover reduces outages but cannot help if all configured providers are unavailable or out of quota. `DATABASE_URL` is optional for local development; without it, graph records use `data/graphsheet.db`. For production graph storage, set it to a PostgreSQL URL such as `postgresql://user:password@host:5432/graphsheet`; the PostgreSQL driver is included in `requirements.txt`. To use Neo4j for the dataset knowledge graph, set `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD` (and optionally `NEO4J_DATABASE`). Neo4j credentials are required together; Aura requires an encrypted `neo4j+s://` or `bolt+s://` URI. On the first graph build after configuration, existing datasets are rebuilt from their SQLite source records. ChromaDB stores local evidence vectors under `CHROMA_PERSIST_DIRECTORY` (default `data/chroma`). `GEMINI_MODEL` defaults to `gemini-2.5-flash` and can be changed if your account supports another model. At least one configured LLM provider key is needed for RAG answers and Text-to-SQL; upload, preview, profiling, graph, and relationship APIs do not require an LLM key. Spreadsheet uploads accept `.csv`, legacy Excel `.xls`, and `.xlsx` files.

The `.venv` directory is ignored by Git. If it does not exist, create it with `py -m venv .venv` before installing dependencies. Dependency versions are listed in `requirements.txt`.

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --reload-include "*.py" --reload-include ".env" --port 8001
```

- Web interface: <http://127.0.0.1:8001/app/>
- API documentation: <http://127.0.0.1:8001/docs>
- Liveness check: <http://127.0.0.1:8001/health>
- Neo4j readiness check: <http://127.0.0.1:8001/ready/neo4j>

`/health` reports whether the API process is running and does not probe Neo4j.
Use `/ready/neo4j` to check the configured Neo4j connection separately; it
returns a structured 503 diagnostic when Neo4j is not configured or cannot be
reached.

Dataset and graph tables are initialized lazily. Existing conversation graph tables are renamed safely on first database initialization so the persistent dataset graph can use `graph_nodes` and `graph_edges`; existing conversation graph records are preserved.

## API examples

Upload a spreadsheet:

```powershell
curl.exe -F "file=@C:\path\to\sales.csv" http://127.0.0.1:8001/documents/upload
```

List datasets and retrieve a bounded preview:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/datasets/
Invoke-RestMethod "http://127.0.0.1:8001/datasets/<dataset-id>/preview?limit=10"
```

Create a chat, select up to five sheets, and ask a grounded question:

```powershell
$body = @{ query = "Which products have the highest sales?" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8001/datasets/<dataset-id>/ask" `
  -ContentType "application/json" -Body $body
```

Update the selected sheets and run RAG or SQL within that chat:

```powershell
$chat = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8001/conversations/" `
  -ContentType "application/json" `
  -Body (@{ title = "Sales analysis" } | ConvertTo-Json)
$chatId = $chat.conversation.conversation_id

$selection = @{ dataset_ids = @("<dataset-id-1>", "<dataset-id-2>") } | ConvertTo-Json
Invoke-RestMethod -Method Patch `
  -Uri "http://127.0.0.1:8001/conversations/$chatId/datasets" `
  -ContentType "application/json" -Body $selection

$body = @{ query = "Compare sales across both sheets" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8001/conversations/$chatId/ask" `
  -ContentType "application/json" -Body $body

$body = @{ question = "What is the total revenue?" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8001/conversations/$chatId/sql" `
  -ContentType "application/json" -Body $body

$body = @{
  question = "Show totals by category and separately by city and category"
} | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8001/conversations/$chatId/sql-analysis" `
  -ContentType "application/json" -Body $body
```

Other endpoints include `GET /datasets/{dataset_id}/schema`, `GET /datasets/{dataset_id}/search`, `GET /graph/`, and `GET /relationships/`.

Conversation history and its persisted graph can be accessed with
`GET /conversations/`, `GET /conversations/{conversation_id}`, and
`GET /conversations/{conversation_id}/graph`. Chat RAG uses
`POST /conversations/{conversation_id}/ask`; chat-scoped SQL uses
`POST /conversations/{conversation_id}/sql` for a single SQL result or
`POST /conversations/{conversation_id}/sql-analysis` for multiple independently
generated result tables in one turn. Update the selected sheets with
`PATCH /conversations/{conversation_id}/datasets` and a
`{"dataset_ids": ["<dataset-id>"]}` JSON body. Both endpoints use recent
same-chat history and graph context; RAG uses evidence from selected sheets,
and SQL execution rejects any table outside the selection. Candidate joins
remain heuristic and are not asserted as verified foreign keys. Inspect join
options with `GET /conversations/{conversation_id}/sql-relationships` and
approve a safe candidate using
`POST /conversations/{conversation_id}/sql-relationships/{relationship_id}/approval`.

Delete one uploaded dataset and its associated backend data:

```powershell
Invoke-RestMethod -Method Delete `
  -Uri "http://127.0.0.1:8001/datasets/<dataset-id>"
```

## Security notes

- Upload parsing checks supported extensions, file size, CSV/XLSX structure, headers, and spreadsheet row/column limits. XLSX macro-enabled content is rejected and formulas are read as cached values rather than executed.
- SQL uses SQLite read-only URI mode, a restrictive authorizer, dataset table registration checks, a five-second execution limit, a 500-row maximum, and a response-size bound. Dataset-scoped queries cannot read the registry or other tables.
- Keep `.env`, database files, and uploaded data private. LLM requests transmit the selected question and, depending on the workflow, retrieved evidence or selected dataset schema to the provider handling that request.
- The application does not provide user accounts, access control, or protection against a malicious local process with access to the SQLite file.

## Tests

Run the backend suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Automated tests mock LLM provider integrations and do not require paid API calls.
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
