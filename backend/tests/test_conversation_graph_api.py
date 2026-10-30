from fastapi.testclient import TestClient

from backend.app.api import conversations as conversations_api
from backend.app.main import app
from backend.app.services.conversation_store import (
    create_conversation,
    get_conversation_graph,
    record_message,
)
from backend.app.services.graph_service import build_graph_from_database
from backend.app.services.dataset_store import store_spreadsheet
from backend.app.services import answer_service
from backend.app.services import neo4j_store
from backend.app.services import conversation_store


client = TestClient(app)


def test_conversation_graph_endpoint_returns_persisted_chat_history(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "conversation.db"
    conversation = create_conversation(
        title="Persisted chat",
        database_path=database_path,
    )
    record_message(
        conversation["conversation_id"],
        actor="user",
        message="Show the data graph.",
        database_path=database_path,
    )
    monkeypatch.setattr(
        conversations_api,
        "get_conversation_graph",
        lambda conversation_id: get_conversation_graph(
            conversation_id,
            database_path,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda conversation_id: {
            "conversation_id": conversation_id,
            "selected_dataset_ids": [],
            "events": [],
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "build_graph_for_scope",
        lambda *_args, **_kwargs: {"nodes": [], "edges": []},
    )

    response = client.get(
        f"/conversations/{conversation['conversation_id']}/graph"
    )

    assert response.status_code == 200
    graph = response.json()["graph"]
    assert any(node["type"] == "Message" for node in graph["nodes"])
    assert any(edge["type"] == "HAS_MESSAGE" for edge in graph["edges"])


def test_conversation_dataset_endpoint_rejects_more_than_five(
    monkeypatch,
):
    def reject_selection(*_args):
        raise ValueError("A chat can use at most five datasets.")

    monkeypatch.setattr(
        conversations_api,
        "set_conversation_datasets",
        reject_selection,
    )

    response = client.patch(
        f"/conversations/{'a' * 32}/datasets",
        json={"dataset_ids": [str(index) for index in range(6)]},
    )

    assert response.status_code == 422
    assert "at most five" in response.json()["detail"]


def test_chat_question_uses_selected_sheets_graph_and_same_chat_history(
    monkeypatch,
):
    dataset_ids = ["a" * 32, "b" * 32]
    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda _conversation_id: {
            "selected_dataset_ids": dataset_ids,
            "events": [
                {
                    "event_type": "message",
                    "actor": "user",
                    "message": "Earlier in this chat",
                }
            ],
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda ids: ids,
    )
    monkeypatch.setattr(
        conversations_api,
        "get_dataset_details",
        lambda dataset_id: {"dataset_id": dataset_id},
    )
    monkeypatch.setattr(
        conversations_api,
        "_selected_graph_context",
        lambda _conversation_id, ids, **_kwargs: {
            "candidate_relationships": [{"status": "candidate"}],
            "selected_ids": ids,
        },
    )
    recorded = []

    def fake_record_message(conversation_id, *, actor, message, metadata=None):
        event = {
            "event_id": f"event-{len(recorded)}",
            "actor": actor,
            "message": message,
            "metadata": metadata,
        }
        recorded.append(event)
        return event

    monkeypatch.setattr(
        conversations_api,
        "record_message",
        fake_record_message,
    )
    captured = {}

    def fake_answer_question(**kwargs):
        captured.update(kwargs)
        return {
            "status": "success",
            "answer": "Grounded answer",
            "sources": [{"chunk_id": "source-b", "dataset_id": dataset_ids[1]}],
        }

    monkeypatch.setattr(
        conversations_api,
        "answer_question",
        fake_answer_question,
    )

    response = client.post(
        "/conversations/chat-a/ask",
        json={"query": "Compare both"},
    )

    assert response.status_code == 200
    assert captured["dataset_ids"] == dataset_ids
    assert captured["conversation_history"] == [
        {"role": "user", "content": "Earlier in this chat"}
    ]
    assert captured["graph_context"]["selected_ids"] == dataset_ids
    assert [event["actor"] for event in recorded] == ["user", "assistant"]
    assert response.json()["sources"][0]["dataset_id"] == dataset_ids[1]


def test_chat_rag_reads_selected_evidence_from_the_graph(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "sales.csv"
    source.write_text("Sales\n12\n", encoding="utf-8")
    database = tmp_path / "graph.db"
    stored = store_spreadsheet(
        source,
        "sales.csv",
        database,
        chunks=[
            {
                "chunk_id": "sales-chunk",
                "text": "Sales: 12",
                "metadata": {"source_rows": [1]},
            }
        ],
    )
    dataset_id = stored["dataset_id"]
    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda _conversation_id: {
            "selected_dataset_ids": [dataset_id],
            "events": [],
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda ids: ids,
    )
    monkeypatch.setattr(
        conversations_api,
        "get_dataset_details",
        lambda _dataset_id: {"dataset_id": dataset_id},
    )
    monkeypatch.setattr(
        conversations_api,
        "get_conversation_graph",
        lambda _conversation_id: {
            "nodes": [{"id": "chat:test", "type": "Chat", "label": "Chat"}],
            "edges": [],
        },
    )

    def build_selected_scope(scope_id, dataset_ids, **kwargs):
        return {
            **build_graph_from_database(
                database,
                dataset_ids=dataset_ids,
                **kwargs,
            ),
            "scope_id": scope_id,
        }

    monkeypatch.setattr(
        conversations_api,
        "build_graph_for_scope",
        build_selected_scope,
    )
    recorded = []

    def fake_record_message(_conversation_id, *, actor, message, metadata=None):
        event = {
            "event_id": f"event-{len(recorded)}",
            "actor": actor,
            "message": message,
            "metadata": metadata or {},
        }
        recorded.append(event)
        return event

    monkeypatch.setattr(
        conversations_api,
        "record_message",
        fake_record_message,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class FakeModels:
        def generate_content(self, **kwargs):
            assert "Sales: 12" in kwargs["contents"]
            assert "Evidence chunk 1" not in kwargs["contents"]
            return type(
                "FakeResponse",
                (),
                {"text": "Sales is 12."},
            )()

    monkeypatch.setattr(
        answer_service.genai,
        "Client",
        lambda **_kwargs: type("FakeClient", (), {"models": FakeModels()})(),
    )

    response = client.post(
        "/conversations/chat-rag/ask",
        json={"query": "What is sales?"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "Sales is 12."
    assert response.json()["sources"][0]["chunk_id"] == "sales-chunk"
    assert response.json()["graph_retrieval"]["matched_columns"][0][
        "sql_name"
    ] == "sales"
    assert [event["actor"] for event in recorded] == ["user", "assistant"]


def test_chat_answer_is_not_blocked_by_neo4j_outage(
    tmp_path,
    monkeypatch,
    caplog,
):
    database = tmp_path / "chat.db"
    source = tmp_path / "sales.csv"
    source.write_text("Sales\n12\n", encoding="utf-8")
    stored = store_spreadsheet(
        source,
        "sales.csv",
        database,
        chunks=[
            {
                "chunk_id": "sales-chunk",
                "text": "Sales: 12",
                "metadata": {"source_rows": [1]},
            }
        ],
    )
    dataset_id = stored["dataset_id"]
    monkeypatch.setattr(neo4j_store, "is_configured", lambda: True)
    monkeypatch.setattr(
        neo4j_store,
        "sync_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            neo4j_store.Neo4jStoreError(
                "Neo4j unavailable.",
                code="NEO4J_UNAVAILABLE",
            )
        ),
    )
    conversation = conversation_store.create_conversation(
        title="Sales",
        dataset_id=dataset_id,
        database_path=database,
    )
    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda conversation_id: conversation_store.get_conversation(
            conversation_id,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_conversation_graph",
        lambda conversation_id: conversation_store.get_conversation_graph(
            conversation_id,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda ids: conversation_store.validate_dataset_ids(
            ids,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_dataset_details",
        lambda requested_id: {"dataset_id": requested_id},
    )
    monkeypatch.setattr(
        conversations_api,
        "build_graph_for_scope",
        lambda scope_id, dataset_ids, **kwargs: {
            **build_graph_from_database(
                database,
                dataset_ids=dataset_ids,
                **kwargs,
            ),
            "scope_id": scope_id,
            "graph_store": "sqlite",
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "record_message",
        lambda conversation_id, **kwargs: conversation_store.record_message(
            conversation_id,
            database_path=database,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "answer_question",
        lambda **_kwargs: {
            "status": "success",
            "answer": "Sales is 12.",
            "sources": [{"chunk_id": "sales-chunk", "dataset_id": dataset_id}],
            "graph_retrieval": {},
        },
    )

    with caplog.at_level("WARNING"):
        response = client.post(
            f"/conversations/{conversation['conversation_id']}/ask",
            json={"query": "What is sales?"},
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "Sales is 12."
    assert response.json()["graph_store"] == "sqlite"
    assert "SQLite remains authoritative" in caplog.text


def test_chat_sql_persists_query_execution_and_result_events(monkeypatch):
    dataset_ids = ["c" * 32, "d" * 32]
    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda _conversation_id: {
            "selected_dataset_ids": dataset_ids,
            "events": [],
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda ids: ids,
    )
    monkeypatch.setattr(
        conversations_api,
        "get_dataset_details",
        lambda dataset_id: {"dataset_id": dataset_id},
    )
    monkeypatch.setattr(
        conversations_api,
        "_selected_graph_context",
        lambda _conversation_id, _ids, **_kwargs: {
            "nodes": [],
            "edges": [],
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "record_message",
        lambda *_args, **_kwargs: {"event_id": "question-event"},
    )
    captured = {}

    def fake_ask_with_sql(question, *, dataset_ids, graph_context):
        captured["question"] = question
        captured["dataset_ids"] = dataset_ids
        captured["graph_context"] = graph_context
        return {
            "status": "success",
            "dataset_id": dataset_ids[0],
            "dataset_ids": dataset_ids,
            "question": question,
            "sql": "SELECT SUM(value) FROM selected",
            "result": {
                "status": "success",
                "columns": ["total"],
                "rows": [{"total": 42}],
                "row_count": 1,
                "read_columns": ["value"],
                "read_column_refs": [
                    {"table": "dataset_c", "column": "value"}
                ],
            },
        }

    monkeypatch.setattr(
        conversations_api,
        "ask_with_sql",
        fake_ask_with_sql,
    )
    monkeypatch.setattr(
        conversations_api,
        "record_sql",
        lambda *_args, **_kwargs: {"event_id": "query-event"},
    )
    monkeypatch.setattr(
        conversations_api,
        "record_execution",
        lambda *_args, **_kwargs: {"event_id": "execution-event"},
    )
    monkeypatch.setattr(
        conversations_api,
        "record_result_reference",
        lambda *_args, **_kwargs: {"event_id": "result-event"},
    )

    response = client.post(
        "/conversations/chat-b/sql",
        json={"question": "Sum value"},
    )

    assert response.status_code == 200
    assert captured["dataset_ids"] == dataset_ids
    assert captured["graph_context"] == {"nodes": [], "edges": []}
    assert response.json()["events"] == {
        "query": "query-event",
        "execution": "execution-event",
        "result": "result-event",
    }


def test_chat_graph_context_combines_selected_sheet_graph_and_chat_history(
    monkeypatch,
):
    selected_id = "a" * 32
    unselected_id = "b" * 32
    monkeypatch.setattr(
        conversations_api,
        "get_conversation_graph",
        lambda _conversation_id: {
            "nodes": [
                {"id": "chat:1", "type": "Chat"},
                {
                    "id": "dataset:selected",
                    "type": "Dataset",
                    "dataset_id": selected_id,
                },
                {
                    "id": "column:selected",
                    "type": "Column",
                    "dataset_id": selected_id,
                    "sql_name": "student_id",
                },
                {
                    "id": "dataset:other",
                    "type": "Dataset",
                    "dataset_id": unselected_id,
                },
                {"id": "message:1", "type": "Message"},
            ],
            "edges": [
                {
                    "id": "uses:selected",
                    "source": "chat:1",
                    "target": "dataset:selected",
                    "type": "USES_DATASET",
                },
                {
                    "id": "contains:selected",
                    "source": "dataset:selected",
                    "target": "column:selected",
                    "type": "HAS_COLUMN",
                },
                {
                    "id": "uses:other",
                    "source": "chat:1",
                    "target": "dataset:other",
                    "type": "USES_DATASET",
                },
                {
                    "id": "message-edge",
                    "source": "chat:1",
                    "target": "message:1",
                    "type": "HAS_MESSAGE",
                },
            ],
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "build_graph_for_scope",
        lambda scope_id, dataset_ids, **kwargs: {
            "scope_id": scope_id,
            "nodes": [
                {
                    "id": f"dataset:{selected_id}",
                    "type": "Dataset",
                    "dataset_id": selected_id,
                    "table_name": f"dataset_{selected_id}",
                },
                {
                    "id": f"column:{selected_id}:student_id",
                    "type": "Column",
                    "dataset_id": selected_id,
                    "sql_name": "student_id",
                    "label": "Student ID",
                },
            ],
            "edges": [
                {
                    "id": "schema-column",
                    "source": f"dataset:{selected_id}",
                    "target": f"column:{selected_id}:student_id",
                    "type": "HAS_COLUMN",
                }
            ],
        },
    )

    context = conversations_api._selected_graph_context(
        "chat-1",
        [selected_id],
        include_history=True,
    )

    assert {node["id"] for node in context["nodes"]} == {
        "chat:1",
        f"dataset:{selected_id}",
        f"column:{selected_id}:student_id",
        "message:1",
    }
    assert {edge["id"] for edge in context["edges"]} == {
        "uses:selected",
        "contains:selected",
        "schema-column",
        "message-edge",
    }
