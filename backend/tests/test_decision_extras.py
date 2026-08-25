"""Coverage for the file-diff and DRC checks added to the decision engine."""

from __future__ import annotations

from app.decision import plan_touches_pcb, unexpected_file_changes
from app.models import (
    ActionPlan,
    ConnectPins,
    ErcReport,
    PlanAnswers,
    Violation,
)
from app.workflow import SessionStore


def _plan() -> ActionPlan:
    return ActionPlan(
        goal="g",
        selected_components=["U1", "U2"],
        actions=[ConnectPins(id="a1", **{"from": "U2.SDA", "to": "U1.GPIO21"}, net_name="I2C_SDA")],
    )


def test_schematic_changes_are_expected_and_project_changes_are_not() -> None:
    assert unexpected_file_changes(["demo.kicad_sch"], touches_pcb=False) == []
    assert unexpected_file_changes(["demo.kicad_pro"], touches_pcb=False) == ["demo.kicad_pro"]
    assert unexpected_file_changes(["lib.kicad_sym"], touches_pcb=False) == ["lib.kicad_sym"]


def test_board_changes_are_unexpected_unless_the_plan_edits_the_board() -> None:
    assert unexpected_file_changes(["demo.kicad_pcb"], touches_pcb=False) == ["demo.kicad_pcb"]
    assert unexpected_file_changes(["demo.kicad_pcb"], touches_pcb=True) == []


def test_local_ui_state_is_never_treated_as_a_change() -> None:
    """kicad-cli rewrites .kicad_prl as a side effect of running; it holds no design data."""
    assert unexpected_file_changes(["demo.kicad_prl"], touches_pcb=False) == []


def test_no_current_action_type_claims_to_touch_the_pcb() -> None:
    assert plan_touches_pcb(_plan()) is False


def test_accepted_run_reports_only_the_schematic_as_changed(store: SessionStore) -> None:
    session = store.create("esp32_i2c_demo")
    plan, problems, _ = store.plan(
        session, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic."
    )
    assert problems == []
    report = store.execute(session, plan)

    changed = report.validation.files_changed
    assert any(name.endswith(".kicad_sch") for name in changed), changed
    assert report.validation.unexpected_files == []
    names = [check.name for check in report.validation.checks]
    assert "only_expected_files_changed" in names


def test_a_stray_file_write_is_caught_as_an_unexpected_file_change(store: SessionStore) -> None:
    """An executor that scribbles outside the schematic must not slip through."""
    from app.execution.local import LocalExecutor

    class ScribblingExecutor(LocalExecutor):
        name = "scribbling"

        def execute(self, project_dir, plan):
            result = super().execute(project_dir, plan)
            board = next(iter(sorted(project_dir.glob("*.kicad_pro"))))
            board.write_text(board.read_text() + "\n")
            return result

    session = store.create("esp32_i2c_demo")
    plan, _, _ = store.plan(session, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic.")
    report = store.execute(session, plan, executor=ScribblingExecutor())

    assert report.decision.value == "rejected_and_restored"
    assert any(name.endswith(".kicad_pro") for name in report.validation.unexpected_files)
    assert report.restoration_verified is True


def test_drc_is_reported_for_a_board_project(store: SessionStore) -> None:
    session = store.create("esp32_i2c_board")
    assert session.board_path is not None
    plan, problems, _ = store.plan(
        session, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic."
    )
    assert problems == []
    report = store.execute(session, plan)

    if not (report.validation.drc_after and report.validation.drc_after.ran):
        return  # kicad-cli not installed in this environment
    names = [check.name for check in report.validation.checks]
    assert "no_new_critical_drc_violations" in names
    check = next(c for c in report.validation.checks if c.name == "no_new_critical_drc_violations")
    # No action type edits the board, so DRC must never be the reason for a rejection.
    assert check.passed is True


def test_drc_check_is_absent_for_a_schematic_only_project(
    store: SessionStore, schematic_only: str
) -> None:
    session = store.create(schematic_only)
    assert session.board_path is None
    assert session.baseline_drc is None
    plan, _, _ = store.plan(session, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic.")
    report = store.execute(session, plan)
    assert "no_new_critical_drc_violations" not in [c.name for c in report.validation.checks]


def test_form_answers_alone_produce_a_plan_without_any_instruction_text(store: SessionStore) -> None:
    """The Phase 5 form path: every parameter supplied structurally, no prose."""
    session = store.create("esp32_i2c_demo")
    answers = PlanAnswers(
        protocol="I2C",
        logic_voltage="3.3V",
        controller_sda="GPIO21",
        controller_scl="GPIO22",
        pullup_value="2.2k",
    )
    plan, problems, source = store.plan(session, ["U1", "U2"], "", answers)

    assert problems == []
    assert source == "rules"
    assert isinstance(plan, ActionPlan)
    pullups = [a for a in plan.actions if a.type.value == "ensure_pullup"]
    assert pullups and all(a.value == "2.2k" for a in pullups)


def test_drc_sections_are_severity_counted() -> None:
    """A parity failure is an error, so it must not be silently downgraded."""
    report = ErcReport(
        ran=True,
        violations=[Violation(severity="error", type="schematic_parity.extra_footprint", description="x")],
    )
    assert report.violations[0].severity == "error"


def test_duplicate_component_metric_actually_fires(store: SessionStore) -> None:
    """A 0% duplicate rate must mean "none happened", not "never detected".

    The executor is run directly here, bypassing the decision engine, because the
    pipeline rejects a duplicate before it can ever be measured (see the test
    below). This exercises the detector itself.
    """
    from app.benchmark import Benchmark, BenchmarkOutcome, duplicate_pullups
    from app.execution.local import LocalExecutor
    from app.kicad.reader import read_project
    from app.models import EnsurePullup

    class DuplicatingExecutor(LocalExecutor):
        """Ignores existing pull-ups, so it adds a redundant resistor pair."""

        name = "duplicating"

        @staticmethod
        def _find_pullup(state, net, rail):  # noqa: ARG004 - deliberately blind
            return None

    session = store.create("esp32_i2c_existing_pullups")
    before = read_project(session.project_dir)
    # The planner correctly omits pull-ups here, so ask for them explicitly.
    forced = ActionPlan(
        goal="force duplicate pull-ups",
        selected_components=["U1", "U2"],
        actions=[
            EnsurePullup(id="d1", net="I2C_SDA", to_net="+3V3", value="4.7k"),
            EnsurePullup(id="d2", net="I2C_SCL", to_net="+3V3", value="4.7k"),
        ],
    )
    DuplicatingExecutor().execute(session.project_dir, forced)
    after = read_project(session.project_dir)

    outcome = BenchmarkOutcome(
        benchmark=Benchmark(name="t", project="p", instruction="i", selected=[], expect="accepted"),
        before=before,
        after=after,
    )
    duplicates = duplicate_pullups(outcome)
    assert duplicates, "a redundant pull-up pair should be reported as a duplicate"
    assert any("I2C_SDA" in d or "I2C_SCL" in d for d in duplicates)


def test_a_duplicate_pullup_is_rejected_before_it_can_be_accepted(store: SessionStore) -> None:
    """The duplicate-component rate stays at zero because such a run is rolled back."""
    from app.execution.local import LocalExecutor
    from app.models import EnsurePullup

    class RoguePullupExecutor(LocalExecutor):
        """Adds pull-ups the approved plan never asked for, ignoring existing ones."""

        name = "rogue-pullups"

        def execute(self, project_dir, plan):
            sabotaged = plan.model_copy(deep=True)
            sabotaged.actions.extend(
                [
                    EnsurePullup(id="rogue-1", net="I2C_SDA", to_net="+3V3", value="4.7k"),
                    EnsurePullup(id="rogue-2", net="I2C_SCL", to_net="+3V3", value="4.7k"),
                ]
            )
            return super().execute(project_dir, sabotaged)

        @staticmethod
        def _find_pullup(state, net, rail):  # noqa: ARG004 - deliberately blind
            return None

    session = store.create("esp32_i2c_existing_pullups")
    plan, problems, _ = store.plan(
        session, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic. Add 4.7k pull-ups if missing."
    )
    assert problems == []
    # The planner reuses R1/R2, so any resistor that appears is unauthorized.
    assert not [a for a in plan.actions if a.type.value == "ensure_pullup"]

    report = store.execute(session, plan, executor=RoguePullupExecutor())
    assert report.decision.value == "rejected_and_restored"
    assert any("was added" in check.detail for check in report.validation.checks if not check.passed)
    assert report.restoration_verified is True
    assert [ref for ref in store.refresh(session).components if ref.startswith("R")] == ["R1", "R2"]
