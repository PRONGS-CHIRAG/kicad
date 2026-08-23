"""Placement application tests against a real KiCAD board fixture."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.models import ErcReport, Violation
from app.team.layout import LayoutApplicationError, apply_layout
from app.team.profiles import get_profile
from app.team.schemas import LayoutProposal, Placement

FIXTURE = Path(__file__).parents[2] / "fixtures" / "projects" / "esp32_i2c_board"


def _board(tmp_path: Path) -> Path:
    directory = tmp_path / "project"
    shutil.copytree(FIXTURE, directory)
    return directory / "esp32_i2c_board.kicad_pcb"


def test_layout_application_rewrites_placement_and_preserves_board_validity(tmp_path: Path) -> None:
    board = _board(tmp_path)
    proposal = LayoutProposal(
        placements=[{"reference": "U1", "x": 70, "y": 65, "rotation": 0, "rationale": "test"}],
        critical_nets=[],
        unrouted_nets=[],
    )
    result = apply_layout(
        board,
        proposal,
        get_profile("generic_two_layer"),
        project_version="rev-1",
        checkpoint_path=tmp_path / "checkpoint",
    )
    assert result.accepted
    assert not result.restored
    assert "(at 70 65 0)" in board.read_text()


def test_unknown_reference_is_rejected_before_any_write(tmp_path: Path) -> None:
    board = _board(tmp_path)
    original = board.read_bytes()
    proposal = LayoutProposal(
        placements=[{"reference": "U99", "x": 70, "y": 65, "rotation": 0, "rationale": "test"}],
        critical_nets=[],
        unrouted_nets=[],
    )
    with pytest.raises(LayoutApplicationError):
        apply_layout(
            board,
            proposal,
            "generic_two_layer",
            project_version="rev-1",
            checkpoint_path=tmp_path / "checkpoint",
        )
    assert board.read_bytes() == original


def test_thirteenth_live_layout_rejects_absent_board_references(tmp_path: Path) -> None:
    board = _board(tmp_path)
    proposal = LayoutProposal.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_pcb_layout_thirteenth.json").read_text())
    )
    proposal = proposal.model_copy(
        update={
            "placements": [
                *proposal.placements,
                Placement(reference="R1", x=70, y=65, rotation=0, rationale="absent"),
            ]
        }
    )
    with pytest.raises(LayoutApplicationError, match="R1"):
        apply_layout(
            board,
            proposal,
            "generic_two_layer",
            project_version="thirteenth-live",
            checkpoint_path=tmp_path / "checkpoint",
        )


def test_rejected_edge_placement_restores_original_bytes(tmp_path: Path) -> None:
    board = _board(tmp_path)
    original = board.read_bytes()
    proposal = LayoutProposal(
        placements=[{"reference": "U1", "x": 1, "y": 1, "rotation": 0, "rationale": "outside"}],
        critical_nets=[],
        unrouted_nets=[],
    )
    result = apply_layout(
        board,
        proposal,
        "generic_two_layer",
        project_version="rev-1",
        checkpoint_path=tmp_path / "checkpoint",
    )
    assert not result.accepted
    assert result.restored
    assert board.read_bytes() == original


def test_fresh_drc_violation_rejects_and_restores(tmp_path: Path) -> None:
    board = _board(tmp_path)
    original = board.read_bytes()
    proposal = LayoutProposal(
        placements=[{"reference": "U1", "x": 70, "y": 65, "rotation": 0, "rationale": "test"}],
        critical_nets=[],
        unrouted_nets=[],
    )

    class DrcCli:
        def run_drc(self, _path: Path) -> ErcReport:
            return ErcReport(
                ran=True,
                violations=[
                    Violation(
                        severity="error",
                        type="clearance",
                        description="fresh violation",
                    )
                ],
            )

    result = apply_layout(
        board,
        proposal,
        "generic_two_layer",
        project_version="rev-1",
        checkpoint_path=tmp_path / "checkpoint",
        drc_before=ErcReport(ran=True),
        cli=DrcCli(),  # type: ignore[arg-type]
    )
    assert not result.accepted
    assert result.restored
    assert board.read_bytes() == original
