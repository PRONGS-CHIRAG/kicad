"""Executor interface: only schema-validated actions ever reach KiCAD."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import ActionPlan, ExecutionResult


class Executor(ABC):
    """Applies an approved plan to a KiCAD project."""

    name = "base"

    @abstractmethod
    def execute(self, project_dir: Path, plan: ActionPlan) -> ExecutionResult:
        """Apply every action in the plan; stop at the first failure."""

    @property
    def available(self) -> bool:
        return True
