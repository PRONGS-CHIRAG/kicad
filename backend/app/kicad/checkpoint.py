"""Whole-project checkpoints with hash-verified restoration."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

CHECKPOINT_PATTERNS = (
    "*.kicad_pro",
    "*.kicad_sch",
    "*.kicad_pcb",
    "*.kicad_sym",
    "*.kicad_prl",
    "*.pretty/**/*",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def hash_tree(project_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for pattern in CHECKPOINT_PATTERNS:
        for path in sorted(project_dir.glob(pattern)):
            if path.is_file():
                hashes[str(path.relative_to(project_dir))] = sha256(path)
    return hashes


@dataclass
class Checkpoint:
    project_dir: Path
    storage_dir: Path
    hashes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, project_dir: Path, storage_dir: Path) -> Checkpoint:
        project_dir = Path(project_dir)
        storage_dir = Path(storage_dir)
        if storage_dir.exists():
            shutil.rmtree(storage_dir)
        storage_dir.mkdir(parents=True)
        hashes = hash_tree(project_dir)
        for relative in hashes:
            source = project_dir / relative
            target = storage_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return cls(project_dir=project_dir, storage_dir=storage_dir, hashes=hashes)

    def files_changed(self) -> list[str]:
        current = hash_tree(self.project_dir)
        changed = [name for name, digest in current.items() if self.hashes.get(name) != digest]
        changed += [name for name in self.hashes if name not in current]
        return sorted(set(changed))

    def restore(self) -> bool:
        """Restore every checkpointed file and verify hashes match the original."""
        for relative in self.hashes:
            target = self.project_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.storage_dir / relative, target)
        for name in hash_tree(self.project_dir):
            if name not in self.hashes:
                (self.project_dir / name).unlink()
        return hash_tree(self.project_dir) == self.hashes
