# Neo4j Connectivity and Graph Fix Report

## 1. Root cause

The reported `BoltSecurityError` / `ConnectionResetError` occurred while the
driver was negotiating TLS with the Neo4j endpoint. The failure happened before
authentication or Cypher execution; the routing error was the driver's
consequence of being unable to establish that initial connection. The error
does not identify a unique infrastructure cause such as an Aura IP allowlist,
firewall, or transient service issue.

The configured secure connection was later verified successfully from this
environment. Therefore, the original TLS reset is not currently reproducible,
and its specific external cause remains unconfirmed.

## 2. Deployment type

The configured hostname uses Neo4j Aura's `*.databases.neo4j.io` pattern. The
deployment is Aura, not a local Neo4j instance. The configured database value
is used for driver sessions; credentials and the hostname are intentionally
omitted here.

## 3. Connection configuration diagnosis

- The configured driver scheme is `neo4j+s`, which enables TLS and routing for
  Aura. The application does not replace the hostname with a resolved IP or
  disable TLS.
- The Neo4j username and password are read from local environment
  configuration and are never included in diagnostics or API errors.
- The configured database is passed to each Neo4j session.
- The successful live connectivity check confirms that the current URI,
  credentials, TLS negotiation, and database session configuration work from
  this environment.

## 4. Files changed

Files changed for this Neo4j fix:

- `README.md`
- `backend/app/api/conversations.py`
- `backend/app/api/datasets.py`
- `backend/app/api/graph.py`
- `backend/app/api/neo4j_errors.py`
- `backend/app/main.py`
- `backend/app/services/conversation_store.py`
- `backend/app/services/dataset_store.py`
- `backend/app/services/graph_service.py`
- `backend/app/services/neo4j_store.py`
- `backend/tests/conftest.py`
- `backend/tests/test_conversation_store.py`
- `backend/tests/test_dataset_store.py`
- `backend/tests/test_neo4j_api.py`
- `backend/tests/test_neo4j_store.py`

The working tree also contains unrelated pre-existing changes; they were not
reverted as part of this fix.

## 5. Fixes implemented

- Added one process-scoped Neo4j driver, closed during FastAPI shutdown.
- Added safe URI/configuration validation and credential-free diagnostics for
  TLS, routing, authentication, and unavailable-server errors.
- Added bounded connection timeouts, a short cooldown after connection
  failures, and cached connectivity verification to suppress repeated polling
  attempts during outages.
- Kept `/health` as an API liveness check and added `/ready/neo4j` for an
  independent, real Neo4j readiness check.
- Added structured 503 responses with stable error codes and `Retry-After`
  where applicable for Neo4j-dependent graph and deletion operations.
- Scoped Neo4j reads, writes, and deletions by scope ID. Synchronization uses
  idempotent merges and removes old edge links before reconnecting an updated
  edge, avoiding duplicate or stale relationships on repeated synchronization.
- Dataset deletion performs its Neo4j updates before SQLite deletion. A Neo4j
  failure therefore leaves the SQLite dataset and uploads untouched. The
  dataset-schema synchronization occurs after dependent graph scopes are
  cleared.
- SQLite and Neo4j cannot participate in one atomic transaction. If SQLite
  deletion fails after the Neo4j update, SQLite remains the source of truth;
  retrying the deletion rebuilds the graph from current SQLite state before
  retrying the SQLite transaction. There is no background reconciliation
  worker.

## 6. Unit and integration test results

The complete backend test suite was run:

```text
176 passed, 1 warning
```

The warning is an existing Starlette deprecation warning for
`HTTP_422_UNPROCESSABLE_ENTITY`. The tests use fake Neo4j drivers and do not
contact the configured database. Coverage includes connection validation and
diagnostics, TLS/unavailable failures, shared-driver lifecycle, synchronization
and repeat synchronization, graph retrieval and deletion, graph scope
isolation, structured API errors, and preservation of SQLite data when Neo4j
operations fail.

Python compilation and `git diff --check` also passed.

## 7. Live connectivity result

**Succeeded.** A live `verify_connectivity()` check succeeded using the
configured Aura connection. No credentials or hostname were printed.

## 8. Graph synchronization and retrieval results

- **Live graph retrieval: succeeded.** A read of the `dataset-schema` scope
  completed successfully and returned 0 nodes and 0 edges.
- **Live graph synchronization: succeeded.** A uniquely named temporary scope
  was written with 2 nodes and 1 relationship, read back and verified, then
  deleted successfully. No dataset, upload, or existing graph scope was
  deleted or overwritten.
- Automated graph synchronization, retrieval, deletion, and idempotence tests
  also passed using mocked Neo4j sessions.

## 9. Remaining limitations

- The original TLS reset's exact infrastructure cause cannot be established
  now that the live connection succeeds. If it recurs, check Aura instance
  status and network/firewall or allowlist behavior without changing the secure
  URI or bypassing TLS.
- The cross-database dataset deletion is not atomic. SQLite is preserved when
  Neo4j fails; if SQLite itself fails after a successful Neo4j update, retry
  the deletion to reconcile the graph from SQLite. No background retry worker
  is implemented.
- The automated suite is mocked; live behavior was verified separately in
  this environment and may differ on another host or network.
