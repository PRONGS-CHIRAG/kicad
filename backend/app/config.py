"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    projects_dir: Path = Field(default=REPO_ROOT / "fixtures" / "projects")
    workspace_dir: Path = Field(default=Path(os.environ.get("KICAD_MITOS_WORKSPACE", "/tmp/kicad-mitos")))
    kicad_cli: str = "kicad-cli"

    executor: str = Field(default="local", description="'local' (direct file edits) or 'mitos' (MCP)")
    mitos_command: str | None = None
    mitos_url: str | None = None

    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("KICAD_MITOS_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"

    model_config = {"env_prefix": "KICAD_MITOS_", "env_file": ".env", "extra": "ignore"}

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def checkpoints_dir(self) -> Path:
        return self.workspace_dir / "checkpoints"

    @property
    def sessions_dir(self) -> Path:
        return self.workspace_dir / "sessions"


settings = Settings()
