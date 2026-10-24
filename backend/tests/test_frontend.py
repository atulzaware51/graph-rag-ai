from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_frontend_and_static_assets_are_served():
    page = client.get("/app/")
    script = client.get("/app/app.js")
    styles = client.get("/app/styles.css")

    assert page.status_code == 200
    assert "GraphSheet AI" in page.text
    assert script.status_code == 200
    assert "loadDatasets" in script.text
    assert styles.status_code == 200


def test_api_root_contract_is_preserved():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["status"] == "success"
