from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(store, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.api.routes.store", store)
    return TestClient(app)


def test_health_and_projects(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    names = [p["name"] for p in client.get("/api/projects").json()["projects"]]
    assert "esp32_i2c_demo" in names


def test_full_flow(client: TestClient) -> None:
    session = client.post("/api/sessions", json={"project": "esp32_i2c_demo"}).json()
    assert {c["reference"] for c in session["components"]} >= {"U1", "U2", "J1"}

    plan_response = client.post(
        f"/api/sessions/{session['session_id']}/plan",
        json={
            "selected_components": ["U1", "U2"],
            "instruction": (
                "Connect U1 and U2 using I2C with 3.3V logic. Do not modify J1 or the USB circuit."
            ),
        },
    ).json()
    assert plan_response["executable"] is True
    assert len(plan_response["plan"]["actions"]) == 6

    report = client.post(f"/api/sessions/{session['session_id']}/execute", json={"approved": True}).json()
    assert report["decision"] == "accepted"
    assert report["validation"]["requested_connections_created"] == 6

    stored = client.get(f"/api/sessions/{session['session_id']}/report").json()
    assert stored["decision"] == "accepted"


def test_render_follows_the_working_copy(client: TestClient) -> None:
    """The view is rendered from disk, so approved changes show up and unknown views 404."""
    session = client.post("/api/sessions", json={"project": "esp32_i2c_demo"}).json()
    assert session["has_pcb"] is False
    session_id = session["session_id"]

    before = client.get(f"/api/sessions/{session_id}/render")
    assert before.status_code == 200
    assert before.headers["content-type"].startswith("image/svg+xml")
    assert "I2C_SDA" not in before.text

    client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "selected_components": ["U1", "U2"],
            "instruction": "Connect U1 and U2 using I2C with 3.3V logic.",
        },
    )
    assert client.get(f"/api/sessions/{session_id}/render").text == before.text, "plans alone change nothing"

    client.post(f"/api/sessions/{session_id}/execute", json={"approved": True})
    after = client.get(f"/api/sessions/{session_id}/render")
    assert after.status_code == 200
    assert after.text != before.text

    assert client.get(f"/api/sessions/{session_id}/render", params={"view": "pcb"}).status_code == 404
    assert client.get(f"/api/sessions/{session_id}/render", params={"view": "3d"}).status_code == 400


def test_pcb_fixture_renders_board(client: TestClient) -> None:
    session = client.post("/api/sessions", json={"project": "esp32_i2c_board"})
    assert session.status_code == 200
    session_data = session.json()
    assert session_data["has_pcb"] is True

    rendered = client.get(
        f"/api/sessions/{session_data['session_id']}/render",
        params={"view": "pcb"},
    )
    assert rendered.status_code == 200
    assert rendered.headers["content-type"].startswith("image/svg+xml")
    assert "<path" in rendered.text or "<line" in rendered.text


def test_execution_requires_approval(client: TestClient) -> None:
    session = client.post("/api/sessions", json={"project": "esp32_i2c_demo"}).json()
    response = client.post(f"/api/sessions/{session['session_id']}/execute", json={"approved": False})
    assert response.status_code == 400


def test_execution_requires_a_validated_plan(client: TestClient) -> None:
    session = client.post("/api/sessions", json={"project": "esp32_i2c_demo"}).json()
    response = client.post(f"/api/sessions/{session['session_id']}/execute", json={"approved": True})
    assert response.status_code == 400


def test_clarification_is_returned_instead_of_a_plan(client: TestClient) -> None:
    session = client.post("/api/sessions", json={"project": "esp32_i2c_demo"}).json()
    response = client.post(
        f"/api/sessions/{session['session_id']}/plan",
        json={"selected_components": ["U1", "U2"], "instruction": "Connect the sensor properly."},
    ).json()
    assert response["plan"] is None
    assert response["clarification"]["reason"] == "protocol_not_specified"


def test_unknown_project_and_session(client: TestClient) -> None:
    assert client.post("/api/sessions", json={"project": "nope"}).status_code == 404
    assert client.get("/api/sessions/deadbeef").status_code == 404
