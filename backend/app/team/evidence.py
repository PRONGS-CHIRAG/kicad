"""Append-only evidence for a team run."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ..config import settings
from .schemas import EvidenceRecord


class EvidenceStore:
    """Persist team evidence outside the repository working tree."""

    def __init__(self, run_id: str, workspace_dir: Path | None = None) -> None:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError("run_id must be a single path component")
        self.root = (workspace_dir or settings.workspace_dir) / "team" / run_id
        self.artifacts_dir = self.root / "artifacts"
        self.evidence_path = self.root / "evidence.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(exist_ok=True)

    def append(self, record: EvidenceRecord) -> Path:
        """Append one JSON object, preserving the order records were produced."""
        with self.evidence_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.model_dump(mode="json"), sort_keys=True))
            stream.write("\n")
        return self.evidence_path

    def write_stage_output(self, stage: str, output: BaseModel | dict[str, object]) -> Path:
        if not stage or Path(stage).name != stage:
            raise ValueError("stage must be a single path component")
        payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
        path = self.root / f"{stage}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def write_artifact(self, name: str, content: bytes | str | Path) -> Path:
        destination = (self.artifacts_dir / name).resolve()
        if not destination.is_relative_to(self.artifacts_dir.resolve()):
            raise ValueError("artifact path escapes the run directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Path):
            destination.write_bytes(content.read_bytes())
        elif isinstance(content, bytes):
            destination.write_bytes(content)
        else:
            destination.write_text(content, encoding="utf-8")
        return destination
