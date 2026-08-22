from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import settings
from app.kicad.erc import KicadCli
from app.kicad.reader import read_project
from app.workflow import SessionStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "projects"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    target = tmp_path / "esp32_i2c_demo"
    shutil.copytree(FIXTURES / "esp32_i2c_demo", target)
    return target


@pytest.fixture()
def state(project: Path):
    return read_project(project)


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "workspace")
    monkeypatch.setattr(settings, "projects_dir", FIXTURES)
    return SessionStore()


@pytest.fixture()
def schematic_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Point the store at a projects root whose one project has no board.

    Every bundled fixture now ships a `.kicad_pcb`, so the schematic-only path
    needs a project built for the test rather than a fixture that happens to
    lack one. Returns the project name to open.
    """
    root = tmp_path / "schematic-only"
    target = root / "esp32_i2c_demo"
    shutil.copytree(FIXTURES / "esp32_i2c_demo", target)
    for board in target.glob("*.kicad_pcb"):
        board.unlink()
    monkeypatch.setattr(settings, "projects_dir", root)
    return target.name


@pytest.fixture()
def client(store: SessionStore, monkeypatch: pytest.MonkeyPatch):
    """API client sharing the temp-workspace store, so uploads stay out of the repo."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.api.routes.store", store)
    return TestClient(app)


@pytest.fixture()
def requires_kicad() -> None:
    """Skip tests that can only pass when KiCAD's own ERC/DRC and renderers exist.

    Without kicad-cli the pipeline deliberately refuses to accept anything, so
    these assertions would be testing the degraded path, not the real one.
    Run them via the Docker image, which ships kicad-cli.
    """
    cli = KicadCli(settings.kicad_cli)
    if not cli.available:
        pytest.skip(f"{settings.kicad_cli} is not on PATH; run the suite in the backend image")
