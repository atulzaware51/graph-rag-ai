
"""LangGraph workflow for GraphSheet AI Text-to-SQL."""

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.services.text_to_sql_service import (
    TextToSQLError,
    ask_with_sql,
)

logger = logging.getLogger(__name__)


class TextToSQLState(TypedDict, total=False):
    """Data passed between workflow nodes."""

    question: str
    dataset_id: str
    graph_context: dict[str, Any]
    status: str
    result: dict[str, Any]
    error: str


def validate_input(state: TextToSQLState) -> dict[str, Any]:
    """Validate required input before invoking the LLM."""

    question = state.get("question")
    dataset_id = state.get("dataset_id")

    if not isinstance(question, str) or not question.strip():
        return {
            "status": "error",
            "error": "Question must not be empty.",
        }

    if not isinstance(dataset_id, str) or not dataset_id.strip():
        return {
            "status": "error",
            "error": "Dataset ID must not be empty.",
        }

    return {
        "question": question.strip(),
        "dataset_id": dataset_id.strip(),
        "status": "ready",
    }


def route_after_validation(state: TextToSQLState) -> str:
    """Choose whether to continue or stop."""

    if state.get("status") == "ready":
        return "execute"

    return "finish"


def execute_query(state: TextToSQLState) -> dict[str, Any]:
    """Generate and execute SQL through the existing safe service."""

    try:
        sql_arguments: dict[str, Any] = {
            "question": state["question"],
            "dataset_id": state["dataset_id"],
        }
        if state.get("graph_context") is not None:
            sql_arguments["graph_context"] = state["graph_context"]
        result = ask_with_sql(
            **sql_arguments,
        )

        return {
            "status": "success",
            "result": result,
        }
    except TextToSQLError as exc:
        return {
            "status": "error",
            "error": str(exc),
        }
    except Exception as exc:
        logger.error(
            "Text-to-SQL workflow failed (%s).",
            type(exc).__name__,
        )
        return {
            "status": "error",
            "error": "Text-to-SQL workflow failed.",
        }


def finish(state: TextToSQLState) -> dict[str, Any]:
    """Finalize the workflow without running the SQL service."""

    return {
        "status": state.get("status", "error"),
        "error": state.get("error", "Invalid workflow input."),
    }


def build_text_to_sql_graph():
    """Construct and compile the Text-to-SQL workflow."""

    workflow = StateGraph(TextToSQLState)

    workflow.add_node("validate", validate_input)
    workflow.add_node("execute", execute_query)
    workflow.add_node("finish", finish)

    workflow.add_edge(START, "validate")

    workflow.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "execute": "execute",
            "finish": "finish",
        },
    )

    workflow.add_edge("execute", END)
    workflow.add_edge("finish", END)

    return workflow.compile()


text_to_sql_graph = build_text_to_sql_graph()


def ask_with_sql_graph(
    question: str,
    dataset_id: str,
    *,
    graph_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the compiled LangGraph workflow."""

    return text_to_sql_graph.invoke(
        {
            "question": question,
            "dataset_id": dataset_id,
            "graph_context": graph_context,
        }
    )
