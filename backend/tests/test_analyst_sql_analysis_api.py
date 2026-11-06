import json
import sqlite3

from fastapi.testclient import TestClient

from backend.app.api import conversations as conversations_api
from backend.app.main import app
from backend.app.services import conversation_store
from backend.app.services import sql_analysis_service
from backend.app.services.dataset_reader import get_dataset_details
from backend.app.services.dataset_store import store_spreadsheet


client = TestClient(app)


def test_analyst_sql_analysis_returns_multiple_tables_and_restores_history(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "analyst-sql.db"
    source = tmp_path / "sales.csv"
    source.write_text(
        "city,category,amount\n"
        "North,Books,10\n"
        "North,Toys,5\n"
        "South,Books,20\n",
        encoding="utf-8",
    )
    stored = store_spreadsheet(source, "sales.csv", database)
    conversation = conversation_store.create_conversation(
        title="Analyst SQL",
        selected_dataset_ids=[stored["dataset_id"]],
        database_path=database,
    )
    conversation_id = conversation["conversation_id"]
    table = stored["table_name"]

    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda requested_id: conversation_store.get_conversation(
            requested_id,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda dataset_ids: conversation_store.validate_dataset_ids(
            dataset_ids,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_dataset_details",
        lambda dataset_id: get_dataset_details(dataset_id, database),
    )
    monkeypatch.setattr(
        conversations_api,
        "record_message",
        lambda requested_id, **kwargs: conversation_store.record_message(
            requested_id,
            database_path=database,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_event_by_idempotency_key",
        lambda requested_id, key: conversation_store.get_event_by_idempotency_key(
            requested_id,
            key,
            database_path=database,
        ),
    )
    plan = {
        "analysis_title": "Sales breakdown",
        "queries": [
            {
                "title": "Totals by category",
                "description": "",
                "sql": (
                    f'SELECT "category", SUM("amount") AS "total_amount" '
                    f'FROM "{table}" GROUP BY "category" '
                    'ORDER BY "total_amount" DESC'
                ),
                "source_dataset_ids": [stored["dataset_id"]],
                "referenced_tables": [table],
                "referenced_columns": ["category", "amount"],
                "join_explanation": "",
            },
            {
                "title": "Totals by city and category",
                "description": "",
                "sql": (
                    f'SELECT "city", "category", '
                    f'SUM("amount") AS "total_amount" FROM "{table}" '
                    'GROUP BY "city", "category" '
                    'ORDER BY "city", "category"'
                ),
                "source_dataset_ids": [stored["dataset_id"]],
                "referenced_tables": [table],
                "referenced_columns": ["city", "category", "amount"],
                "join_explanation": "",
            },
        ],
    }
    monkeypatch.setattr(
        sql_analysis_service,
        "generate_text",
        lambda **_kwargs: (json.dumps(plan), "gemini"),
    )
    generation_calls = []

    def generate(prompt, dataset_ids, **kwargs):
        generation_calls.append(prompt)
        return sql_analysis_service.generate_analysis(
            prompt,
            dataset_ids,
            database_path=database,
            **kwargs,
        )

    monkeypatch.setattr(conversations_api, "generate_analysis", generate)

    request_payload = {
        "question": (
            "Show total sales by category and separately by city and category"
        ),
        "request_id": "analysis-request-1",
    }
    response = client.post(
        f"/conversations/{conversation_id}/sql-analysis",
        json=request_payload,
    )

    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis["status"] == "success"
    assert [query["title"] for query in analysis["queries"]] == [
        "Totals by category",
        "Totals by city and category",
    ]
    assert analysis["queries"][0]["result"]["rows"] == [
        {"category": "Books", "total_amount": 30},
        {"category": "Toys", "total_amount": 5},
    ]
    assert analysis["queries"][1]["result"]["rows"] == [
        {"city": "North", "category": "Books", "total_amount": 10},
        {"city": "North", "category": "Toys", "total_amount": 5},
        {"city": "South", "category": "Books", "total_amount": 20},
    ]
    replay = client.post(
        f"/conversations/{conversation_id}/sql-analysis",
        json=request_payload,
    )
    assert replay.status_code == 200
    assert len(generation_calls) == 1
    conflict = client.post(
        f"/conversations/{conversation_id}/sql-analysis",
        json={
            **request_payload,
            "question": "Use the same ID for a different question",
        },
    )
    assert conflict.status_code == 409

    restored = client.get(
        f"/conversations/{conversation_id}"
    ).json()["conversation"]
    assistant_message = next(
        event
        for event in restored["events"]
        if event["actor"] == "assistant"
        and event["metadata"].get("intent") == "sql_analysis"
    )
    assert len(assistant_message["metadata"]["sql_analysis"]["queries"]) == 2
    assert assistant_message["metadata"]["sql_analysis"]["queries"][1][
        "result"
    ]["rows"] == analysis["queries"][1]["result"]["rows"]


def test_analyst_code_generation_persists_unexecuted_ddl(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "analyst-codegen.db"
    source = tmp_path / "students.csv"
    source.write_text(
        "StudentID,FirstName\n1,Ada\n",
        encoding="utf-8",
    )
    stored = store_spreadsheet(source, "students.csv", database)
    conversation = conversation_store.create_conversation(
        title="DDL generation",
        selected_dataset_ids=[stored["dataset_id"]],
        database_path=database,
    )
    conversation_id = conversation["conversation_id"]
    ddl = (
        "CREATE TABLE Students (StudentID TEXT PRIMARY KEY, FirstName TEXT);"
    )
    plan = {
        "analysis_title": "Students table",
        "queries": [
            {
                "title": "Create Students",
                "description": "Create the requested table.",
                "sql": ddl,
                "source_dataset_ids": [stored["dataset_id"]],
                "referenced_tables": [],
                "referenced_columns": [],
                "join_explanation": "",
            }
        ],
    }

    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda requested_id: conversation_store.get_conversation(
            requested_id,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda dataset_ids: conversation_store.validate_dataset_ids(
            dataset_ids,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_dataset_details",
        lambda dataset_id: get_dataset_details(dataset_id, database),
    )
    monkeypatch.setattr(
        conversations_api,
        "record_message",
        lambda requested_id, **kwargs: conversation_store.record_message(
            requested_id,
            database_path=database,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_event_by_idempotency_key",
        lambda requested_id, key: conversation_store.get_event_by_idempotency_key(
            requested_id,
            key,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "generate_analysis",
        lambda prompt, dataset_ids, **kwargs: sql_analysis_service.generate_analysis(
            prompt,
            dataset_ids,
            database_path=database,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        sql_analysis_service,
        "generate_text",
        lambda **_kwargs: (json.dumps(plan), "gemini"),
    )

    response = client.post(
        f"/conversations/{conversation_id}/sql-analysis",
        json={
            "question": "Generate DDL for a Students table.",
            "request_id": "ddl-request-1",
            "mode": "code_generation",
        },
    )

    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis["mode"] == "code_generation"
    assert analysis["status"] == "generated"
    assert analysis["queries"][0]["sql"] == ddl
    assert analysis["queries"][0]["execution_status"] == "not_executed"
    restored = conversation_store.get_conversation(
        conversation_id,
        database_path=database,
    )
    assistant_event = next(
        event
        for event in restored["events"]
        if event["actor"] == "assistant"
    )
    assert assistant_event["metadata"]["mode"] == "code_generation"
    assert assistant_event["metadata"]["intent"] == "sql_code_generation"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'Students'"
        ).fetchall() == []


def test_analyst_sql_join_requires_and_uses_chat_scoped_approval(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "analyst-sql-join.db"
    customers_path = tmp_path / "customers.csv"
    customers_path.write_text(
        "customer_id,name\n1,Ada\n2,Lin\n",
        encoding="utf-8",
    )
    orders_path = tmp_path / "orders.csv"
    orders_path.write_text(
        "order_id,customer_id,amount\n100,1,5\n101,2,7\n",
        encoding="utf-8",
    )
    customers = store_spreadsheet(customers_path, "customers.csv", database)
    orders = store_spreadsheet(orders_path, "orders.csv", database)
    dataset_ids = [customers["dataset_id"], orders["dataset_id"]]
    conversation = conversation_store.create_conversation(
        title="Analyst join",
        selected_dataset_ids=dataset_ids,
        database_path=database,
    )
    conversation_id = conversation["conversation_id"]
    customers_table = customers["table_name"]
    orders_table = orders["table_name"]

    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda requested_id: conversation_store.get_conversation(
            requested_id,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda requested_ids: conversation_store.validate_dataset_ids(
            requested_ids,
            database_path=database,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_dataset_details",
        lambda dataset_id: get_dataset_details(dataset_id, database),
    )
    monkeypatch.setattr(
        conversations_api,
        "record_message",
        lambda requested_id, **kwargs: conversation_store.record_message(
            requested_id,
            database_path=database,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "record_event",
        lambda requested_id, event_type, **kwargs: conversation_store.record_event(
            requested_id,
            event_type,
            database_path=database,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "get_sql_analysis_context",
        lambda selected_ids, **kwargs: sql_analysis_service.get_sql_analysis_context(
            selected_ids,
            database_path=database,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        conversations_api,
        "generate_analysis",
        lambda prompt, selected_ids, **kwargs: sql_analysis_service.generate_analysis(
            prompt,
            selected_ids,
            database_path=database,
            **kwargs,
        ),
    )
    sql = (
        f'SELECT c."name", SUM(o."amount") AS "total_amount" '
        f'FROM "{orders_table}" AS o '
        f'JOIN "{customers_table}" AS c '
        'ON o."customer_id" = c."customer_id" '
        'GROUP BY c."name" ORDER BY c."name"'
    )
    plan = {
        "analysis_title": "Order totals by customer",
        "queries": [
            {
                "title": "Order totals by customer",
                "description": "",
                "sql": sql,
                "source_dataset_ids": dataset_ids,
                "referenced_tables": [orders_table, customers_table],
                "referenced_columns": ["customer_id", "amount", "name"],
                "join_explanation": "Orders link to customers by customer_id.",
            }
        ],
    }
    monkeypatch.setattr(
        sql_analysis_service,
        "generate_text",
        lambda **_kwargs: (json.dumps(plan), "gemini"),
    )

    candidates_response = client.get(
        f"/conversations/{conversation_id}/sql-relationships"
    )
    assert candidates_response.status_code == 200
    relationship = next(
        item
        for item in candidates_response.json()["relationships"]
        if item["left"]["column"] == "customer_id"
        and item["right"]["column"] == "customer_id"
    )
    denied = client.post(
        f"/conversations/{conversation_id}/sql-analysis",
        json={"question": "Show each customer's order total"},
    )
    assert denied.status_code == 200
    assert denied.json()["analysis"]["queries"][0][
        "execution_status"
    ] == "needs_clarification"

    approved = client.post(
        f"/conversations/{conversation_id}/sql-relationships/"
        f'{relationship["relationship_id"]}/approval'
    )
    assert approved.status_code == 200
    result = client.post(
        f"/conversations/{conversation_id}/sql-analysis",
        json={"question": "Show each customer's order total"},
    )

    assert result.status_code == 200
    assert result.json()["analysis"]["queries"][0]["result"]["rows"] == [
        {"name": "Ada", "total_amount": 5},
        {"name": "Lin", "total_amount": 7},
    ]
