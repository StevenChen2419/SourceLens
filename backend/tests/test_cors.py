import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_local_frontend_can_preflight_json_post(origin: str) -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/answers",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


def test_unapproved_origin_is_not_allowed() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/answers",
            headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST"},
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_validation_errors_are_readable_by_local_frontend() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/answers", json={"question": ""}, headers={"Origin": "http://127.0.0.1:5173"}
        )
    assert response.status_code == 422
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_document_lifecycle_methods_are_allowed_only_from_local_frontend(method):
    with TestClient(app) as client:
        for origin, expected in [("http://127.0.0.1:5173", 200), ("https://other.example", 400)]:
            response = client.options("/api/documents", headers={"Origin": origin, "Access-Control-Request-Method": method})
            assert response.status_code == expected
