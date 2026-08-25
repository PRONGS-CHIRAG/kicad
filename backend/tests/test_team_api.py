"""Team API route coverage."""

from __future__ import annotations

import time

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


def test_a_paused_run_publishes_its_question_and_the_answer_reaches_the_repair_stage(
    client, store, monkeypatch
) -> None:
    """The answer to a parked run goes to its repair stage, not back to the start.

    The restart path keys off a finished report, and a parked run has none - so
    without the paused branch the answer would be filed under `answers` and
    nothing would ever read it.
    """
    import threading

    import app.api.team as team_api
    from app.team.schemas import InterventionRequest

    monkeypatch.setattr(team_api, "store", store)
    monkeypatch.setattr(team_api._executor, "submit", lambda *_args, **_kwargs: None)
    run_id = client.post(
        "/api/team/runs", json={"project": "esp32_i2c_demo", "request": "Validate the fixture"}
    ).json()["run_id"]

    question = InterventionRequest(
        run_id=run_id,
        stage="verification",
        stage_name="Verification",
        problem="The Verification stage failed its gate, and the repair stage could not satisfy it either.",
        findings=["verification gate: verification reports no unresolved critical finding"],
    )
    answered: list[str | None] = []

    def park() -> None:
        answered.append(team_api._ask_human(run_id, question))

    waiter = threading.Thread(target=park)
    waiter.start()
    # The run advertises the pause and the problem while it is parked.
    for _ in range(200):
        if client.get(f"/api/team/runs/{run_id}").json()["status"] == "awaiting_human":
            break
        time.sleep(0.01)
    parked = client.get(f"/api/team/runs/{run_id}").json()
    assert parked["status"] == "awaiting_human"
    assert parked["question"]["stage"] == "verification"
    assert parked["question"]["findings"] == question.findings

    response = client.post(
        f"/api/team/runs/{run_id}/answer", json={"answer": "Add an AMS1117-3.3 regulator on VBUS."}
    )
    waiter.join(timeout=5)
    assert response.json() == {
        "run_id": run_id,
        "accepted": True,
        "answers": ["Add an AMS1117-3.3 regulator on VBUS."],
        "resumed": "repair",
    }
    # The orchestrator's hook got the answer back, and the run is running again.
    assert answered == ["Add an AMS1117-3.3 regulator on VBUS."]
    resumed = client.get(f"/api/team/runs/{run_id}").json()
    assert resumed["status"] == "running"
    assert resumed["question"] is None


def test_a_pause_nobody_answers_times_out_and_carries_on(client, store, monkeypatch) -> None:
    """The wait holds a worker thread, so it cannot be unbounded."""
    import app.api.team as team_api
    from app.config import settings
    from app.team.schemas import InterventionRequest

    monkeypatch.setattr(team_api, "store", store)
    monkeypatch.setattr(team_api._executor, "submit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings, "team_human_timeout_seconds", 0)
    run_id = client.post(
        "/api/team/runs", json={"project": "esp32_i2c_demo", "request": "Validate the fixture"}
    ).json()["run_id"]

    answer = team_api._ask_human(
        run_id,
        InterventionRequest(
            run_id=run_id, stage="verification", stage_name="Verification", problem="stuck"
        ),
    )
    # No answer means the run carries on exactly as it would with nobody watching.
    assert answer is None
    state = client.get(f"/api/team/runs/{run_id}").json()
    assert state["status"] == "running"
    assert state["question"] is None


def test_a_run_started_through_the_api_installs_the_intervention_hook(
    client, store, monkeypatch, tmp_path
) -> None:
    """The seam between the two halves of the human-in-the-loop path.

    The orchestrator side is covered by its own tests and the parked-run
    handshake by the one above, but the line that joins them lives in
    `_run_team` - and every other API test stubs the executor out before it runs.
    Nothing asserted that a run started through POST /runs was asked to ask.
    """
    import app.api.team as team_api

    monkeypatch.setattr(team_api, "store", store)
    monkeypatch.setattr(team_api._executor, "submit", lambda *_args, **_kwargs: None)
    run_id = client.post(
        "/api/team/runs", json={"project": "esp32_i2c_demo", "request": "Validate the fixture"}
    ).json()["run_id"]

    installed: list[object] = []

    class _Recorder(team_api.TeamOrchestrator):
        def __init__(self, runner, **kwargs):
            installed.append(kwargs["options"].intervention)
            super().__init__(runner, **kwargs)

        def run(self, project):  # type: ignore[override]
            return TeamRunReport(
                run_id=run_id,
                project=ProjectSpec(project_id="demo", current_stage="team"),
                request="",
                release_status="needs_human_review",
                summary="needs_human_review",
            )

    monkeypatch.setattr(team_api, "TeamOrchestrator", _Recorder)
    session = store.create("esp32_i2c_demo")
    team_api._run_team(
        run_id, team_api.TeamRunRequest(project="esp32_i2c_demo"), session.project_dir
    )

    assert len(installed) == 1
    hook = installed[0]
    assert hook is not None
    # And it is wired to this run: parking through it publishes this run's question.
    monkeypatch.setattr(team_api.settings, "team_human_timeout_seconds", 0)
    assert (
        hook(
            team_api.InterventionRequest(
                run_id=run_id, stage="verification", stage_name="Verification", problem="stuck"
            )
        )
        is None
    )
    assert client.get(f"/api/team/runs/{run_id}").json()["status"] == "running"
