"""Team API route coverage."""

from __future__ import annotations

from app.team.evidence import EvidenceStore
from app.team.schemas import ProjectSpec, TeamRunReport


def test_team_run_creation_status_report_evidence_and_answer(client, store, monkeypatch) -> None:
    import app.api.team as team_api

    monkeypatch.setattr(team_api, "store", store)
    monkeypatch.setattr(team_api._executor, "submit", lambda *_args, **_kwargs: None)
    response = client.post(
        "/api/team/runs",
        json={"project": "esp32_i2c_demo", "request": "Validate the fixture"},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    status = client.get(f"/api/team/runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "running"

    report = TeamRunReport(
        run_id=run_id,
        project=ProjectSpec(project_id="demo", current_stage="team"),
        request="Validate the fixture",
        requirements_satisfied=0,
        requirements_total=0,
        erc_status="not run",
        drc_status="not run",
        release_status="needs_human_review",
        summary="needs_human_review",
    )
    team_api._runs[run_id]["report"] = report
    evidence = EvidenceStore(run_id)
    evidence.evidence_path.write_text('{"check":"test"}\n')
    assert client.get(f"/api/team/runs/{run_id}/report").status_code == 200
    assert client.get(f"/api/team/runs/{run_id}/evidence").json()["records"] == [{"check": "test"}]

    answer = client.post(f"/api/team/runs/{run_id}/answer", json={"answer": "Use the I2C fixture"})
    assert answer.status_code == 200
    assert answer.json()["accepted"] is True


def test_unknown_team_run_returns_404(client) -> None:
    assert client.get("/api/team/runs/missing").status_code == 404
    assert client.get("/api/team/runs/missing/report").status_code == 404
    assert client.get("/api/team/runs/missing/evidence").status_code == 404
    assert client.post("/api/team/runs/missing/answer", json={"answer": "x"}).status_code == 404
