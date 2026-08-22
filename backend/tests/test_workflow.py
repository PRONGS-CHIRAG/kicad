from __future__ import annotations

from app.benchmark import MisconnectingExecutor
from app.execution.local import LocalExecutor
from app.kicad import board
from app.kicad.checkpoint import hash_tree
from app.models import ActionPlan, Decision
from app.workflow import SessionStore

INSTRUCTION = (
    "Connect U1 and U2 using I2C with 3.3V logic. Add 4.7k pull-ups if missing. "
    "Do not modify J1 or the USB circuit."
)


def _approved_plan(store, session) -> ActionPlan:
    plan, problems, _ = store.plan(session, ["U1", "U2"], INSTRUCTION)
    assert isinstance(plan, ActionPlan) and problems == []
    return plan


def test_successful_batch_is_accepted(store, requires_kicad: None) -> None:
    session = store.create("esp32_i2c_demo")
    report = store.execute(session, _approved_plan(store, session))

    assert report.decision == Decision.ACCEPTED
    assert (
        report.validation.requested_connections_created == report.validation.requested_connections_total == 6
    )
    assert report.validation.unexpected_changes == 0
    assert report.validation.protected_modified is False

    state = store.refresh(session)
    assert state.pin_nets["U2.3"] == "I2C_SDA"
    assert state.pin_nets["U1.3"] == "I2C_SDA"
    assert state.pin_nets["U2.1"] == "+3V3"
    pullups = [ref for ref in state.components if ref.startswith("R")]
    assert len(pullups) == 2


def test_erc_errors_decrease_after_a_successful_batch(store) -> None:
    session = store.create("esp32_i2c_demo")
    before_errors = session.baseline_erc.errors
    report = store.execute(session, _approved_plan(store, session))
    if not report.validation.erc_after or not report.validation.erc_after.ran:
        return  # kicad-cli not installed in this environment
    assert report.validation.erc_after.errors < before_errors
    assert not report.validation.violation_diff.new_critical


def test_wrong_connection_is_rejected_and_restored(store) -> None:
    session = store.create("esp32_i2c_demo")
    plan = _approved_plan(store, session)
    before = hash_tree(session.project_dir)

    report = store.execute(session, plan, executor=MisconnectingExecutor())

    assert report.decision == Decision.REJECTED_AND_RESTORED
    assert report.restoration_verified is True
    assert report.changes == []
    assert hash_tree(session.project_dir) == before


def test_partial_execution_is_rejected_and_restored(store) -> None:
    session = store.create("esp32_i2c_demo")
    plan = _approved_plan(store, session)
    before = hash_tree(session.project_dir)

    report = store.execute(session, plan, executor=LocalExecutor(max_actions=3))

    assert report.decision == Decision.REJECTED_AND_RESTORED
    assert report.execution.completed is False
    assert report.restoration_verified is True
    assert hash_tree(session.project_dir) == before


def test_place_footprint_moves_the_pullup_next_to_the_target(store, requires_kicad: None) -> None:
    session = store.create("esp32_i2c_demo")
    plan, problems, _ = store.plan(
        session,
        ["U1", "U2"],
        "Connect U1 and U2 using I2C with 3.3V logic. Add 4.7k pull-ups near U1.",
    )
    assert isinstance(plan, ActionPlan) and problems == []
    assert any(a.type.value == "place_footprint" for a in plan.actions)

    report = store.execute(session, plan)

    assert report.decision == Decision.ACCEPTED
    moved = [change for change in report.changes if change.startswith("moved")]
    assert moved, f"no placement was reported: {report.changes}"

    doc = board.load(session.board_path)
    for change in moved:
        _, reference, _, _, near = change.split()
        target_extent = board._footprint_extent(board.footprint_by_reference(doc, near))
        moved_extent = board._footprint_extent(board.footprint_by_reference(doc, reference))
        assert target_extent is not None and moved_extent is not None


def test_session_survives_a_backend_restart(store) -> None:
    """A fresh SessionStore over the same workspace must recover the session, not lose it.

    Nothing but `_sessions`, a plain in-memory dict, used to hold this state -
    a process restart dropped every in-flight session even though its working
    copy and checkpoint were untouched on disk.
    """
    session = store.create("esp32_i2c_demo")
    plan = _approved_plan(store, session)
    report = store.execute(session, plan, executor=MisconnectingExecutor())

    restarted = SessionStore(cli=store.cli)
    recovered = restarted.get(session.id)

    assert recovered.id == session.id
    assert recovered.revision == session.revision
    assert recovered.history == session.history
    assert recovered.report is not None
    assert recovered.report.decision == report.decision == Decision.REJECTED_AND_RESTORED
    assert recovered.baseline_erc == session.baseline_erc
    assert recovered.state.pin_nets == session.state.pin_nets


def test_execution_is_idempotent_on_an_already_connected_project(store, requires_kicad: None) -> None:
    session = store.create("esp32_i2c_connected")
    plan = _approved_plan(store, session)
    report = store.execute(session, plan)

    assert report.decision == Decision.ACCEPTED
    assert not [step for step in report.execution.steps if step.status == "failed"]
    state = store.refresh(session)
    assert len([ref for ref in state.components if ref.startswith("R")]) == 2
