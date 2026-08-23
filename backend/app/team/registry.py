"""Canonical team roster and stage input policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel

from ..config import settings
from . import fallbacks, prompts
from .schemas import (
    AgentTask,
    Architecture,
    ComponentSelection,
    DesignContext,
    LayoutProposal,
    ManufacturingReport,
    PMPlan,
    ProjectSpec,
    ReleaseRecord,
    RequirementsDoc,
    SchematicIntents,
    SimulationReport,
    VerificationReport,
)

PromptBuilder = Callable[[ProjectSpec, DesignContext, AgentTask, Mapping[str, object]], str]
Fallback = Callable[[ProjectSpec, AgentTask, Mapping[str, object]], BaseModel]


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    real_world_role: str
    output_model: type[BaseModel]
    prompt_builder: PromptBuilder
    reads: tuple[str, ...]
    mutates_design: bool
    read_only: bool
    fallback: Fallback | None
    tags: tuple[str, ...]

    @property
    def max_acu(self) -> int:
        return settings.team_stage_acu

    @property
    def devin_mode(self) -> str:
        return settings.devin_mode

    @property
    def timeout_seconds(self) -> float:
        if self.id in {"project_manager", "requirements"}:
            return settings.team_coordination_timeout_seconds
        return settings.team_stage_timeout_seconds

    def inputs_for(self, prior_outputs: Mapping[str, object]) -> dict[str, object]:
        """Return only the prior-stage outputs declared by this agent."""
        return {stage: prior_outputs[stage] for stage in self.reads if stage in prior_outputs}

    def build_prompt(
        self,
        project: ProjectSpec,
        task: AgentTask,
        prior_outputs: Mapping[str, object],
        context: DesignContext | None = None,
    ) -> str:
        allowed = self.inputs_for(prior_outputs)
        filtered_task = task.model_copy(update={"inputs": [_to_input(value) for value in allowed.values()]})
        design_context = (
            context or project.design_context or DesignContext(erc_baseline={"errors": 0, "warnings": 0})
        )
        return self.prompt_builder(project, design_context, filtered_task, allowed)


def _to_input(value: object) -> dict[str, object]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {"value": value}


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="project_manager",
        name="Project Manager",
        real_world_role="Hardware project manager",
        output_model=PMPlan,
        prompt_builder=prompts.build_project_manager_prompt,
        reads=(),
        mutates_design=False,
        read_only=True,
        fallback=None,
        tags=("kicad-mitos", "team", "project_manager"),
    ),
    AgentSpec(
        id="requirements",
        name="Requirements",
        real_world_role="Hardware requirements engineer",
        output_model=RequirementsDoc,
        prompt_builder=prompts.build_requirements_prompt,
        reads=("project_manager",),
        mutates_design=False,
        read_only=True,
        fallback=fallbacks.requirements_fallback,
        tags=("kicad-mitos", "team", "requirements"),
    ),
    AgentSpec(
        id="architecture",
        name="System Architect",
        real_world_role="Electronics system architect",
        output_model=Architecture,
        prompt_builder=prompts.build_architecture_prompt,
        reads=("requirements",),
        mutates_design=False,
        read_only=True,
        fallback=fallbacks.architecture_fallback,
        tags=("kicad-mitos", "team", "architecture"),
    ),
    AgentSpec(
        id="components",
        name="Component Engineer",
        real_world_role="Component/application engineer",
        output_model=ComponentSelection,
        prompt_builder=prompts.build_components_prompt,
        reads=("requirements", "architecture"),
        mutates_design=False,
        read_only=True,
        fallback=fallbacks.components_fallback,
        tags=("kicad-mitos", "team", "components"),
    ),
    AgentSpec(
        id="schematic_design",
        name="Schematic Design",
        real_world_role="Electronics design engineer",
        output_model=SchematicIntents,
        prompt_builder=prompts.build_schematic_design_prompt,
        reads=("requirements", "architecture", "components"),
        mutates_design=True,
        read_only=False,
        fallback=None,
        tags=("kicad-mitos", "team", "schematic_design"),
    ),
    AgentSpec(
        id="pcb_layout",
        name="PCB Layout",
        real_world_role="PCB layout engineer",
        output_model=LayoutProposal,
        prompt_builder=prompts.build_pcb_layout_prompt,
        reads=("schematic_design",),
        mutates_design=True,
        read_only=False,
        fallback=None,
        tags=("kicad-mitos", "team", "pcb_layout"),
    ),
    AgentSpec(
        id="simulation",
        name="Simulation",
        real_world_role="Circuit simulation engineer",
        output_model=SimulationReport,
        prompt_builder=prompts.build_simulation_prompt,
        reads=("schematic_design", "components"),
        mutates_design=False,
        read_only=True,
        fallback=fallbacks.simulation_fallback,
        tags=("kicad-mitos", "team", "simulation"),
    ),
    AgentSpec(
        id="verification",
        name="Verification",
        real_world_role="Hardware verification engineer",
        output_model=VerificationReport,
        prompt_builder=prompts.build_verification_prompt,
        reads=("schematic_design", "pcb_layout", "simulation"),
        mutates_design=False,
        read_only=True,
        fallback=None,
        tags=("kicad-mitos", "team", "verification"),
    ),
    AgentSpec(
        id="manufacturing",
        name="Manufacturing",
        real_world_role="DFM/DFA engineer",
        output_model=ManufacturingReport,
        prompt_builder=prompts.build_manufacturing_prompt,
        reads=("pcb_layout", "components", "verification"),
        mutates_design=False,
        read_only=True,
        fallback=fallbacks.manufacturing_fallback,
        tags=("kicad-mitos", "team", "manufacturing"),
    ),
    AgentSpec(
        id="qa_release",
        name="QA and Release",
        real_world_role="Hardware QA/release engineer",
        output_model=ReleaseRecord,
        prompt_builder=prompts.build_qa_release_prompt,
        reads=(
            "requirements",
            "architecture",
            "components",
            "schematic_design",
            "pcb_layout",
            "simulation",
            "verification",
            "manufacturing",
        ),
        mutates_design=False,
        read_only=True,
        fallback=fallbacks.qa_release_fallback,
        tags=("kicad-mitos", "team", "qa_release"),
    ),
)

AGENT_REGISTRY: dict[str, AgentSpec] = {agent.id: agent for agent in AGENTS}


def get_agent(agent_id: str) -> AgentSpec:
    return AGENT_REGISTRY[agent_id]


def output_models() -> dict[str, type[BaseModel]]:
    return {agent.id: agent.output_model for agent in AGENTS}
