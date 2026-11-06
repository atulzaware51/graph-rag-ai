# GraphSheet AI API Test Results

**Test method:** FastAPI `TestClient` tests plus an isolated local SQLite
workflow. These are not a live deployed-server test. Routes that mock service
or provider behavior are identified below.

| Endpoint | Method / request | Expected | Actual | Status |
|---|---|---|---|---|
| `/health` | `GET` | HTTP 200; `status=healthy` | HTTP 200; asserted healthy response | PASS |
| `/documents/upload` | `POST`, valid 2-row CSV multipart upload | HTTP 200; preview and persisted dataset metadata | HTTP 200; 2 rows, 3 columns, preview and dataset table metadata asserted using a temporary test DB | PASS |
| `/documents/upload` | `POST`, `.txt` upload | HTTP 400 | HTTP 400 | PASS |
| `/documents/upload` | `POST`, empty `.csv` | HTTP 400 | HTTP 400 | PASS |
| `/datasets/{id}/preview?limit=5` | `GET` | HTTP 200; bounded rows | HTTP 200 and rows asserted with a mocked reader | PASS (mocked) |
| `/datasets/{id}/preview?limit=101` | `GET` | HTTP 422 | HTTP 422 | PASS |
| `/datasets/{id}/schema` | `GET` | HTTP 200 profile; unknown dataset HTTP 404 | HTTP 200/404 asserted with mocked profiler | PASS (mocked) |
| `/relationships/` | `GET` | HTTP 200 candidate response; service error HTTP 500 | HTTP 200/500 asserted with mocked inference | PASS (mocked) |
| `/graph/?limit=2` | `GET` | HTTP 200; returned edges reference returned nodes | HTTP 200 and endpoint consistency asserted with mocked graph | PASS (mocked) |
| `/graph/` | `GET` | HTTP 200 graph from configured store | Initial configured test run returned service failure after Neo4j connection could not be established; no live graph result | BLOCKED |
| `/conversations/{id}/graph` | `GET` | HTTP 200 persisted message nodes/edges | HTTP 200; SQLite history with mocked selected graph builder | PASS (mixed) |
| `/conversations/{id}/datasets` | `PATCH`, six dataset IDs | HTTP 422 | HTTP 422 asserted with mocked store validation | PASS (mocked) |
| `/conversations/{id}/ask` | `POST`, selected sheets and question | HTTP 200, answer with source metadata and same-chat history | HTTP 200; answer and graph services mocked | PASS (mocked) |
| `/datasets/{id}/ask-sql` | `POST`, valid question | HTTP 200 with SQL result | HTTP 200; workflow, graph, and result are mocked | PASS (mocked) |
| `/datasets/{id}/ask-sql` | `POST`, empty/missing question | HTTP 422 | HTTP 422 | PASS |
| `/datasets/{id}/ask-sql` | `POST`, unknown dataset / rejected SQL / missing provider | HTTP 404 / 502 / 500 | Expected status/detail asserted from mocked workflow | PASS (mocked) |
| `/app/`, `/app/app.js`, `/app/styles.css` | `GET` | HTTP 200 static assets | HTTP 200 and expected content asserted | PASS (static client) |

The isolated temporary two-sheet workflow was not a route-level upload/browser
test. It verified graph construction, a `customer_id` candidate, two retrieved
evidence chunks, selected-dataset isolation, a read-only JOIN producing totals
of 500, 2,000, and 2,500 (total 5,000), and rejection of four write statements.

No live HTTP server test was run. No genuine Gemini answer or generated SQL
response was recorded. No read-only Cypher query was run. React UI/browser
screenshots were not captured. No credentials, full environment files, or
production database contents are included here.
