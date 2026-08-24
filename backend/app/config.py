"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
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
    # v3: `cog_` service-user keys work only with the v3 API. The v1/v2
    # endpoints are for the legacy `apk_` keys and answer 403 to a service user.
    devin_base_url: str = "https://api.devin.ai/v3"
    devin_org_id: str | None = Field(
        default=None,
        description="org-… id. Left unset it is discovered once from GET /v3/self.",
    )
    devin_mode: str = Field(
        default="normal",
        description="normal | fast | lite | ultra | fusion. 'fast' is ~2x quicker at ~4x the ACU.",
    )
    devin_timeout_seconds: float = 300.0
    # Sessions answer anywhere between ~25 s and ~160 s, and the answer is read
    # the moment it appears - so a flat, short poll is what decides how quickly
    # one stage hands over to the next. A five-second poll spent up to five of
    # those seconds doing nothing, ten times a run.
    devin_poll_seconds: float = 1.5
    devin_max_acu: int = 5
    auto_resolve: bool = Field(
        default=False,
        description=(
            "Let the Devin agent answer a resolvable ambiguity instead of asking the user. "
            "Off by default: the benchmark measures the deterministic planner, and four of its "
            "ten scenarios expect a clarification."
        ),
    )

    team_runner: str | None = Field(
        default=None,
        description="Team runner: 'devin' for real sessions or 'stub' for deterministic offline runs.",
    )
    team_max_retries: int = Field(default=2, ge=0)
    team_stage_acu: int = Field(default=3, ge=1)
    team_manufacturer_profile: str = "generic_two_layer"
    team_parallel: bool = True
    team_repair: bool = Field(
        default=True,
        description=(
            "Let the repair stage correct a rejected document before the run is handed back to "
            "the agent that owns it. The repaired document goes through the same gate."
        ),
    )
    team_max_repairs_per_stage: int = Field(default=1, ge=0)
    team_human_timeout_seconds: int = Field(
        default=900,
        ge=0,
        description=(
            "How long a run waits for a person to answer when the gate has beaten the repair "
            "stage. The wait holds a worker thread, so it is bounded: on expiry the run carries "
            "on exactly as it would with nobody watching, and ends on needs_human_review."
        ),
    )

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"

    model_config = {"env_prefix": "KICAD_MITOS_", "env_file": ".env", "extra": "ignore"}

    @field_validator("team_runner")
    @classmethod
    def validate_team_runner(cls, value: str | None) -> str | None:
        if value is not None and value not in {"devin", "stub"}:
            raise ValueError("team_runner must be 'devin' or 'stub'")
        return value

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def devin_enabled(self) -> bool:
        return bool(self.devin_api_key)

    @property
    def resolved_team_runner(self) -> str:
        return self.team_runner or ("devin" if self.devin_enabled else "stub")

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
