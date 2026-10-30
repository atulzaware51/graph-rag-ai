import os

import pytest


os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for variable in (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
):
    os.environ.pop(variable, None)


@pytest.fixture(autouse=True)
def reset_neo4j_driver():
    from backend.app.services.neo4j_store import close_driver

    close_driver()
    yield
    close_driver()
