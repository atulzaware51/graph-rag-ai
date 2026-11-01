"""HTTP handlers for persisting GraphSheet AI conversations."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.services.conversation_store import (
    ConversationStoreError,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_graph,
    get_event_by_idempotency_key,
    list_conversations,
    record_choice,
    record_clarification,
    record_event,
    record_execution,
    record_message,
    record_result_reference,
    record_sql,
    set_conversation_datasets,
    validate_dataset_ids,
)
from backend.app.services.answer_service import (
    AnswerServiceError,
    answer_question,
)
from backend.app.services.dataset_reader import (
    DatasetNotFoundError,
    get_dataset_details,
)
from backend.app.services.neo4j_store import (
    graph_store_status,
)
from backend.app.api.neo4j_errors import neo4j_unavailable_response
from backend.app.services.graph_service import (
    GraphServiceError,
    build_graph_for_scope,
)
from backend.app.services.retrieval import RetrievalError
from backend.app.services.text_to_sql_service import (
    TextToSQLError,
    ask_with_sql,
)

router = APIRouter(prefix="/conversations", tags=["Conversations"])
logger = logging.getLogger(__name__)


class CreateConversationRequest(BaseModel):
    """Payload used to create a conversation."""

    title: str | None = None
    dataset_id: str | None = None
    selected_dataset_ids: list[str] = Field(default_factory=list)
    conversation_id: str | None = None


class SelectedDatasetsRequest(BaseModel):
    """Dataset selection for one chat."""

    dataset_ids: list[str]


class ChatQuestionRequest(BaseModel):
    """Chat-scoped question payload."""

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class ChatSQLRequest(BaseModel):
    """Chat-scoped natural-language SQL payload."""

    question: str = Field(min_length=1, max_length=2000)


class EventRequest(BaseModel):
    """Generic event payload for persisted conversation activity."""

    event_type: str = Field(min_length=1, max_length=64)
    actor: str = Field(default="assistant", min_length=1, max_length=32)
    message: str | None = None
    metadata: dict[str, Any] | None = None
    result_reference: str | None = None


@router.get("/")
def get_all_conversations():
    """Return all stored conversations."""
    try:
        return {
            "status": "success",
            "count": len(list_conversations()),
            "conversations": list_conversations(),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/")
def create_conversation_endpoint(request: CreateConversationRequest):
    """Create a new conversation record."""
    try:
        selected_dataset_ids = request.selected_dataset_ids
        if request.dataset_id and request.dataset_id not in selected_dataset_ids:
            selected_dataset_ids = [
                request.dataset_id,
                *selected_dataset_ids,
            ]
        selected_dataset_ids = validate_dataset_ids(selected_dataset_ids)
        return {
            "status": "success",
            "conversation": create_conversation(
                title=request.title,
                selected_dataset_ids=selected_dataset_ids,
                conversation_id=request.conversation_id,
            ),
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.patch("/{conversation_id}/datasets")
def update_conversation_datasets(
    conversation_id: str,
    request: SelectedDatasetsRequest,
):
    """Replace the selected datasets for a conversation."""
    try:
        return {
            "status": "success",
            "conversation": set_conversation_datasets(
                conversation_id,
                request.dataset_ids,
            ),
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ConversationStoreError as exc:
        if exc.code and exc.code.startswith("NEO4J_"):
            return neo4j_unavailable_response(exc)
        if "Neo4j" in str(exc):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif str(exc) == "Conversation not found.":
            status_code = status.HTTP_404_NOT_FOUND
        elif "do not exist" in str(exc):
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        else:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        raise HTTPException(
            status_code=status_code,
            detail=str(exc),
        ) from exc


@router.get("/{conversation_id}/graph")
def get_conversation_graph_endpoint(conversation_id: str):
    """Return persisted graph nodes and edges for a conversation."""
    try:
        conversation = get_conversation(conversation_id)
        dataset_ids = validate_dataset_ids(
            conversation["selected_dataset_ids"]
        )
        graph = _selected_graph_context(
            conversation_id,
            dataset_ids,
            include_evidence=True,
            include_history=True,
        )
        visible_nodes = [
            node for node in graph["nodes"]
            if node.get("type") != "Evidence"
        ]
        visible_node_ids = {node["id"] for node in visible_nodes}
        graph["nodes"] = visible_nodes
        graph["edges"] = [
            edge
            for edge in graph["edges"]
            if edge.get("source") in visible_node_ids
            and edge.get("target") in visible_node_ids
        ]
        return {
            "status": "success",
            "graph": {
                **graph,
                "graph_store": graph.get(
                    "graph_store",
                    graph_store_status(),
                ),
            },
        }
    except ConversationStoreError as exc:
        if exc.code and exc.code.startswith("NEO4J_"):
            return neo4j_unavailable_response(exc)
        if "Neo4j" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if str(exc) == "Conversation not found."
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=str(exc),
        ) from exc


def _selected_graph_context(
    conversation_id: str,
    dataset_ids: list[str],
    *,
    include_evidence: bool = False,
    include_history: bool = False,
) -> dict[str, Any]:
    """Combine this chat's history graph with its selected-sheet knowledge graph."""
    chat_graph = get_conversation_graph(conversation_id)
    selected = set(dataset_ids)
    graph_scope_id = f"chat-schema:{conversation_id}"
    try:
        sheet_graph = build_graph_for_scope(
            graph_scope_id,
            dataset_ids=dataset_ids,
            include_evidence=include_evidence,
        )
    except GraphServiceError as exc:
        raise ConversationStoreError(
            str(exc),
            code=exc.code or "GRAPH_RETRIEVAL_FAILED",
            retry_after_seconds=exc.retry_after_seconds or 5,
        ) from exc
    nodes_by_id = {
        node["id"]: node
        for node in sheet_graph["nodes"]
    }
    id_mapping: dict[str, str] = {}
    history_nodes: list[dict[str, Any]] = []
    for node in chat_graph["nodes"]:
        node_type = node.get("type")
        dataset_id = node.get("dataset_id")
        if node_type == "Dataset":
            if dataset_id not in selected:
                continue
            target_id = f"dataset:{dataset_id}"
            id_mapping[node["id"]] = target_id
        elif node_type == "Column":
            if dataset_id not in selected:
                continue
            sql_name = node.get("sql_name")
            if not isinstance(sql_name, str):
                continue
            target_id = f"column:{dataset_id}:{sql_name}"
            id_mapping[node["id"]] = target_id
        else:
            if node_type != "Chat" and not include_history:
                continue
            history_nodes.append(node)
            id_mapping[node["id"]] = node["id"]

    for node in history_nodes:
        nodes_by_id[node["id"]] = node

    edges_by_id = {
        edge["id"]: edge
        for edge in sheet_graph["edges"]
    }
    for edge in chat_graph["edges"]:
        source = id_mapping.get(edge.get("source"))
        target = id_mapping.get(edge.get("target"))
        if source not in nodes_by_id or target not in nodes_by_id:
            continue
        remapped_edge = {
            **edge,
            "source": source,
            "target": target,
        }
        edges_by_id[edge["id"]] = remapped_edge

    evidence_nodes_by_source = {
        (node.get("dataset_id"), node.get("chunk_id")): node
        for node in nodes_by_id.values()
        if node.get("type") == "Evidence"
        and isinstance(node.get("chunk_id"), str)
    }
    for node in history_nodes:
        if node.get("type") != "Evidence":
            continue
        source = evidence_nodes_by_source.get(
            (node.get("dataset_id"), node.get("chunk_id"))
        )
        if source is None or source["id"] == node["id"]:
            continue
        edge_id = f"evidence-reference:{node['id']}:{source['id']}"
        edges_by_id[edge_id] = {
            "id": edge_id,
            "source": node["id"],
            "target": source["id"],
            "type": "REFERS_TO",
        }

    nodes = list(nodes_by_id.values())
    edges = list(edges_by_id.values())
    column_nodes = {
        node["id"]: node
        for node in nodes
        if node.get("type") == "Column"
        and node.get("dataset_id") in selected
    }
    candidates = []
    for edge in sheet_graph["edges"]:
        if edge.get("type") not in {"CANDIDATE_MATCH", "RELATED_TO"}:
            continue
        left = column_nodes.get(edge["source"])
        right = column_nodes.get(edge["target"])
        if left is None or right is None:
            continue
        candidates.append(
            {
                "left": {
                    "dataset_id": left["dataset_id"],
                    "column": left["sql_name"],
                    "label": left["label"],
                    "table_name": next(
                        (
                            node.get("table_name")
                            for node in nodes
                            if node.get("type") == "Dataset"
                            and node.get("dataset_id") == left["dataset_id"]
                        ),
                        None,
                    ),
                },
                "right": {
                    "dataset_id": right["dataset_id"],
                    "column": right["sql_name"],
                    "label": right["label"],
                    "table_name": next(
                        (
                            node.get("table_name")
                            for node in nodes
                            if node.get("type") == "Dataset"
                            and node.get("dataset_id") == right["dataset_id"]
                        ),
                        None,
                    ),
                },
                "status": edge.get("status", "candidate"),
                "confidence": edge.get("confidence", 0.0),
                "shared_value_count": edge.get("shared_value_count", 0),
                "evidence": edge.get("evidence", ""),
                "edge_id": edge["id"],
            }
        )
    return {
        "conversation_id": conversation_id,
        "nodes": nodes,
        "edges": edges,
        "candidate_relationships": candidates,
        "graph_store": (
            "sqlite"
            if chat_graph.get("graph_store") == "sqlite"
            or sheet_graph.get("graph_store") == "sqlite"
            else graph_store_status()
        ),
        "relationship_note": (
            "Candidate links are heuristic, not verified foreign keys."
        ),
    }


@router.post("/{conversation_id}/ask")
def ask_conversation_question(
    conversation_id: str,
    request: ChatQuestionRequest,
):
    """Answer using only this chat's selected sheets, history, and graph."""
    try:
        conversation = get_conversation(conversation_id)
        dataset_ids = validate_dataset_ids(
            conversation["selected_dataset_ids"]
        )
        if not dataset_ids:
            raise ValueError("Select at least one dataset for this chat.")

        for dataset_id in dataset_ids:
            get_dataset_details(dataset_id)
        answer_key = (
            f"ask:{request.request_id}:answer"
            if request.request_id
            else None
        )
        if answer_key is not None:
            cached_event = get_event_by_idempotency_key(
                conversation_id,
                answer_key,
            )
            if cached_event is not None:
                cached_metadata = cached_event.get("metadata", {})
                cached_result = cached_metadata.get("response")
                if (
                    cached_metadata.get("dataset_ids") != dataset_ids
                    or cached_metadata.get("top_k") != request.top_k
                    or (
                        isinstance(cached_result, dict)
                        and cached_result.get("question") != request.query
                    )
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="This request ID was already used for a different question or dataset selection.",
                    )
                if isinstance(cached_result, dict):
                    return {
                        **cached_result,
                        "conversation_id": conversation_id,
                        "graph_store": cached_metadata.get(
                            "graph_store",
                            graph_store_status(),
                        ),
                        "events": {
                            "user": cached_metadata.get("user_event_id"),
                            "assistant": cached_event["event_id"],
                        },
                    }

        history = [
            {
                "role": event["actor"],
                "content": event["message"],
            }
            for event in conversation["events"]
            if event["event_type"] == "message"
            and event["actor"] in {"user", "assistant"}
            and event["message"]
        ][-20:]

        user_event = record_message(
            conversation_id,
            actor="user",
            message=request.query,
            metadata={
                "dataset_ids": dataset_ids,
                "top_k": request.top_k,
                **(
                    {"request_id": request.request_id}
                    if request.request_id
                    else {}
                ),
            },
            idempotency_key=(
                f"ask:{request.request_id}:question"
                if request.request_id
                else None
            ),
        )
        graph_context = _selected_graph_context(
            conversation_id,
            dataset_ids,
            include_evidence=True,
        )
        result = answer_question(
            query=request.query,
            dataset_ids=dataset_ids,
            top_k=request.top_k,
            graph_context=graph_context,
            conversation_history=history,
        )
        assistant_event = record_message(
            conversation_id,
            actor="assistant",
            message=result["answer"],
            metadata={
                "dataset_ids": dataset_ids,
                "top_k": request.top_k,
                "sources": result.get("sources", []),
                "status": result["status"],
                "in_reply_to": user_event["event_id"],
                "request_id": request.request_id,
                "response": result,
                "graph_store": graph_context.get(
                    "graph_store",
                    graph_store_status(),
                ),
            },
            idempotency_key=answer_key,
        )
        return {
            **result,
            "conversation_id": conversation_id,
            "graph_store": graph_context.get(
                "graph_store",
                graph_store_status(),
            ),
            "events": {
                "user": user_event["event_id"],
                "assistant": assistant_event["event_id"],
            },
        }
    except (ValueError, RetrievalError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ConversationStoreError as exc:
        if exc.code == "SQLITE_BUSY" or (
            exc.code is not None and exc.code.startswith("GRAPH_")
        ):
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif exc.code == "IDEMPOTENCY_CONFLICT":
            code = status.HTTP_409_CONFLICT
        elif "Neo4j" in str(exc):
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif str(exc) == "Conversation not found.":
            code = status.HTTP_404_NOT_FOUND
        elif "do not exist" in str(exc):
            code = status.HTTP_404_NOT_FOUND
        else:
            code = status.HTTP_500_INTERNAL_SERVER_ERROR
        detail: str | dict[str, Any] = str(exc)
        if exc.code:
            detail = {
                "code": exc.code,
                "message": str(exc),
                "retryable": (
                    exc.retry_after_seconds is not None
                    or (exc.code is not None and exc.code.startswith("GRAPH_"))
                ),
                **(
                    {"retry_after_seconds": exc.retry_after_seconds}
                    if exc.retry_after_seconds is not None
                    else {}
                ),
            }
        raise HTTPException(
            status_code=code,
            detail=detail,
            headers=(
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds is not None
                else None
            ),
        ) from exc
    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="A selected dataset no longer exists.",
        ) from exc
    except AnswerServiceError as exc:
        try:
            record_event(
                conversation_id,
                "answer_error",
                actor="assistant",
                message="Question answering failed.",
                metadata={"error": str(exc)},
            )
        except ConversationStoreError:
            logger.exception(
                "Could not persist answer failure for conversation %s.",
                conversation_id,
            )
        raise HTTPException(
            status_code=exc.http_status,
            detail=exc.as_dict(),
            headers=(
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds is not None
                else None
            ),
        ) from exc
    except Exception as exc:
        logger.exception(
            "Chat question failed for conversation %s.",
            conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete the chat question.",
        ) from exc


@router.post("/{conversation_id}/sql")
def run_conversation_sql(
    conversation_id: str,
    request: ChatSQLRequest,
):
    """Generate and execute safe SQL against only this chat's selected sheets."""
    try:
        conversation = get_conversation(conversation_id)
        dataset_ids = validate_dataset_ids(
            conversation["selected_dataset_ids"]
        )
        if not dataset_ids:
            raise ValueError("Select at least one dataset for this chat.")
        for dataset_id in dataset_ids:
            get_dataset_details(dataset_id)

        graph_context = _selected_graph_context(
            conversation_id,
            dataset_ids,
            include_evidence=True,
        )
        record_message(
            conversation_id,
            actor="user",
            message=request.question,
            metadata={"dataset_ids": dataset_ids, "intent": "sql"},
        )
        result = ask_with_sql(
            request.question,
            dataset_ids=dataset_ids,
            graph_context=graph_context,
        )
        query_event = record_sql(
            conversation_id,
            actor="assistant",
            sql=result["sql"],
            metadata={
                "question": request.question,
                "dataset_id": dataset_ids[0],
                "dataset_ids": dataset_ids,
                "read_columns": result["result"].get("read_columns", []),
                "read_column_refs": result["result"].get(
                    "read_column_refs", []
                ),
                "result_columns": result["result"].get("columns", []),
                "graph_guidance": result.get("graph_guidance", {}),
                "status": "success",
            },
        )
        query_id = query_event["event_id"]
        execution = record_execution(
            conversation_id,
            actor="assistant",
            status="success",
            message="SQL execution completed",
            metadata={"query_id": query_id},
        )
        result_event = record_result_reference(
            conversation_id,
            actor="assistant",
            result_reference=f"result:{query_id}",
            message=f"Query returned {result['result']['row_count']} rows",
            metadata={
                "query_id": query_id,
                "dataset_ids": dataset_ids,
                "columns": result["result"]["columns"],
                "rows": result["result"]["rows"][:25],
                "row_count": result["result"]["row_count"],
            },
        )
        _selected_graph_context(
            conversation_id,
            dataset_ids,
            include_evidence=True,
            include_history=True,
        )
        return {
            **result,
            "conversation_id": conversation_id,
            "graph_store": graph_store_status(),
            "events": {
                "query": query_id,
                "execution": execution["event_id"],
                "result": result_event["event_id"],
            },
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ConversationStoreError as exc:
        if "Neo4j" in str(exc):
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif str(exc) == "Conversation not found.":
            code = status.HTTP_404_NOT_FOUND
        elif "do not exist" in str(exc):
            code = status.HTTP_404_NOT_FOUND
        else:
            code = status.HTTP_500_INTERNAL_SERVER_ERROR
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="A selected dataset no longer exists.",
        ) from exc
    except TextToSQLError as exc:
        try:
            failed_query = record_event(
                conversation_id,
                "sql",
                actor="assistant",
                message="SQL analysis failed.",
                metadata={
                    "question": request.question,
                    "dataset_ids": dataset_ids,
                    "status": "failed",
                    "error": str(exc),
                },
            )
            record_execution(
                conversation_id,
                actor="assistant",
                status="failed",
                message="SQL execution failed",
                metadata={
                    "query_id": failed_query["event_id"],
                    "error": str(exc),
                },
            )
        except ConversationStoreError:
            logger.exception(
                "Could not persist SQL failure for conversation %s.",
                conversation_id,
            )
        if exc.code != "TEXT_TO_SQL_ERROR":
            error_status = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.retryable
                else (
                    status.HTTP_500_INTERNAL_SERVER_ERROR
                    if exc.code == "LLM_PROVIDER_NOT_CONFIGURED"
                    else status.HTTP_502_BAD_GATEWAY
                )
            )
            detail: str | dict[str, Any] = {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            }
        else:
            error_status = status.HTTP_502_BAD_GATEWAY
            detail = str(exc)
        raise HTTPException(
            status_code=error_status,
            detail=detail,
        ) from exc
    except Exception as exc:
        logger.exception(
            "Chat SQL failed for conversation %s.",
            conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete chat SQL analysis.",
        ) from exc


@router.get("/{conversation_id}")
def get_conversation_endpoint(conversation_id: str):
    """Return the full conversation state and chronology."""
    try:
        return {
            "status": "success",
            "conversation": get_conversation(conversation_id),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete("/{conversation_id}")
def delete_conversation_endpoint(conversation_id: str):
    """Delete one conversation and its graph history."""
    try:
        if not delete_conversation(conversation_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found.",
            )
        return {
            "status": "success",
            "conversation_id": conversation_id,
            "deleted": True,
        }
    except ConversationStoreError as exc:
        if exc.code and exc.code.startswith("NEO4J_"):
            return neo4j_unavailable_response(exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/{conversation_id}/events")
def add_event(conversation_id: str, request: EventRequest):
    """Persist a generic conversation event."""
    try:
        return {
            "status": "success",
            "event": record_event(
                conversation_id,
                request.event_type,
                actor=request.actor,
                message=request.message,
                metadata=request.metadata,
                result_reference=request.result_reference,
            ),
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{conversation_id}/messages")
def add_message(conversation_id: str, request: dict[str, Any]):
    """Persist a message event."""
    actor = str(request.get("actor", "user"))
    message = request.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message must not be empty.",
        )

    try:
        return {
            "status": "success",
            "event": record_message(
                conversation_id,
                actor=actor,
                message=message,
                metadata=request.get("metadata"),
            ),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{conversation_id}/clarifications")
def add_clarification(conversation_id: str, request: dict[str, Any]):
    """Persist a clarification question and options."""
    message = request.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Clarification message must not be empty.",
        )

    try:
        return {
            "status": "success",
            "event": record_clarification(
                conversation_id,
                actor=str(request.get("actor", "assistant")),
                message=message,
                options=request.get("options"),
                metadata=request.get("metadata"),
            ),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{conversation_id}/choices")
def add_choice(conversation_id: str, request: dict[str, Any]):
    """Persist a user choice made in response to a clarification."""
    selected_option = request.get("selected_option")
    if not isinstance(selected_option, str) or not selected_option.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="selected_option must not be empty.",
        )

    try:
        return {
            "status": "success",
            "event": record_choice(
                conversation_id,
                actor=str(request.get("actor", "user")),
                message=request.get("message"),
                selected_option=selected_option,
                metadata=request.get("metadata"),
            ),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{conversation_id}/sql")
def add_sql(conversation_id: str, request: dict[str, Any]):
    """Persist generated SQL output."""
    sql = request.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="SQL must not be empty.",
        )

    try:
        return {
            "status": "success",
            "event": record_sql(
                conversation_id,
                actor=str(request.get("actor", "assistant")),
                sql=sql,
                metadata=request.get("metadata"),
            ),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{conversation_id}/execution")
def add_execution(conversation_id: str, request: dict[str, Any]):
    """Persist SQL execution status and optional result references."""
    status_value = request.get("status")
    if not isinstance(status_value, str) or not status_value.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Execution status must not be empty.",
        )

    try:
        return {
            "status": "success",
            "event": record_execution(
                conversation_id,
                actor=str(request.get("actor", "system")),
                status=status_value,
                message=request.get("message"),
                result_reference=request.get("result_reference"),
                metadata=request.get("metadata"),
            ),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{conversation_id}/result-references")
def add_result_reference_endpoint(conversation_id: str, request: dict[str, Any]):
    """Persist a reference to a result artifact."""
    result_reference = request.get("result_reference")
    if not isinstance(result_reference, str) or not result_reference.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="result_reference must not be empty.",
        )

    try:
        return {
            "status": "success",
            "event": record_result_reference(
                conversation_id,
                actor=str(request.get("actor", "assistant")),
                result_reference=result_reference,
                message=request.get("message"),
                metadata=request.get("metadata"),
            ),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
