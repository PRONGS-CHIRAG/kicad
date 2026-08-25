"""Named, deterministic manufacturer constraints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ManufacturerProfile(BaseModel):
    name: str
    provenance: str
    minimum_trace_width_mm: float
    minimum_spacing_mm: float
    minimum_drill_mm: float
    copper_to_edge_clearance_mm: float
    supported_layer_count: int

    model_config = ConfigDict(extra="forbid", frozen=True)


PROFILES: dict[str, ManufacturerProfile] = {
    "generic_two_layer": ManufacturerProfile(
        name="generic_two_layer",
        provenance=(
            "Repository-chosen values representative of low-cost two-layer fabrication; "
            "not confirmed by any vendor."
        ),
        minimum_trace_width_mm=0.15,
        minimum_spacing_mm=0.15,
        minimum_drill_mm=0.2,
        copper_to_edge_clearance_mm=0.3,
        supported_layer_count=2,
    )
}


def get_profile(name: str) -> ManufacturerProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown manufacturer profile {name!r}") from exc
