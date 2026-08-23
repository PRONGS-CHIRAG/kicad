"""Typed contracts shared by the ten-agent team."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..kicad.reader import ProjectState
from ..models import ErcReport

SCHEMA_VERSION = "1.0"
# Keep payloads JSON-shaped without importing an unbounded recursive alias into
# Pydantic's schema generator. Nested values are still represented by JSON
# containers and are serialized by the evidence store.
JsonValue = str | int | float | bool | None | list[object] | dict[str, object]


class TeamModel(BaseModel):
    schema_version: str = SCHEMA_VERSION

    model_config = ConfigDict(extra="forbid")


REQUIREMENT_ID_RE = re.compile(r"(PWR|IF|MECH|TEST)-\d{3}")

CATEGORY_BY_PREFIX = {"PWR": "power", "IF": "interface", "MECH": "mechanical", "TEST": "test"}
"""The ID prefix and the category are the same fact, spelled twice."""


class Requirement(BaseModel):
    id: str
    category: Literal["power", "interface", "mechanical", "test"]
    statement: str
    value: str | int | float | bool | None
    unit: str
    source: str = ""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def category_follows_identifier(cls, data: object) -> object:
        """Take the category from the ID prefix rather than from the agent's wording.

        `id` is the load-bearing field: the architect maps blocks to it, the
        verifier reports an outcome per ID, and the report counts them. `category`
        only groups requirements for the gates. So an agent that filed `TEST-001`
        under "quality" named the same thing in its own words, and a real run did
        exactly that three times over - burning three two-minute sessions on a
        synonym. The prefix decides, and the gate is spent on electrical content
        instead. An ID that does not match the pattern is left alone for the field
        validators to reject.
        """
        if not isinstance(data, dict):
            return data
        identifier = str(data.get("id", "")).strip().upper()
        match = REQUIREMENT_ID_RE.fullmatch(identifier)
        if match is None:
            return data
        return {**data, "id": identifier, "category": CATEGORY_BY_PREFIX[match.group(1)]}

    @model_validator(mode="after")
    def require_identifier(self) -> Requirement:
        if not REQUIREMENT_ID_RE.fullmatch(self.id):
            raise ValueError("requirement id must match PWR-001, IF-002, MECH-003, or TEST-004")
        return self


class DesignPin(BaseModel):
    number: str
    name: str
    electrical_type: str

    model_config = ConfigDict(extra="forbid")


class DesignComponent(BaseModel):
    reference: str
    value: str
    lib_id: str
    pins: list[DesignPin]

    model_config = ConfigDict(extra="forbid")


class DesignNet(BaseModel):
    name: str
    pins: list[str]

    model_config = ConfigDict(extra="forbid")


class CheckCounts(BaseModel):
    errors: int
    warnings: int

    model_config = ConfigDict(extra="forbid")


class BoardContext(BaseModel):
    outline: tuple[float, float, float, float]
    width_mm: float
    height_mm: float

    model_config = ConfigDict(extra="forbid")


class DesignContext(BaseModel):
    components: list[DesignComponent] = Field(default_factory=list)
    nets: list[DesignNet] = Field(default_factory=list)
    erc_baseline: CheckCounts
    drc_baseline: CheckCounts | None = None
    board: BoardContext | None = None

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_project(
        cls,
        state: ProjectState,
        erc_baseline: ErcReport,
        drc_baseline: ErcReport | None = None,
        board_path: Path | str | None = None,
    ) -> DesignContext:
        from ..kicad.board import board_outline, load

        components = [
            DesignComponent(
                reference=component.reference,
                value=component.value,
                lib_id=component.lib_id,
                pins=[
                    DesignPin(
                        number=pin.number,
                        name=pin.name,
                        electrical_type=pin.electrical_type,
                    )
                    for pin in component.pins
                ],
            )
            for component in sorted(state.components.values(), key=lambda item: item.reference)
        ]
        nets = [DesignNet(name=name, pins=sorted(pins)) for name, pins in sorted(state.nets.items())]
        board = None
        path = Path(board_path) if board_path is not None else state.schematic_path.with_suffix(".kicad_pcb")
        if path.exists():
            document = load(path)
            min_x, min_y, max_x, max_y = board_outline(document)
            board = BoardContext(
                outline=(min_x, min_y, max_x, max_y),
                width_mm=max_x - min_x,
                height_mm=max_y - min_y,
            )
        return cls(
            components=components,
            nets=nets,
            erc_baseline=CheckCounts(errors=erc_baseline.errors, warnings=erc_baseline.warnings),
            drc_baseline=(
                CheckCounts(errors=drc_baseline.errors, warnings=drc_baseline.warnings)
                if drc_baseline is not None
                else None
            ),
            board=board,
        )

    @classmethod
    def from_project_state(
        cls,
        state: ProjectState,
        erc_baseline: ErcReport,
        drc_baseline: ErcReport | None = None,
        board_path: Path | str | None = None,
    ) -> DesignContext:
        return cls.from_project(state, erc_baseline, drc_baseline, board_path)


class ProjectSpec(TeamModel):
    project_id: str
    request: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    protected_objects: list[str] = Field(default_factory=list)
    manufacturer_profile: dict[str, JsonValue] = Field(default_factory=dict)
    current_stage: str
    design_context: DesignContext | None = None


class AgentTask(TeamModel):
    task_id: str
    assigned_agent: str
    objective: str
    inputs: list[dict[str, JsonValue]] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    protected_objects: list[str] = Field(default_factory=list)
    prior_gate_findings: list[str] = Field(default_factory=list)
    """Why this stage was handed back, in the gate's own words. Empty on a first pass."""


class ActionProposal(TeamModel):
    agent: str
    actions: list[JsonValue] = Field(default_factory=list)
    expected_changes: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    required_approval: bool


class EvidenceRecord(TeamModel):
    check: str
    tool: str
    project_version: str
    timestamp: str
    result: str
    findings: list[JsonValue] = Field(default_factory=list)
    runner: str | None = None
    session_url: str | None = None
    status: str | None = None
    acus_consumed: float = 0.0
    duration_seconds: float = 0.0


class AgentResult(TeamModel):
    task_id: str
    agent: str
    status: str
    output: object | None = None
    output_artifacts: list[str] = Field(default_factory=list)
    validation_evidence: list[EvidenceRecord] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    session_url: str | None = None
    acus_consumed: float = 0.0
    duration_seconds: float = 0.0
    runner: str = "devin"


class TeamRunReport(TeamModel):
    run_id: str
    project: ProjectSpec
    request: str
    per_stage_results: dict[str, AgentResult] = Field(default_factory=dict)
    requirements_satisfied: int
    requirements_total: int
    erc_status: str
    drc_status: str
    requirements_status: str = "not run"
    power_tests: int = 0
    power_tests_passed: int = 0
    power_tests_total: int = 0
    power_tests_status: str = "not run"
    manufacturing_warnings: int = 0
    manufacturing_status: str = "not run"
    critical_issues: int = 0
    open_critical_findings: list[JsonValue] = Field(default_factory=list)
    release_status: str
    summary: str


class PMPlan(BaseModel):
    project_goal: str
    workflow: list[str]
    status: str

    model_config = ConfigDict(extra="forbid")


class PowerRequirements(BaseModel):
    input: str
    logic_voltage: str
    maximum_current_ma: float | None

    model_config = ConfigDict(extra="forbid")


class InterfaceRequirement(BaseModel):
    type: str
    voltage: str
    devices: int | None

    model_config = ConfigDict(extra="forbid")


class MechanicalRequirements(BaseModel):
    maximum_width_mm: float | None
    maximum_height_mm: float | None
    layers: int | None

    model_config = ConfigDict(extra="forbid")


class RequirementsDoc(BaseModel):
    requirements: list[Requirement]
    power: PowerRequirements
    interfaces: list[InterfaceRequirement]
    mechanical: MechanicalRequirements
    constraints: list[str]
    acceptance_tests: list[str]

    @model_validator(mode="after")
    def unique_requirement_ids(self) -> RequirementsDoc:
        ids = [requirement.id for requirement in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("requirement IDs must be unique")
        return self

    model_config = ConfigDict(extra="forbid")


class ArchitectureBlock(BaseModel):
    id: str
    type: str
    requirement_ids: list[str] | None = None
    input_voltage: str | None = None
    output_voltage: str | None = None
    required_inputs: list[str] | None = None
    required_outputs: list[str] | None = None
    power_required_ma: float | None = None
    power_available_ma: float | None = None

    model_config = ConfigDict(extra="forbid")


class ArchitectureConnection(BaseModel):
    from_block: str = Field(alias="from")
    to_block: str = Field(alias="to")
    signal: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Architecture(BaseModel):
    blocks: list[ArchitectureBlock]
    connections: list[ArchitectureConnection]
    unresolved_questions: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


class ComponentSpecification(BaseModel):
    parameter: str
    unit: str
    source: str = ""
    page_or_section: str
    minimum: str | float
    typical: str | float
    maximum: str | float

    model_config = ConfigDict(extra="forbid")


class SelectedComponent(BaseModel):
    reference_group: str
    manufacturer_part: str
    quantity: int
    symbol: str
    footprint: str
    reason: str
    verified_constraints: list[str]
    specifications: list[ComponentSpecification] | None = None

    model_config = ConfigDict(extra="forbid")


class ComponentSelection(BaseModel):
    components: list[SelectedComponent]

    model_config = ConfigDict(extra="forbid")


class ConnectPinsIntent(BaseModel):
    id: str
    type: Literal["connect_pins"]
    from_pin: str = Field(alias="from")
    to_pin: str = Field(alias="to")
    net_name: str
    purpose: str = ""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ConnectPinToNetIntent(BaseModel):
    id: str
    type: Literal["connect_pin_to_net"]
    pin: str
    net: str
    purpose: str = ""

    model_config = ConfigDict(extra="forbid")


class EnsurePullupIntent(BaseModel):
    id: str
    type: Literal["ensure_pullup"]
    net: str
    to_net: str
    value: str = "4.7k"
    purpose: str = ""

    model_config = ConfigDict(extra="forbid")


SchematicIntent = Annotated[
    ConnectPinsIntent | ConnectPinToNetIntent | EnsurePullupIntent,
    Field(discriminator="type"),
]


class SchematicIntents(BaseModel):
    protocol: str
    logic_voltage: str
    pullup_value: str
    protected_objects: list[str]
    assumptions: list[str]
    intents: list[SchematicIntent]

    model_config = ConfigDict(extra="forbid")


class SchematicConnection(BaseModel):
    from_pin: str = Field(alias="from")
    to_pin: str = Field(alias="to")
    net: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SchematicProposal(BaseModel):
    added_components: list[str]
    created_nets: list[str]
    connections: list[SchematicConnection]

    model_config = ConfigDict(extra="forbid")


class BoardSize(BaseModel):
    width: float
    height: float

    model_config = ConfigDict(extra="forbid")


class Placement(BaseModel):
    reference: str
    x: float
    y: float
    rotation: float
    rationale: str

    model_config = ConfigDict(extra="forbid")


class LayoutProposal(BaseModel):
    placements: list[Placement]
    critical_nets: list[str]
    unrouted_nets: list[str]

    model_config = ConfigDict(extra="forbid")


class SimulationRailTest(BaseModel):
    name: str
    expected: dict[str, str | float]
    measured_v: str | float
    status: str
    source: str | None = None

    model_config = ConfigDict(extra="forbid")


class SimulationMarginTest(BaseModel):
    name: str
    required_ma: str | float
    available_ma: str | float
    margin_percent: str | float
    status: str
    source: str | None = None

    model_config = ConfigDict(extra="forbid")


SimulationTest = SimulationRailTest | SimulationMarginTest


class SimulationReport(BaseModel):
    tests: list[SimulationTest]
    models: list[str] | None = None
    assumptions: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


class VerificationFinding(BaseModel):
    requirement_id: str
    finding: str
    evidence: dict[str, JsonValue]
    rule: str | None = None
    actual: JsonValue = None
    expected: JsonValue = None
    kicad_object: str | None = None
    evidence_source: str | None = None
    severity: str | None = None

    model_config = ConfigDict(extra="forbid")


class RequirementOutcome(BaseModel):
    requirement_id: str
    status: Literal["passed", "failed", "unverified"]
    evidence: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class VerificationReport(BaseModel):
    requirements_total: int
    requirements_passed: int
    requirements_failed: int
    requirements_unverified: int
    critical_findings: list[VerificationFinding]
    decision: str
    requirement_outcomes: list[RequirementOutcome] | None = None

    model_config = ConfigDict(extra="forbid")


class ManufacturingFinding(BaseModel):
    type: str
    net: str
    severity: str
    recommendation: str

    model_config = ConfigDict(extra="forbid")


class ManufacturingReport(BaseModel):
    manufacturer_profile: str
    dfm_status: str
    findings: list[ManufacturingFinding]
    fabrication_ready: bool
    profile_rules: dict[str, float] | None = None
    profile_provenance: str | None = None

    model_config = ConfigDict(extra="forbid")


class ReleaseRecord(BaseModel):
    release_status: str
    project_version: str
    included_files: list[str]
    open_critical_findings: int
    release_hash: str
    checklist: dict[str, bool] | None = None

    model_config = ConfigDict(extra="forbid")


class CheckFinding(BaseModel):
    rule: str
    actual: JsonValue
    expected: JsonValue
    kicad_object: str
    evidence_source: str
    severity: Literal["error", "warning", "info"]

    model_config = ConfigDict(extra="forbid")


class StageCheckResult(BaseModel):
    stage: str
    passed: bool
    findings: list[CheckFinding] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    skipped_count: int = 0

    model_config = ConfigDict(extra="forbid")


class LayoutApplicationResult(BaseModel):
    accepted: bool
    restored: bool
    checkpoint_path: str
    check: StageCheckResult
    drc_report: ErcReport | None = None

    model_config = ConfigDict(extra="forbid")


OutputModel = TypeVar("OutputModel", bound=BaseModel)


def _inline_schema(value: object, definitions: dict[str, object]) -> object:
    if isinstance(value, list):
        return [_inline_schema(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value

    if "$ref" in value:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise ValueError(f"unsupported schema reference {reference!r}")
        name = reference.removeprefix("#/$defs/")
        if name not in definitions:
            raise ValueError(f"unknown schema definition {name!r}")
        expanded = deepcopy(definitions[name])
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            expanded.update(siblings)
        return _inline_schema(expanded, definitions)

    result = {
        key: _inline_schema(item, definitions)
        for key, item in value.items()
        if key not in {"$defs", "discriminator"}
    }
    if result.get("type") == "object" or "properties" in result:
        properties = result.get("properties", {})
        result["additionalProperties"] = False
        result["required"] = list(properties)
    return result


def json_schema(model: type[OutputModel]) -> dict[str, object]:
    """Return a Devin-compatible schema with references fully expanded."""
    generated = model.model_json_schema(by_alias=True)
    definitions = generated.get("$defs", {})
    return _inline_schema(generated, definitions)  # type: ignore[return-value]


def build_design_context(
    state: ProjectState,
    erc_baseline: ErcReport,
    drc_baseline: ErcReport | None = None,
    board_path: Path | str | None = None,
) -> DesignContext:
    return DesignContext.from_project(state, erc_baseline, drc_baseline, board_path)
