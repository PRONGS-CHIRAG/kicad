"""KiCAD ERC/DRC execution and normalization. KiCAD output is the source of truth."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..models import ErcReport, Violation, ViolationDiff

SEVERITY_MAP = {"error": "error", "warning": "warning", "info": "info", "exclusion": "info", "ignore": "info"}


class KicadCli:
    """Thin, discoverable wrapper around the installed `kicad-cli`."""

    def __init__(self, executable: str = "kicad-cli") -> None:
        self.executable = executable

    @property
    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def version(self) -> str | None:
        if not self.available:
            return None
        result = subprocess.run(
            [self.executable, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
        return result.stdout.strip() or None

    def supports(self, *args: str) -> bool:
        """`kicad-cli <sub> --help` exits non-zero when INPUT_FILE is missing, so match on usage text."""
        if not self.available:
            return False
        result = subprocess.run(
            [self.executable, *args, "--help"], capture_output=True, text=True, timeout=60, check=False
        )
        output = (result.stdout + result.stderr).lower()
        return f"usage: {args[-1]}" in output

    def run_erc(self, schematic: Path) -> ErcReport:
        return self._run_check(["sch", "erc"], schematic)

    def run_drc(self, board: Path) -> ErcReport:
        return self._run_check(["pcb", "drc"], board)

    def _run_check(self, subcommand: list[str], target: Path) -> ErcReport:
        if not self.available:
            return ErcReport(ran=False, raw_output=f"{self.executable} not found on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            proc = subprocess.run(
                [self.executable, *subcommand, "--format", "json", "-o", str(out), str(target)],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if not out.exists():
                return ErcReport(ran=False, raw_output=(proc.stdout + proc.stderr)[-4000:])
            data = json.loads(out.read_text())
        return parse_report(data, raw_output=proc.stdout[-4000:])


def parse_report(data: dict, raw_output: str = "") -> ErcReport:
    """Normalize a kicad-cli JSON ERC/DRC report."""
    violations: list[Violation] = []
    groups = data.get("sheets") or data.get("violations") or []
    if isinstance(groups, dict):
        groups = [groups]
    entries: list[dict] = []
    for group in groups:
        if isinstance(group, dict) and "violations" in group:
            entries.extend(group["violations"])
        elif isinstance(group, dict):
            entries.append(group)
    for entry in entries:
        severity = SEVERITY_MAP.get(str(entry.get("severity", "warning")).lower(), "warning")
        items = [str(item.get("description", "")) for item in entry.get("items", [])]
        violations.append(
            Violation(
                severity=severity,
                type=str(entry.get("type", "unknown")),
                description=str(entry.get("description", "")),
                items=sorted(items),
            )
        )
    return ErcReport(
        ran=True,
        kicad_version=data.get("kicad_version"),
        errors=sum(1 for v in violations if v.severity == "error"),
        warnings=sum(1 for v in violations if v.severity == "warning"),
        violations=violations,
        raw_output=raw_output,
    )


def diff_violations(before: ErcReport, after: ErcReport) -> ViolationDiff:
    """Compare actual violations, never just counts."""
    before_map: dict[str, Violation] = {}
    after_map: dict[str, Violation] = {}
    for violation in before.violations:
        before_map.setdefault(violation.signature(), violation)
    for violation in after.violations:
        after_map.setdefault(violation.signature(), violation)
    return ViolationDiff(
        new=[v for sig, v in after_map.items() if sig not in before_map],
        resolved=[v for sig, v in before_map.items() if sig not in after_map],
        unchanged=[v for sig, v in before_map.items() if sig in after_map],
    )
