"""Deterministic accept / reject / review engine. The LLM never decides this."""

from __future__ import annotations

from pathlib import Path

from .kicad.board import BoardSync
from .kicad.reader import ProjectState
from .kicad.state import diff_states, touches_protected
from .models import (
    ActionPlan,
    ActionType,
    CheckResult,
    ConnectPins,
    ConnectPinToNet,
    Decision,
    EnsurePullup,
    ErcReport,
    ExecutionResult,
    ValidationReport,
    Violation,
    ViolationDiff,
)

SCHEMATIC_SUFFIXES = {".kicad_sch"}
BOARD_SUFFIXES = {".kicad_pcb"}
# `.kicad_prl` holds local UI state (open sheet, zoom). kicad-cli rewrites it as
# a side effect of running, and it carries no design data, so a change there is
# never evidence that the project was modified.
IGNORED_SUFFIXES = {".kicad_prl"}

# No action type edits the board today. Deriving the flag from a set instead of
# hardcoding False keeps the DRC gate correct the moment a PCB action is added.
PCB_ACTION_TYPES: frozenset[ActionType] = frozenset()


def plan_touches_pcb(plan: ActionPlan) -> bool:
    return any(action.type in PCB_ACTION_TYPES for action in plan.actions)


def _awaiting_routing(violation: Violation, synced_nets: set[str]) -> bool:
    """Is this violation just the board waiting to be routed?

    A board sync assigns nets but lays no copper — precisely the state KiCAD's
    own *Update PCB from Schematic* leaves behind — so the unconnected items it
    produces are the next step for the person, not a regression this run caused.

    Note what this does *not* widen. DRC was never a gate before (PCB_ACTION_TYPES
    was empty, so `touches_pcb` was always false); turning it on with this carve-out
    leaves the app strictly more verified than it was, because clearance and
    crossing regressions on the board now reject where they previously went
    unremarked. The exemption is therefore the boundary of a new guarantee, not a
    hole in an old one — and it is kept as narrow as it can be: only unconnected
    items, and only when *every* net the violation names was touched by this sync.
    An unconnected item naming any other net still rejects, so severing existing
    copper cannot slip through.
    """
    return (
        violation.type.endswith("unconnected_items")
        and bool(violation.nets)
        and set(violation.nets) <= synced_nets
    )


def unexpected_file_changes(files_changed: list[str], touches_pcb: bool) -> list[str]:
    """Checkpointed files that changed but that this plan had no business changing."""
    allowed = set(SCHEMATIC_SUFFIXES) | (BOARD_SUFFIXES if touches_pcb else set())
    return sorted(
        name
        for name in files_changed
        if Path(name).suffix not in allowed and Path(name).suffix not in IGNORED_SUFFIXES
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
    files_changed: list[str] | None = None,
    drc_before: ErcReport | None = None,
    drc_after: ErcReport | None = None,
    drc_diff: ViolationDiff | None = None,
    board_sync: BoardSync | None = None,
) -> tuple[Decision, str, ValidationReport]:
    checks: list[CheckResult] = []
    # A run that synced the board wrote the layout even though no action type
    # names it, so the board counts as touched: `.kicad_pcb` becomes an expected
    # change and DRC becomes a gate rather than a report.
    touches_pcb = plan_touches_pcb(plan) or bool(board_sync and board_sync.changed)
    report = ValidationReport(
        erc_before=erc_before,
        erc_after=erc_after,
        violation_diff=violation_diff,
        drc_before=drc_before,
        drc_after=drc_after,
        drc_diff=drc_diff,
        files_changed=sorted(files_changed or []),
    )

    # Snapshotted before the post-execution ERC ran, so kicad-cli's own writes
    # are not mistaken for changes the executor made.
    report.unexpected_files = unexpected_file_changes(report.files_changed, touches_pcb)
    checks.append(
        CheckResult(
            name="only_expected_files_changed",
            passed=not report.unexpected_files,
            detail=(
                f"unexpected file changes: {', '.join(report.unexpected_files)}"
                if report.unexpected_files
                else f"{len(report.files_changed)} expected file(s) changed"
            ),
        )
    )

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

    # DRC is only a gate when the plan actually edits the board (§17). Reporting
    # it as informational otherwise surfaces real findings - such as the
    # schematic/PCB parity errors an added resistor creates - without inventing
    # a rejection path for plans that never touch the layout.
    if drc_after is not None and drc_after.ran:
        synced_nets = board_sync.touched_nets if board_sync else set()
        pending = [v for v in (drc_diff.new if drc_diff else []) if _awaiting_routing(v, synced_nets)]
        new_drc = [v for v in (drc_diff.new_critical if drc_diff else []) if v not in pending]
        new_minor = [
            v
            for v in (drc_diff.new if drc_diff else [])
            if v.severity != "error" and v not in pending
        ]
        kinds = sorted({v.type for v in new_drc})
        minor_kinds = sorted({v.type for v in new_minor})
        if new_drc and touches_pcb:
            detail = "; ".join(f"{v.type}: {v.description}" for v in new_drc)
        elif new_drc:
            detail = (
                f"informational: {len(new_drc)} new DRC error(s) ({', '.join(kinds)}); this plan changes "
                "only the schematic, so the board needs re-syncing in KiCAD"
            )
        elif new_minor:
            # Surfaced rather than swallowed: a symbol added to the schematic has
            # no footprint on the board yet, which parity reports as a warning.
            detail = (
                f"no new DRC errors; {len(new_minor)} new non-critical DRC violation(s) "
                f"({', '.join(minor_kinds)})"
            )
        else:
            detail = "no new DRC errors"
        if pending:
            # Reported, never hidden: the board was synced and now awaits copper.
            routes = ", ".join(sorted({net for v in pending for net in v.nets}))
            detail += f"; {routes} synced to the board and still need routing"
        checks.append(
            CheckResult(
                name="no_new_critical_drc_violations",
                passed=not (new_drc and touches_pcb),
                detail=detail,
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
