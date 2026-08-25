"""Strict schemas shared by the plan generator, executor and validation pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class Protocol(str, Enum):
    I2C = "I2C"
    SPI = "SPI"
    UART = "UART"
    GPIO = "GPIO"
    POWER = "POWER"


class ActionType(str, Enum):
    CONNECT_PINS = "connect_pins"
    CONNECT_PIN_TO_NET = "connect_pin_to_net"
    ENSURE_PULLUP = "ensure_pullup"


class ConnectPins(BaseModel):
    id: str
    type: Literal[ActionType.CONNECT_PINS] = ActionType.CONNECT_PINS
    from_pin: str = Field(alias="from")
    to_pin: str = Field(alias="to")
    net_name: str
    purpose: str = ""

    model_config = {"populate_by_name": True}


class ConnectPinToNet(BaseModel):
    id: str
    type: Literal[ActionType.CONNECT_PIN_TO_NET] = ActionType.CONNECT_PIN_TO_NET
    pin: str
    net: str
    purpose: str = ""


class EnsurePullup(BaseModel):
    id: str
    type: Literal[ActionType.ENSURE_PULLUP] = ActionType.ENSURE_PULLUP
    net: str
    to_net: str
    value: str = "4.7k"
    purpose: str = ""


Action = Annotated[ConnectPins | ConnectPinToNet | EnsurePullup, Field(discriminator="type")]


class ActionPlan(BaseModel):
    schema_version: str = SCHEMA_VERSION
    goal: str
    selected_components: list[str]
    protocol: Protocol = Protocol.I2C
    logic_voltage: str = "3.3V"
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    protected_objects: list[str] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)


class PlanAnswers(BaseModel):
    """Answers to earlier clarifications, replayed into the next planning attempt."""

    protocol: str | None = None
    logic_voltage: str | None = None
    peripheral_sda: str | None = None
    peripheral_scl: str | None = None
    controller_sda: str | None = None
    controller_scl: str | None = None
    pullup_value: str | None = None
    #: Signal name -> pin, for every protocol beyond I2C's four legacy scalars.
    #: The generator folds the scalars above into these dicts before using them,
    #: so old clients that only know `controller_sda` keep working unchanged.
    controller_pins: dict[str, str] = Field(default_factory=dict)
    peripheral_pins: dict[str, str] = Field(default_factory=dict)


class Clarification(BaseModel):
    """Returned instead of a plan when the request cannot be resolved safely."""

    question: str
    reason: str
    options: list[str] = Field(default_factory=list)
    answer_key: str | None = None
    """Which `PlanAnswers` field a chosen option fills, or "selection" for a component choice."""


class Violation(BaseModel):
    severity: Literal["error", "warning", "info"]
    type: str
    description: str
    items: list[str] = Field(default_factory=list)
    #: Nets named by this violation's items, when it names any.
    nets: list[str] = Field(default_factory=list)

    def signature(self) -> str:
        # An unconnected-items violation names an arbitrary representative pair
        # drawn from the unconnected cluster, and KiCAD does not pick the same
        # pair on consecutive runs of the same unchanged file. Keying those on
        # the nets involved - which is what the violation is actually about -
        # makes before/after diffing deterministic. Without this, a DRC gate
        # would reject valid changes at random.
        if self.type.endswith("unconnected_items") and self.nets:
            return f"{self.severity}|{self.type}|{'|'.join(self.nets)}"
        return f"{self.severity}|{self.type}|{'|'.join(sorted(self.items))}"


class ErcReport(BaseModel):
    ran: bool
    tool: str = "kicad-cli"
    kicad_version: str | None = None
    errors: int = 0
    warnings: int = 0
    violations: list[Violation] = Field(default_factory=list)
    raw_output: str = ""


class ViolationDiff(BaseModel):
    new: list[Violation] = Field(default_factory=list)
    resolved: list[Violation] = Field(default_factory=list)
    unchanged: list[Violation] = Field(default_factory=list)

    @property
    def new_critical(self) -> list[Violation]:
        return [v for v in self.new if v.severity == "error"]


class StateDiff(BaseModel):
    added_components: list[str] = Field(default_factory=list)
    removed_components: list[str] = Field(default_factory=list)
    changed_values: dict[str, list[str]] = Field(default_factory=dict)
    added_pin_nets: dict[str, str] = Field(default_factory=dict)
    removed_pin_nets: dict[str, str] = Field(default_factory=dict)
    changed_pin_nets: dict[str, list[str]] = Field(default_factory=dict)


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Decision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED_AND_RESTORED = "rejected_and_restored"
    NEEDS_USER_REVIEW = "needs_user_review"


class ValidationReport(BaseModel):
    checks: list[CheckResult] = Field(default_factory=list)
    erc_before: ErcReport | None = None
    erc_after: ErcReport | None = None
    violation_diff: ViolationDiff | None = None
    drc_before: ErcReport | None = None
    drc_after: ErcReport | None = None
    drc_diff: ViolationDiff | None = None
    state_diff: StateDiff | None = None
    files_changed: list[str] = Field(default_factory=list)
    unexpected_files: list[str] = Field(default_factory=list)
    requested_connections_created: int = 0
    requested_connections_total: int = 0
    unexpected_changes: int = 0
    protected_modified: bool = False
    project_readable: bool = True


class ExecutionStep(BaseModel):
    action_id: str
    tool: str
    status: Literal["applied", "skipped", "failed"]
    detail: str = ""


class ExecutionResult(BaseModel):
    completed: bool
    steps: list[ExecutionStep] = Field(default_factory=list)
    error: str | None = None


class RunReport(BaseModel):
    session_id: str
    goal: str
    decision: Decision
    reason: str
    changes: list[str] = Field(default_factory=list)
    restoration_verified: bool | None = None
    plan: ActionPlan
    execution: ExecutionResult
    validation: ValidationReport
    duration_seconds: float = 0.0
