"""Deterministic accept / reject / review engine. The LLM never decides this."""

from __future__ import annotations

from .kicad.reader import ProjectState
from .kicad.state import diff_states, touches_protected
from .models import (
    ActionPlan,
    CheckResult,
    ConnectPins,
    ConnectPinToNet,
    Decision,
    EnsurePullup,
    ErcReport,
    ExecutionResult,
    ValidationReport,
    ViolationDiff,
)


def expected_pin_nets(plan: ActionPlan, before: ProjectState) -> dict[str, str]:
    """Pin -> net assignments the approved plan is allowed to create."""
    expected: dict[str, str] = {}
    for action in plan.actions:
        if isinstance(action, ConnectPins):
            for pin_ref in (action.from_pin, action.to_pin):
                reference, _, pin_key = pin_ref.partition(".")
                component = before.components.get(reference)
                pin = component.pin(pin_key) if component else None
                if pin is not None:
                    expected[f"{reference}.{pin.number}"] = action.net_name
        elif isinstance(action, ConnectPinToNet):
            reference, _, pin_key = action.pin.partition(".")
            component = before.components.get(reference)
            pin = component.pin(pin_key) if component else None
            if pin is not None:
                expected[f"{reference}.{pin.number}"] = action.net
    return expected


def _pullup_nets(plan: ActionPlan) -> list[tuple[str, str]]:
    return [(a.net, a.to_net) for a in plan.actions if isinstance(a, EnsurePullup)]


def evaluate(
    plan: ActionPlan,
    execution: ExecutionResult,
    before: ProjectState,
    after: ProjectState | None,
    erc_before: ErcReport,
    erc_after: ErcReport | None,
    violation_diff: ViolationDiff | None,
) -> tuple[Decision, str, ValidationReport]:
    checks: list[CheckResult] = []
    report = ValidationReport(erc_before=erc_before, erc_after=erc_after, violation_diff=violation_diff)

    checks.append(
        CheckResult(
            name="execution_completed",
            passed=execution.completed,
            detail=execution.error or f"{len(execution.steps)} action(s) executed",
        )
    )

    readable = after is not None
    report.project_readable = readable
    checks.append(
        CheckResult(
            name="project_readable",
            passed=readable,
            detail="schematic parsed after execution" if readable else "schematic could not be parsed",
        )
    )

    if after is None:
        report.checks = checks
        return Decision.REJECTED_AND_RESTORED, "The modified project could not be read back.", report

    state_diff = diff_states(before, after)
    report.state_diff = state_diff

    expected = expected_pin_nets(plan, before)
    created = {pin: net for pin, net in expected.items() if after.pin_nets.get(pin) == net}
    report.requested_connections_total = len(expected)
    report.requested_connections_created = len(created)
    missing = sorted(set(expected) - set(created))
    checks.append(
        CheckResult(
            name="requested_connections_created",
            passed=not missing,
            detail=f"{len(created)}/{len(expected)} created"
            + (f"; missing {', '.join(missing)}" if missing else ""),
        )
    )

    pullup_problems: list[str] = []
    for net, rail in _pullup_nets(plan):
        found = False
        for reference, component in after.components.items():
            if component.is_power or not reference.startswith("R"):
                continue
            nets = {after.pin_nets.get(f"{reference}.{pin.number}") for pin in component.pins}
            if net in nets and rail in nets:
                found = True
                break
        if not found:
            pullup_problems.append(f"{net} has no pull-up to {rail}")
    checks.append(
        CheckResult(
            name="supporting_components_present",
            passed=not pullup_problems,
            detail="; ".join(pullup_problems) or "all required pull-ups present",
        )
    )

    protected_hits = touches_protected(state_diff, plan.protected_objects)
    report.protected_modified = bool(protected_hits)
    checks.append(
        CheckResult(
            name="protected_objects_preserved",
            passed=not protected_hits,
            detail="; ".join(protected_hits) or "no protected object changed",
        )
    )

    allowed_new_components = {net for net, _ in _pullup_nets(plan)}
    unexpected: list[str] = []
    for pin, net in {**state_diff.added_pin_nets}.items():
        if expected.get(pin) == net:
            continue
        reference = pin.split(".")[0]
        if reference in state_diff.added_components and allowed_new_components:
            continue
        unexpected.append(f"{pin} joined {net}")
    for pin, (old, new) in state_diff.changed_pin_nets.items():
        if expected.get(pin) != new:
            unexpected.append(f"{pin} moved from {old} to {new}")
    for pin, net in state_diff.removed_pin_nets.items():
        unexpected.append(f"{pin} left {net}")
    for reference in state_diff.added_components:
        if not (reference.startswith("R") and allowed_new_components):
            unexpected.append(f"component {reference} was added")
    for reference in state_diff.removed_components:
        unexpected.append(f"component {reference} was removed")
    for reference, (old, new) in state_diff.changed_values.items():
        unexpected.append(f"{reference} value changed from {old} to {new}")
    report.unexpected_changes = len(unexpected)
    checks.append(
        CheckResult(
            name="no_unauthorized_changes",
            passed=not unexpected,
            detail="; ".join(unexpected) or "only approved changes were applied",
        )
    )

    erc_conclusive = erc_before.ran and erc_after is not None and erc_after.ran
    new_critical = violation_diff.new_critical if violation_diff else []
    checks.append(
        CheckResult(
            name="no_new_critical_erc_violations",
            passed=not new_critical,
            detail=(
                "; ".join(f"{v.type}: {v.description}" for v in new_critical)
                if new_critical
                else ("no new ERC errors" if erc_conclusive else "ERC could not be run")
            ),
        )
    )

    report.checks = checks
    failed = [check for check in checks if not check.passed]
    if failed:
        reason = "; ".join(f"{check.name}: {check.detail}" for check in failed)
        return Decision.REJECTED_AND_RESTORED, reason, report
    if not erc_conclusive:
        return (
            Decision.NEEDS_USER_REVIEW,
            "All structural checks passed but KiCAD ERC could not be run, so the result is unverified.",
            report,
        )
    resolved = len(violation_diff.resolved) if violation_diff else 0
    return (
        Decision.ACCEPTED,
        f"All {report.requested_connections_created} approved connections were created, "
        f"no unauthorized changes were made and no new critical ERC violations were introduced "
        f"({resolved} pre-existing violation(s) resolved).",
        report,
    )
