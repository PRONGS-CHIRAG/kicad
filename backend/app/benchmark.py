"""Repeatable benchmark suite for the ten defined KiCAD tasks."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .execution.base import Executor
from .execution.local import LocalExecutor
from .kicad.reader import ProjectState
from .models import ActionPlan, Clarification, Decision, ExecutionResult, RunReport
from .workflow import Session, SessionStore


class MisconnectingExecutor(LocalExecutor):
    """Executor that applies different wiring than the approved plan.

    This models a real risk for this architecture, not an arbitrary fault: the
    Mitos MCP executor's tool mapping is unverified (`DEFAULT_TOOL_MAP` in
    execution/mitos.py, see docs/mitos.md), so an execution backend applying
    something other than what was approved is a plausible failure, not a
    hypothetical one. It ties every signal net to GND as a concrete stand-in
    for "the backend did something other than the approved plan."
    """

    name = "misconnecting"

    def execute(self, project_dir: Path, plan: ActionPlan) -> ExecutionResult:
        sabotaged = plan.model_copy(deep=True)
        for action in sabotaged.actions:
            if action.type.value == "connect_pins":
                action.net_name = "GND"
        return super().execute(project_dir, sabotaged)


@dataclass
class Benchmark:
    name: str
    project: str
    instruction: str
    selected: list[str]
    expect: str  # "accepted" | "rejected_and_restored" | "clarification"
    check: Callable[[BenchmarkOutcome], str | None] | None = None
    executor: Executor | None = None


@dataclass
class BenchmarkOutcome:
    benchmark: Benchmark
    passed: bool = False
    detail: str = ""
    duration_seconds: float = 0.0
    plan: ActionPlan | None = None
    clarification: Clarification | None = None
    report: RunReport | None = None
    before: ProjectState | None = None
    after: ProjectState | None = None
    problems: list[str] = field(default_factory=list)
    check_ran: bool = False
    check_detail: str | None = None
    duplicates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.benchmark.name,
            "project": self.benchmark.project,
            "instruction": self.benchmark.instruction,
            "expected": self.benchmark.expect,
            "passed": self.passed,
            "detail": self.detail,
            "duration_seconds": round(self.duration_seconds, 3),
            "decision": self.report.decision.value if self.report else None,
            "reason": self.report.reason if self.report else None,
            "clarification": self.clarification.model_dump() if self.clarification else None,
            "plan_actions": len(self.plan.actions) if self.plan else 0,
            "unexpected_changes": self.report.validation.unexpected_changes if self.report else 0,
            "restoration_verified": self.report.restoration_verified if self.report else None,
            "planning_correct": planning_correct(self),
            "check_ran": self.check_ran,
            "check_detail": self.check_detail,
            "duplicate_components": self.duplicates,
            "unexpected_files": self.report.validation.unexpected_files if self.report else [],
        }


def planning_correct(outcome: BenchmarkOutcome) -> bool:
    """Whether the planning stage alone did the right thing, regardless of execution."""
    if outcome.benchmark.expect == "clarification":
        return outcome.clarification is not None or bool(outcome.problems)
    return outcome.plan is not None and not outcome.problems


def _resistor_nets(state: ProjectState) -> dict[str, frozenset[str]]:
    """reference -> the pair of nets each two-terminal resistor bridges."""
    pairs: dict[str, frozenset[str]] = {}
    for reference, component in state.components.items():
        if component.is_power or not reference.startswith("R"):
            continue
        nets = frozenset(
            net
            for net in (state.pin_nets.get(f"{reference}.{pin.number}") for pin in component.pins)
            if net
        )
        if len(nets) == 2:
            pairs[reference] = nets
    return pairs


def duplicate_pullups(outcome: BenchmarkOutcome) -> list[str]:
    """Resistors this run added that bridge a net pair already bridged before it ran."""
    if outcome.before is None or outcome.after is None:
        return []
    before = _resistor_nets(outcome.before)
    after = _resistor_nets(outcome.after)
    already = set(before.values())
    return [
        f"{reference} duplicates an existing pull-up across {' / '.join(sorted(nets))}"
        for reference, nets in sorted(after.items())
        if reference not in before and nets in already
    ]


def _no_new_components(outcome: BenchmarkOutcome) -> str | None:
    if outcome.report is None:
        return "no execution report"
    added = (
        outcome.report.validation.state_diff.added_components if outcome.report.validation.state_diff else []
    )
    return f"unexpected new components: {added}" if added else None


def _usb_untouched(outcome: BenchmarkOutcome) -> str | None:
    if outcome.after is None:
        return "no post-execution state"
    for pin, net in outcome.after.pin_nets.items():
        if pin.startswith("J1.") and outcome.before and outcome.before.pin_nets.get(pin) != net:
            return f"protected pin {pin} changed"
    return None


def _baseline_warnings_preserved(outcome: BenchmarkOutcome) -> str | None:
    report = outcome.report
    if report is None or report.validation.violation_diff is None:
        return "no violation diff"
    resolved_warnings = [v for v in report.validation.violation_diff.resolved if v.severity == "warning"]
    unrelated = [v for v in resolved_warnings if "lib_symbol" in v.type]
    return f"baseline library warnings disappeared unexpectedly: {len(unrelated)}" if unrelated else None


def _restored(outcome: BenchmarkOutcome) -> str | None:
    if outcome.report is None:
        return "no execution report"
    if outcome.report.restoration_verified is not True:
        return "checkpoint restoration was not verified"
    if outcome.before and outcome.after and outcome.before.to_dict() != outcome.after.to_dict():
        return "project state differs from the checkpoint"
    return None


BENCHMARKS: list[Benchmark] = [
    Benchmark(
        name="1. Standard I2C connection",
        project="esp32_i2c_demo",
        instruction="Connect U1 and U2 using I2C with 3.3V logic.",
        selected=["U1", "U2"],
        expect="accepted",
    ),
    Benchmark(
        name="2. Existing pull-ups are reused",
        project="esp32_i2c_existing_pullups",
        instruction="Connect U1 and U2 using I2C with 3.3V logic. Add 4.7k pull-ups if they are missing.",
        selected=["U1", "U2"],
        expect="accepted",
        check=_no_new_components,
    ),
    Benchmark(
        name="3. Missing pin names",
        project="esp32_i2c_unnamed_pins",
        instruction="Connect U1 and U2 using I2C with 3.3V logic.",
        selected=["U1", "U2"],
        expect="clarification",
    ),
    Benchmark(
        name="4. Wrong voltage request",
        project="esp32_i2c_demo",
        instruction="Connect U1 and U2 using I2C with 5V logic.",
        selected=["U1", "U2"],
        expect="clarification",
    ),
    Benchmark(
        name="5. Protected circuit",
        project="esp32_i2c_demo",
        instruction=("Connect U1 and U2 using I2C with 3.3V logic. Do not modify J1 or the USB circuit."),
        selected=["U1", "U2"],
        expect="accepted",
        check=_usb_untouched,
    ),
    Benchmark(
        name="6. Pre-existing ERC warnings are not blamed",
        project="esp32_i2c_demo",
        instruction="Connect U1 and U2 using I2C with 3.3V logic.",
        selected=["U1", "U2"],
        expect="accepted",
        check=_baseline_warnings_preserved,
    ),
    Benchmark(
        name="7. Deliberately wrong connection",
        project="esp32_i2c_demo",
        instruction="Connect U1 and U2 using I2C with 3.3V logic.",
        selected=["U1", "U2"],
        expect="rejected_and_restored",
        check=_restored,
        executor=MisconnectingExecutor(),
    ),
    Benchmark(
        name="8. Partial execution failure",
        project="esp32_i2c_demo",
        instruction="Connect U1 and U2 using I2C with 3.3V logic.",
        selected=["U1", "U2"],
        expect="rejected_and_restored",
        check=_restored,
        executor=LocalExecutor(max_actions=3),
    ),
    Benchmark(
        name="9. Unknown component",
        project="esp32_i2c_demo",
        instruction="Connect U1 and U99 using I2C with 3.3V logic.",
        selected=["U1", "U99"],
        expect="clarification",
    ),
    Benchmark(
        name="10. Ambiguous instruction",
        project="esp32_i2c_demo",
        instruction="Connect the sensor properly.",
        selected=["U1", "U2"],
        expect="clarification",
    ),
]


def run_benchmark(benchmark: Benchmark, store: SessionStore) -> BenchmarkOutcome:
    outcome = BenchmarkOutcome(benchmark=benchmark)
    started = time.perf_counter()
    session: Session = store.create(benchmark.project)
    outcome.before = session.state

    result, problems, _ = store.plan(session, benchmark.selected, benchmark.instruction)
    outcome.problems = problems
    if isinstance(result, Clarification):
        outcome.clarification = result
        outcome.passed = benchmark.expect == "clarification"
        outcome.detail = result.question if outcome.passed else f"unexpected clarification: {result.question}"
        outcome.duration_seconds = time.perf_counter() - started
        return outcome

    outcome.plan = result
    if problems:
        outcome.passed = benchmark.expect == "clarification"
        outcome.detail = f"plan rejected by validator: {problems}"
        outcome.duration_seconds = time.perf_counter() - started
        return outcome
    if benchmark.expect == "clarification":
        outcome.detail = "a plan was produced although clarification was required"
        outcome.duration_seconds = time.perf_counter() - started
        return outcome

    report = store.execute(session, result, executor=benchmark.executor)
    outcome.report = report
    outcome.after = store.refresh(session)
    outcome.duplicates = duplicate_pullups(outcome)

    # The custom check runs unconditionally. Short-circuiting on a decision
    # mismatch used to leave three benchmarks' real assertions never executed,
    # which made them look covered when they were not.
    expected_decision = Decision(benchmark.expect)
    if benchmark.check is not None:
        outcome.check_ran = True
        outcome.check_detail = benchmark.check(outcome)

    if report.decision != expected_decision:
        outcome.detail = f"expected {expected_decision.value}, got {report.decision.value}: {report.reason}"
        if outcome.check_detail:
            outcome.detail += f" | check also failed: {outcome.check_detail}"
    else:
        outcome.passed = outcome.check_detail is None
        outcome.detail = outcome.check_detail or report.reason
    outcome.duration_seconds = time.perf_counter() - started
    return outcome


def run_all(store: SessionStore | None = None) -> list[BenchmarkOutcome]:
    store = store or SessionStore()
    return [run_benchmark(benchmark, store) for benchmark in BENCHMARKS]


def summarize(outcomes: list[BenchmarkOutcome]) -> dict:
    executed = [o for o in outcomes if o.report is not None]
    unsafe = [o for o in outcomes if o.benchmark.expect in {"rejected_and_restored", "clarification"}]
    restored = [o for o in outcomes if o.report and o.report.decision == Decision.REJECTED_AND_RESTORED]
    durations = sorted(o.duration_seconds for o in outcomes)
    median = durations[len(durations) // 2] if durations else 0.0
    corrupted = [o for o in executed if o.report and not o.report.validation.project_readable]
    duplicated = [o for o in executed if o.duplicates]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(outcomes),
        "passed": sum(1 for o in outcomes if o.passed),
        "overall_pass_rate": round(sum(1 for o in outcomes if o.passed) / max(len(outcomes), 1), 3),
        # Planning-stage only: did it produce a validator-clean plan where a plan
        # was expected, and clarify where clarification was expected? Kept
        # separate from the overall pass rate, which also folds in execution.
        "plan_accuracy": round(
            sum(1 for o in outcomes if planning_correct(o)) / max(len(outcomes), 1), 3
        ),
        "execution_success": round(
            sum(1 for o in executed if o.report and o.report.execution.completed) / max(len(executed), 1), 3
        ),
        "unsafe_change_rejection": round(sum(1 for o in unsafe if o.passed) / max(len(unsafe), 1), 3),
        "rollback_success": round(
            sum(1 for o in restored if o.report and o.report.restoration_verified) / max(len(restored), 1), 3
        ),
        "unauthorized_changes": sum(
            o.report.validation.unexpected_changes
            for o in executed
            if o.report and o.report.decision == Decision.ACCEPTED
        ),
        "duplicate_component_rate": round(len(duplicated) / max(len(executed), 1), 3),
        "project_corruption_rate": round(len(corrupted) / max(len(executed), 1), 3),
        "checks_not_run": [o.benchmark.name for o in outcomes if o.benchmark.check and not o.check_ran],
        "median_duration_seconds": round(median, 3),
        "results": [o.to_dict() for o in outcomes],
    }


def render_html(summary: dict) -> str:
    rows = "\n".join(
        f"<tr class='{'pass' if r['passed'] else 'fail'}'>"
        f"<td>{r['name']}</td><td>{r['project']}</td><td>{r['expected']}</td>"
        f"<td>{r['decision'] or ('clarification' if r['clarification'] else '-')}</td>"
        f"<td>{'PASS' if r['passed'] else 'FAIL'}</td><td>{r['duration_seconds']}s</td>"
        f"<td>{r['detail']}</td></tr>"
        for r in summary["results"]
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>KiCAD Mitos benchmark</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #111; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }}
tr.pass td:nth-child(5) {{ color: #087443; font-weight: 600; }}
tr.fail td:nth-child(5) {{ color: #b42318; font-weight: 600; }}
.metrics {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
.metric {{ border: 1px solid #ddd; border-radius: 8px; padding: 0.75rem 1rem; }}
.metric b {{ display: block; font-size: 1.4rem; }}
</style></head><body>
<h1>KiCAD Mitos benchmark</h1>
<p>{summary["generated_at"]}</p>
<div class="metrics">
  <div class="metric"><b>{summary["passed"]}/{summary["total"]}</b>benchmarks passed</div>
  <div class="metric"><b>{summary["unsafe_change_rejection"] * 100:.0f}%</b>unsafe changes rejected</div>
  <div class="metric"><b>{summary["rollback_success"] * 100:.0f}%</b>rollbacks verified</div>
  <div class="metric"><b>{summary["unauthorized_changes"]}</b>unauthorized changes</div>
  <div class="metric"><b>{summary["plan_accuracy"] * 100:.0f}%</b>plan accuracy</div>
  <div class="metric"><b>{summary["duplicate_component_rate"] * 100:.0f}%</b>duplicate components</div>
  <div class="metric"><b>{summary["project_corruption_rate"] * 100:.0f}%</b>project corruption</div>
  <div class="metric"><b>{summary["median_duration_seconds"]}s</b>median duration</div>
</div>
<table><thead><tr><th>Benchmark</th><th>Project</th><th>Expected</th><th>Result</th><th>Status</th><th>Time</th><th>Detail</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>
"""


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    outcomes = run_all()
    summary = summarize(outcomes)
    (args.out / "benchmark.json").write_text(json.dumps(summary, indent=2))
    (args.out / "benchmark.html").write_text(render_html(summary))

    for outcome in outcomes:
        status = "PASS" if outcome.passed else "FAIL"
        print(f"[{status}] {outcome.benchmark.name}: {outcome.detail}")
    print(f"\n{summary['passed']}/{summary['total']} passed -> {args.out}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
