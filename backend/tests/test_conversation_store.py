import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.services import conversation_store
from backend.app.services.conversation_store import (
    ConversationStoreError,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_graph,
    record_choice,
    record_clarification,
    record_execution,
    record_message,
    record_result_reference,
    record_sql,
    set_conversation_datasets,
)
from backend.app.services.dataset_store import store_spreadsheet
from backend.app.services import neo4j_store


def test_conversation_records_message_clarification_choice_sql_and_execution(tmp_path):
    database_path = tmp_path / "conversation.db"

    conversation = create_conversation(
        title="Customer revenue question",
        dataset_id="dataset_abc",
        database_path=database_path,
    )

    assistant_message = record_message(
        conversation["conversation_id"],
        actor="user",
        message="Which customers had the highest revenue?",
        database_path=database_path,
    )

    record_clarification(
        conversation["conversation_id"],
        actor="assistant",
        message="Should I rank by total revenue or average revenue?",
        options=["total revenue", "average revenue"],
        database_path=database_path,
    )

    record_choice(
        conversation["conversation_id"],
        actor="user",
        message="Use total revenue.",
        selected_option="total revenue",
        database_path=database_path,
    )

    record_sql(
        conversation["conversation_id"],
        actor="assistant",
        sql='SELECT customer_id, SUM(amount) AS total_revenue FROM "dataset_abc" GROUP BY customer_id ORDER BY total_revenue DESC LIMIT 10',
        metadata={"dataset_id": "dataset_abc"},
        database_path=database_path,
    )

    record_execution(
        conversation["conversation_id"],
        actor="system",
        status="success",
        result_reference="query_result_01",
        database_path=database_path,
    )

    record_result_reference(
        conversation["conversation_id"],
        actor="assistant",
        result_reference="query_result_01",
        database_path=database_path,
    )

    stored = get_conversation(conversation["conversation_id"], database_path=database_path)

    assert stored["title"] == "Customer revenue question"
    assert stored["dataset_id"] == "dataset_abc"
    assert [event["event_type"] for event in stored["events"]] == [
        "message",
        "clarification",
        "user_choice",
        "sql",
        "execution",
        "result_reference",
    ]

    assert stored["events"][0]["message"] == "Which customers had the highest revenue?"
    assert stored["events"][1]["metadata"]["options"] == ["total revenue", "average revenue"]
    assert stored["events"][2]["metadata"]["selected_option"] == "total revenue"
    assert stored["events"][3]["metadata"]["sql"].startswith("SELECT customer_id")
    assert stored["events"][4]["metadata"]["status"] == "success"
    assert stored["events"][4]["result_reference"] == "query_result_01"
    assert stored["events"][5]["result_reference"] == "query_result_01"


def test_create_conversation_uses_default_title_when_blank():
    conversation = create_conversation(title="   ", dataset_id="dataset_abc")
    assert conversation["title"] == "New conversation"


def test_record_event_rejects_missing_conversation():
    with pytest.raises(ConversationStoreError):
        record_message("missing-conversation", actor="user", message="hello")


def test_record_message_idempotency_prevents_duplicate_request_events(tmp_path):
    database_path = tmp_path / "conversation.db"
    conversation = create_conversation(database_path=database_path)
    conversation_id = conversation["conversation_id"]
    metadata = {"request_id": "request-1", "dataset_ids": ["sheet-1"]}

    first = record_message(
        conversation_id,
        actor="user",
        message="What is the total?",
        metadata=metadata,
        idempotency_key="ask:request-1:question",
        database_path=database_path,
    )
    retry = record_message(
        conversation_id,
        actor="user",
        message="What is the total?",
        metadata=metadata,
        idempotency_key="ask:request-1:question",
        database_path=database_path,
    )

    assert retry["event_id"] == first["event_id"]
    assert len(get_conversation(conversation_id, database_path)["events"]) == 1
    with pytest.raises(ConversationStoreError, match="different event"):
        record_message(
            conversation_id,
            actor="user",
            message="A different question",
            metadata=metadata,
            idempotency_key="ask:request-1:question",
            database_path=database_path,
        )


def test_concurrent_message_writes_keep_unique_contiguous_event_order(tmp_path):
    database_path = tmp_path / "conversation.db"
    conversation = create_conversation(database_path=database_path)
    conversation_id = conversation["conversation_id"]
    count = 24

    def write_message(index):
        return record_message(
            conversation_id,
            actor="user",
            message=f"Question {index}",
            idempotency_key=f"request-{index}",
            database_path=database_path,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(executor.map(write_message, range(count)))

    stored = get_conversation(conversation_id, database_path)
    orders = [event["event_order"] for event in stored["events"]]
    assert len({event["event_id"] for event in events}) == count
    assert orders == list(range(1, count + 1))
    connection = sqlite3.connect(database_path)
    try:
        journal_mode = connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]
    finally:
        connection.close()
    assert journal_mode.lower() == "wal"


def test_locked_message_write_recovers_after_lock_is_released(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "conversation.db"
    conversation = create_conversation(database_path=database_path)
    blocker = sqlite3.connect(database_path, timeout=0.01)
    blocker.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(conversation_store, "SQLITE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(conversation_store, "SQLITE_BUSY_TIMEOUT_MS", 10)
    monkeypatch.setattr(
        conversation_store.time,
        "sleep",
        lambda _delay: blocker.rollback(),
    )

    event = record_message(
        conversation["conversation_id"],
        actor="user",
        message="Retry after lock recovery.",
        database_path=database_path,
    )
    blocker.close()

    stored = get_conversation(
        conversation["conversation_id"],
        database_path,
    )
    assert stored["events"][0]["event_id"] == event["event_id"]


def test_locked_message_write_returns_clear_retryable_error(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "conversation.db"
    conversation = create_conversation(database_path=database_path)
    blocker = sqlite3.connect(database_path, timeout=0.01)
    blocker.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(conversation_store, "SQLITE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(conversation_store, "SQLITE_BUSY_TIMEOUT_MS", 10)
    monkeypatch.setattr(conversation_store.time, "sleep", lambda _delay: None)

    try:
        with pytest.raises(ConversationStoreError) as error:
            record_message(
                conversation["conversation_id"],
                actor="user",
                message="This write should report lock contention.",
                database_path=database_path,
            )
    finally:
        blocker.rollback()
        blocker.close()

    assert error.value.code == "SQLITE_BUSY"
    assert error.value.retry_after_seconds == 2
    assert "retry shortly" in str(error.value)


def test_conversation_events_are_persisted_as_an_isolated_graph(tmp_path):
    database_path = tmp_path / "conversation.db"
    first = create_conversation(title="First chat", database_path=database_path)
    second = create_conversation(title="Second chat", database_path=database_path)

    user_message = record_message(
        first["conversation_id"],
        actor="user",
        message="Find the highest sales.",
        metadata={"dataset_id": "sales-dataset"},
        database_path=database_path,
    )
    assistant_message = record_message(
        first["conversation_id"],
        actor="assistant",
        message="Sales were highest in May.",
        metadata={
            "dataset_id": "sales-dataset",
            "sources": [
                {
                    "chunk_id": "sales-chunk",
                    "chunk_index": 0,
                    "score": 2,
                }
            ],
        },
        database_path=database_path,
    )
    query = record_sql(
        first["conversation_id"],
        actor="assistant",
        sql="SELECT SUM(sales) FROM data",
        metadata={"question": "Find the highest sales."},
        database_path=database_path,
    )
    record_execution(
        first["conversation_id"],
        actor="system",
        status="success",
        metadata={"query_id": query["event_id"]},
        database_path=database_path,
    )
    record_result_reference(
        first["conversation_id"],
        actor="assistant",
        result_reference="result-1",
        metadata={
            "query_id": query["event_id"],
            "row_count": 1,
            "rows": [{"sales": 25}],
        },
        database_path=database_path,
    )

    graph = get_conversation_graph(
        first["conversation_id"],
        database_path=database_path,
    )
    node_ids = {node["id"] for node in graph["nodes"]}
    assert {node["type"] for node in graph["nodes"]} >= {
        "Chat",
        "Message",
        "SQLQuery",
        "Execution",
        "QueryResult",
        "Evidence",
    }
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in graph["edges"]
    )
    assert any(
        edge["type"] == "FOLLOWS"
        and edge["source"] == f"event:{user_message['event_id']}"
        and edge["target"] == f"event:{assistant_message['event_id']}"
        for edge in graph["edges"]
    )
    assert any(edge["type"] == "ASKED_ABOUT" for edge in graph["edges"])
    assert any(edge["type"] == "SUPPORTED_BY" for edge in graph["edges"])
    assert any(
        edge["type"] == "PRODUCED_RESULT"
        and edge["source"] == f"event:{query['event_id']}"
        for edge in graph["edges"]
    )
    assert all(
        node["conversation_id"] == first["conversation_id"]
        for node in graph["nodes"]
    )
    assert not any(
        node["id"] == f"chat:{second['conversation_id']}"
        for node in graph["nodes"]
    )


def test_conversation_dataset_selection_is_limited_and_persisted(tmp_path):
    database_path = tmp_path / "datasets.db"
    dataset_ids = []
    for index in range(6):
        source = tmp_path / f"sheet-{index}.csv"
        source.write_text(f"value\n{index}\n", encoding="utf-8")
        dataset = store_spreadsheet(
            source,
            source.name,
            database_path,
        )
        dataset_ids.append(dataset["dataset_id"])

    conversation = create_conversation(database_path=database_path)
    selected = set_conversation_datasets(
        conversation["conversation_id"],
        dataset_ids[:5],
        database_path=database_path,
    )
    assert selected["selected_dataset_ids"] == dataset_ids[:5]
    assert get_conversation(
        conversation["conversation_id"],
        database_path=database_path,
    )["selected_dataset_ids"] == dataset_ids[:5]
    graph = get_conversation_graph(
        conversation["conversation_id"],
        database_path=database_path,
    )
    assert len(
        [edge for edge in graph["edges"] if edge["type"] == "USES_DATASET"]
    ) == 5
    assert len(
        [node for node in graph["nodes"] if node["type"] == "Column"]
    ) == 5

    with pytest.raises(ValueError, match="at most five"):
        set_conversation_datasets(
            conversation["conversation_id"],
            dataset_ids,
            database_path=database_path,
        )

    with pytest.raises(ConversationStoreError, match="do not exist"):
        set_conversation_datasets(
            conversation["conversation_id"],
            ["f" * 32],
            database_path=database_path,
        )


def test_delete_conversation_removes_its_history_graph_only(tmp_path):
    database_path = tmp_path / "conversation.db"
    first = create_conversation(database_path=database_path)
    second = create_conversation(database_path=database_path)
    record_message(
        first["conversation_id"],
        actor="user",
        message="Delete only this history.",
        database_path=database_path,
    )

    assert delete_conversation(
        first["conversation_id"],
        database_path=database_path,
    )
    assert not delete_conversation(
        first["conversation_id"],
        database_path=database_path,
    )
    assert get_conversation(
        second["conversation_id"],
        database_path=database_path,
    )["events"] == []


def test_neo4j_failure_preserves_conversation_in_sqlite(tmp_path, monkeypatch):
    database_path = tmp_path / "conversation.db"
    conversation = create_conversation(
        title="Keep on failed graph deletion",
        database_path=database_path,
    )
    failure = neo4j_store.Neo4jStoreError(
        "Neo4j unavailable.",
        code="NEO4J_UNAVAILABLE",
    )
    monkeypatch.setattr(
        neo4j_store,
        "delete_graphs",
        lambda _scopes: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(ConversationStoreError):
        delete_conversation(
            conversation["conversation_id"],
            database_path,
        )

    assert get_conversation(
        conversation["conversation_id"],
        database_path,
    )["conversation_id"] == conversation["conversation_id"]


def test_conversation_graph_falls_back_to_sqlite_when_neo4j_fails(
    tmp_path,
    monkeypatch,
    caplog,
):
    database_path = tmp_path / "conversation.db"
    conversation = create_conversation(database_path=database_path)
    record_message(
        conversation["conversation_id"],
        actor="user",
        message="Keep this graph available.",
        database_path=database_path,
    )
    monkeypatch.setattr(neo4j_store, "is_configured", lambda: True)
    monkeypatch.setattr(
        neo4j_store,
        "sync_graph",
        lambda *_args: (_ for _ in ()).throw(
            neo4j_store.Neo4jStoreError(
                "Neo4j unavailable.",
                code="NEO4J_UNAVAILABLE",
            )
        ),
    )

    with caplog.at_level("WARNING"):
        graph = get_conversation_graph(
            conversation["conversation_id"],
            database_path,
        )

    assert any(node["type"] == "Message" for node in graph["nodes"])
    assert graph["graph_store"] == "sqlite"
    assert "serving SQLite graph" in caplog.text
