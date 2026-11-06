import hashlib
import math
import os
import re

import pytest


os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for variable in (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "MISTRAL_API_KEY",
    "MISTRAL_MODEL",
    "LLM_PROVIDER",
    "LLM_FALLBACK_PROVIDERS",
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
    "EMBEDDING_MODEL",
):
    os.environ.pop(variable, None)


@pytest.fixture(autouse=True)
def reset_neo4j_driver():
    from backend.app.services.neo4j_store import close_driver

    close_driver()
    yield
    close_driver()


@pytest.fixture(autouse=True)
def use_deterministic_test_embeddings(monkeypatch):
    """Keep persistence and retrieval tests independent of model downloads."""
    def encode(texts):
        results = []
        for text in texts:
            vector = [0.0] * 128
            for token in re.findall(r"[a-z0-9]+", text.casefold()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                vector[int.from_bytes(digest[:2], "little") % len(vector)] += 1
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            results.append([value / norm for value in vector])
        return results

    from backend.app.services import dataset_store, vector_store

    monkeypatch.setattr(dataset_store, "embed_texts", encode)
    monkeypatch.setattr(vector_store, "embed_texts", encode)


@pytest.fixture(autouse=True)
def use_test_chroma_collection(monkeypatch):
    """Keep vector-search tests local and independent of a Chroma installation."""
    from backend.app.services import vector_store

    class TestCollection:
        def __init__(self):
            self.records = {}

        def count(self, where=None):
            return len(self._matching_ids(where))

        def get(self, *, where=None, limit=None, include=None):
            ids = self._matching_ids(where)
            if limit is not None:
                ids = ids[:limit]
            return {"ids": ids}

        def delete(self, where=None):
            for record_id in self._matching_ids(where):
                del self.records[record_id]

        def upsert(self, *, ids, documents, embeddings, metadatas):
            for record_id, document, embedding, metadata in zip(
                ids,
                documents,
                embeddings,
                metadatas,
            ):
                self.records[record_id] = {
                    "document": document,
                    "embedding": embedding,
                    "metadata": metadata,
                }

        def query(self, *, query_embeddings, n_results, where, include):
            selected = []
            for record_id in self._matching_ids(where):
                record = self.records[record_id]
                similarity = sum(
                    left * right
                    for left, right in zip(
                        query_embeddings[0],
                        record["embedding"],
                    )
                )
                selected.append((1.0 - similarity, record_id, record))
            selected.sort(key=lambda result: (result[0], result[1]))
            selected = selected[:n_results]
            return {
                "ids": [[record_id for _, record_id, _ in selected]],
                "documents": [
                    [record["document"] for _, _, record in selected]
                ],
                "metadatas": [
                    [record["metadata"] for _, _, record in selected]
                ],
                "distances": [[distance for distance, _, _ in selected]],
            }

        def _matching_ids(self, where):
            if not where:
                return list(self.records)
            filters = where.get("$and", [where])
            return [
                record_id
                for record_id, record in self.records.items()
                if all(
                    (
                        record["metadata"].get(key) == value
                        if isinstance(value, str)
                        else record["metadata"].get(key) in value.get("$in", [])
                    )
                    for item in filters
                    for key, value in item.items()
                )
            ]

    collection = TestCollection()
    monkeypatch.setattr(
        vector_store,
        "_get_chroma_collection",
        lambda _dimensions: collection,
    )
    return collection
