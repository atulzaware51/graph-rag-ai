"""Local sentence embeddings for spreadsheet evidence chunks."""

from __future__ import annotations

import os
import math
from threading import Lock
from typing import Any


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_MODEL_LOCK = Lock()
_ENCODE_LOCK = Lock()
_MODEL: Any = None
_MODEL_NAME: str | None = None


class EmbeddingError(RuntimeError):
    """Raised when a local embedding model cannot encode text."""


def embedding_model_name() -> str:
    """Resolve the configured local sentence-transformer model name."""
    return os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL


def _get_model() -> Any:
    """Load and cache the configured CPU model so later embeddings reuse it."""
    global _MODEL, _MODEL_NAME

    model_name = embedding_model_name()
    with _MODEL_LOCK:
        if _MODEL is not None and _MODEL_NAME == model_name:
            return _MODEL
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(model_name, device="cpu")
        except Exception as exc:
            raise EmbeddingError(
                "Could not load the local embedding model "
                f"{model_name!r}. Install sentence-transformers and ensure "
                "the model is available in the local Hugging Face cache."
            ) from exc
        _MODEL = model
        _MODEL_NAME = model_name
        return model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Encode text into normalized, finite vectors using the local model."""
    if not isinstance(texts, list) or any(
        not isinstance(text, str) or not text.strip() for text in texts
    ):
        raise EmbeddingError("Embedding inputs must be non-empty text strings.")
    if not texts:
        return []

    model = _get_model()
    try:
        with _ENCODE_LOCK:
            encoded = model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        vectors = encoded.tolist()
    except Exception as exc:
        raise EmbeddingError("The local embedding model could not encode text.") from exc

    if (
        len(vectors) != len(texts)
        or not vectors
        or not isinstance(vectors[0], list)
        or not vectors[0]
    ):
        raise EmbeddingError("The embedding model returned an invalid vector shape.")

    dimension = len(vectors[0])
    normalized_vectors: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise EmbeddingError("The embedding model returned inconsistent dimensions.")
        values = [float(value) for value in vector]
        if any(not math.isfinite(value) for value in values):
            raise EmbeddingError("The embedding model returned a non-finite value.")
        normalized_vectors.append(values)
    return normalized_vectors
