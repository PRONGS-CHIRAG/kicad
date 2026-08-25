"""Apply gated placement proposals to KiCAD boards."""

from __future__ import annotations

from pathlib import Path

from ..config import settings
from ..kicad import sexpr
from ..kicad.board import footprint_reference, load, save
from ..kicad.checkpoint import Checkpoint
from ..kicad.erc import KicadCli
from ..models import ErcReport
from .checks import check_layout
from .profiles import ManufacturerProfile, get_profile
from .schemas import LayoutApplicationResult, LayoutProposal


class LayoutApplicationError(ValueError):
    """A placement proposal cannot be applied safely."""


def _set_placement(footprint: list, x: float, y: float, rotation: float) -> None:
    at = sexpr.find(footprint, "at")
    if at is None or len(at) < 3:
        raise LayoutApplicationError("footprint has no valid at node")
    at[1] = sexpr.Symbol(f"{x:g}")
    at[2] = sexpr.Symbol(f"{y:g}")
    if len(at) >= 4:
        at[3] = sexpr.Symbol(f"{rotation:g}")
    else:
        at.append(sexpr.Symbol(f"{rotation:g}"))


def apply_layout(
    board_path: Path | str,
    proposal: LayoutProposal,
    profile: ManufacturerProfile | str,
    *,
    project_version: str,
    checkpoint_path: Path | str | None = None,
    required_nets: tuple[str, ...] = (),
    drc_before: ErcReport | None = None,
    cli: KicadCli | None = None,
) -> LayoutApplicationResult:
    """Apply placements, gate the rewritten board, and restore rejected work."""
    board = Path(board_path)
    if not board.is_file():
        raise LayoutApplicationError(f"board does not exist: {board}")
    document = load(board)
    footprints = {
        reference: footprint
        for footprint in sexpr.find_all(document, "footprint")
        if (reference := footprint_reference(footprint)) is not None
    }
    missing = sorted({placement.reference for placement in proposal.placements} - footprints.keys())
    if missing:
        raise LayoutApplicationError(f"unknown board footprint reference(s): {', '.join(missing)}")

    checkpoint_dir = (
        Path(checkpoint_path) if checkpoint_path is not None else settings.checkpoints_dir / board.stem
    )
    checkpoint = Checkpoint.create(board.parent, checkpoint_dir)
    try:
        for placement in proposal.placements:
            _set_placement(
                footprints[placement.reference],
                placement.x,
                placement.y,
                placement.rotation,
            )
        if proposal.placements:
            save(document, board)
        load(board)
        drc_report = (cli or KicadCli(settings.kicad_cli)).run_drc(board)
        result = check_layout(
            board,
            profile if isinstance(profile, ManufacturerProfile) else get_profile(profile),
            required_nets or tuple(proposal.critical_nets),
            drc_before,
            drc_report,
            project_version=project_version,
        )
        if any(finding.severity == "error" for finding in result.findings):
            restored = checkpoint.restore()
            return LayoutApplicationResult(
                accepted=False,
                restored=restored,
                checkpoint_path=str(checkpoint_dir),
                check=result,
                drc_report=drc_report,
            )
        return LayoutApplicationResult(
            accepted=True,
            restored=False,
            checkpoint_path=str(checkpoint_dir),
            check=result,
            drc_report=drc_report,
        )
    except Exception:
        checkpoint.restore()
        raise
