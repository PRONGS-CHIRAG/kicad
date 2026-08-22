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

    # --- Devin agent -------------------------------------------------------
    devin_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("KICAD_MITOS_DEVIN_API_KEY", "DEVIN_API_KEY"),
    )
    devin_base_url: str = "https://api.devin.ai/v1"
    devin_timeout_seconds: float = 120.0
    devin_poll_seconds: float = 5.0
    devin_max_acu: int = 5
    auto_resolve: bool = Field(
        default=False,
        description=(
            "Let the Devin agent answer a resolvable ambiguity instead of asking the user. "
            "Off by default: the benchmark measures the deterministic planner, and four of its "
            "ten scenarios expect a clarification."
        ),
    )

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"

    model_config = {"env_prefix": "KICAD_MITOS_", "env_file": ".env", "extra": "ignore"}

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def devin_enabled(self) -> bool:
        return bool(self.devin_api_key)

    @property
    def agent_resolves_ambiguity(self) -> bool:
        """Both a key and the opt-in: a key alone must not change planning."""
        return self.devin_enabled and self.auto_resolve

    @property
    def checkpoints_dir(self) -> Path:
        return self.workspace_dir / "checkpoints"

    @property
    def sessions_dir(self) -> Path:
        return self.workspace_dir / "sessions"

    @property
    def uploads_dir(self) -> Path:
        """Uploaded projects live in the workspace, never in the git-tracked fixture set."""
        return self.workspace_dir / "projects"


settings = Settings()
