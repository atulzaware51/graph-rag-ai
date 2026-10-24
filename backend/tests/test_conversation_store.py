import pytest

from backend.app.services.conversation_store import (
    ConversationStoreError,
    create_conversation,
    get_conversation,
    record_choice,
    record_clarification,
    record_execution,
    record_message,
    record_result_reference,
    record_sql,
)


def test_conversation_records_message_clarification_choice_sql_and_execution(tmp_path):
    database_path = tmp_path / "conversation.db"

    conversation = create_conversation(
        title="Customer revenue question",
        dataset_id="dataset_abc",
        database_path=database_path,
    )

    record_message(
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
