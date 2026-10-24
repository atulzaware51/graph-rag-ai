"""HTTP handlers for persisting GraphSheet AI conversations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.services.conversation_store import (
    ConversationStoreError,
    create_conversation,
    get_conversation,
    list_conversations,
    record_choice,
    record_clarification,
    record_event,
    record_execution,
    record_message,
    record_result_reference,
    record_sql,
)

router = APIRouter(prefix="/conversations", tags=["Conversations"])


class CreateConversationRequest(BaseModel):
    """Payload used to create a conversation."""

    title: str | None = None
    dataset_id: str | None = None


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
        return {
            "status": "success",
            "conversation": create_conversation(
                title=request.title,
                dataset_id=request.dataset_id,
            ),
        }
    except ConversationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
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
