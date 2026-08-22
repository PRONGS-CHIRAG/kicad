"""Before/after comparison of schematic project state."""

from __future__ import annotations

from ..models import StateDiff
from .reader import ProjectState


def diff_states(before: ProjectState, after: ProjectState) -> StateDiff:
    before_components = set(before.components)
    after_components = set(after.components)

    changed_values: dict[str, list[str]] = {}
    for reference in sorted(before_components & after_components):
        old, new = before.components[reference].value, after.components[reference].value
        if old != new:
            changed_values[reference] = [old, new]

    added_pin_nets: dict[str, str] = {}
    removed_pin_nets: dict[str, str] = {}
    changed_pin_nets: dict[str, list[str]] = {}
    for pin_key, net in sorted(after.pin_nets.items()):
        if pin_key not in before.pin_nets:
            added_pin_nets[pin_key] = net
        elif before.pin_nets[pin_key] != net:
            changed_pin_nets[pin_key] = [before.pin_nets[pin_key], net]
    for pin_key, net in sorted(before.pin_nets.items()):
        if pin_key not in after.pin_nets:
            removed_pin_nets[pin_key] = net

    return StateDiff(
        added_components=sorted(after_components - before_components),
        removed_components=sorted(before_components - after_components),
        changed_values=changed_values,
        added_pin_nets=added_pin_nets,
        removed_pin_nets=removed_pin_nets,
        changed_pin_nets=changed_pin_nets,
    )


def touches_protected(diff: StateDiff, protected: list[str]) -> list[str]:
    """References/nets from the protected list that the diff modified."""
    protected_upper = {item.upper() for item in protected}
    hits: list[str] = []

    def matches(token: str) -> str | None:
        upper = token.upper()
        for item in protected_upper:
            if upper == item or upper.startswith(f"{item}."):
                return item
        return None

    for reference in [*diff.added_components, *diff.removed_components, *diff.changed_values]:
        hit = matches(reference)
        if hit:
            hits.append(f"component {reference}")
    for pin_key, net in {**diff.added_pin_nets, **diff.removed_pin_nets}.items():
        if matches(pin_key) or matches(net):
            hits.append(f"pin {pin_key}")
    for pin_key, (old, new) in diff.changed_pin_nets.items():
        if matches(pin_key) or matches(old) or matches(new):
            hits.append(f"pin {pin_key}")
    return sorted(set(hits))
