from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import settings
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
