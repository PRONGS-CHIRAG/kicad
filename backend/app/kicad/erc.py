"""KiCAD ERC/DRC execution and normalization. KiCAD output is the source of truth."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..models import ErcReport, Violation, ViolationDiff

logger = logging.getLogger(__name__)

SEVERITY_MAP = {"error": "error", "warning": "warning", "info": "info", "exclusion": "info", "ignore": "info"}
DEFAULT_BOARD_LAYERS = "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts"
DRC_SECTIONS = ("violations", "unconnected_items", "schematic_parity")

# kicad-cli stamps the export time into the SVG's <title>, so two renders of an
# unchanged file differ whenever they straddle a second. Dropping just the date
# makes a render a pure function of the file, which is what lets the UI and the
# demo compare a before and an after meaningfully.
#
# The stamp format is version-dependent: KiCAD 9 writes `date 2026/08/22 20:59:35`,
# KiCAD 10 writes ISO 8601, `date 2026-08-22T20:59:35`. Accept either separator so
# the render stays byte-stable on both.
_SVG_DATE = re.compile(
    r"(<title>[^<]*?)\s*date \d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2}\s*(</title>)"
)


def strip_render_timestamp(svg: str) -> str:
    return _SVG_DATE.sub(r"\1\2", svg)


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
        """`kicad-cli <sub> --help` exits non-zero when INPUT_FILE is missing, so match on usage text.

        The usage line names the subcommand differently across versions: KiCAD 9 (what the
        Docker image ships) prints `Usage: erc [...]`, while KiCAD 10 prints the full path,
        `Usage: sch erc [...]`. Accept either. An unknown subcommand falls back to the
        parent's usage (`Usage: kicad-cli sch [--help] {erc,export,upgrade}`), which matches
        neither form.
        """
        if not self.available:
            return False
        result = subprocess.run(
            [self.executable, *args, "--help"], capture_output=True, text=True, timeout=60, check=False
        )
        output = (result.stdout + result.stderr).lower()
        return f"usage: {' '.join(args)}" in output or f"usage: {args[-1]}" in output

    def export_schematic_svg(self, schematic: Path) -> str | None:
        """Render the schematic exactly as KiCAD sees it on disk."""
        if not self.available:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    self.executable,
                    "sch",
                    "export",
                    "svg",
                    "--no-background-color",
                    # Same reasoning as the board render: the drawing sheet frame is
                    # mostly empty margin, and the UI already shows the title block's
                    # facts (project, revision, ERC counts) beside the render.
                    "--exclude-drawing-sheet",
                    "-o",
                    tmp,
                    str(schematic),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            pages = sorted(Path(tmp).glob("*.svg"))
            if not pages:
                logger.warning("schematic svg export failed: %s", (proc.stdout + proc.stderr)[-500:])
                return None
            return strip_render_timestamp(pages[0].read_text())

    def export_board_svg(self, board: Path, layers: str = DEFAULT_BOARD_LAYERS) -> str | None:
        if not self.available:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.svg"
            proc = subprocess.run(
                [
                    self.executable,
                    "pcb",
                    "export",
                    "svg",
                    "--layers",
                    layers,
                    "--page-size-mode",
                    "2",
                    "--exclude-drawing-sheet",
                    "-o",
                    str(out),
                    str(board),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if not out.exists():
                logger.warning("board svg export failed: %s", (proc.stdout + proc.stderr)[-500:])
                return None
            return strip_render_timestamp(out.read_text())

    def run_erc(self, schematic: Path) -> ErcReport:
        return self._run_check(["sch", "erc"], schematic)

    def run_drc(self, board: Path) -> ErcReport:
        # Parity is opt-in from KiCAD 9 onward, and it is the check that catches
        # a symbol added to the schematic with no matching footprint on the board.
        return self._run_check(["pcb", "drc"], board, extra=["--schematic-parity"])

    def run_drc_baseline(self, board: Path, passes: int = 2) -> ErcReport:
        """A DRC baseline that is a conservative superset of any single run.

        KiCAD's DRC is not fully deterministic. On a byte-identical board it
        intermittently reports one or two fewer violations - measured here as 20
        violations on 38 of 40 runs, 19 once and 18 once, with kicad-cli's own
        output saying "Found 8 violations" one run and "Found 7" the next. The
        ones that come and go are clearance violations, which `_awaiting_routing`
        does not exempt.

        That matters because whichever run happens to become the baseline decides
        whether a later, normal run looks like it introduced new errors. A
        single-run baseline therefore makes the DRC gate reject valid work at
        random, roughly one time in twenty. Unioning a couple of passes fixes the
        dangerous direction: a violation KiCAD reported on the original board is
        pre-existing no matter which pass found it, so it can never be counted as
        new later. It cannot hide a real regression either - a violation the
        original board does not have cannot appear in any pass over it.
        """
        report = self.run_drc(board)
        if not report.ran or passes < 2:
            return report
        seen = {v.signature() for v in report.violations}
        merged = list(report.violations)
        for _ in range(passes - 1):
            extra_pass = self.run_drc(board)
            if not extra_pass.ran:
                continue
            for violation in extra_pass.violations:
                if violation.signature() not in seen:
                    seen.add(violation.signature())
                    merged.append(violation)
        return report.model_copy(
            update={
                "violations": merged,
                "errors": sum(1 for v in merged if v.severity == "error"),
                "warnings": sum(1 for v in merged if v.severity == "warning"),
            }
        )

    def _run_check(self, subcommand: list[str], target: Path, extra: list[str] | None = None) -> ErcReport:
        if not self.available:
            return ErcReport(ran=False, raw_output=f"{self.executable} not found on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            proc = subprocess.run(
                [
                    self.executable,
                    *subcommand,
                    "--format",
                    "json",
                    *(extra or []),
                    "-o",
                    str(out),
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if not out.exists():
                return ErcReport(ran=False, raw_output=(proc.stdout + proc.stderr)[-4000:])
            data = json.loads(out.read_text())
        return parse_report(data, raw_output=proc.stdout[-4000:])


def _entries(data: dict) -> list[dict]:
    """Every violation entry in an ERC or DRC report, whatever section it sits in."""
    entries: list[dict] = []
    # ERC groups its violations per sheet.
    sheets = data.get("sheets")
    if isinstance(sheets, dict):
        sheets = [sheets]
    for sheet in sheets or []:
        if isinstance(sheet, dict):
            entries.extend(item for item in sheet.get("violations") or [] if isinstance(item, dict))
    # DRC reports unconnected items and schematic-parity failures in their own
    # top-level sections. They are real DRC failures, so dropping them would
    # make the "no new critical DRC violations" check silently always pass.
    for section in DRC_SECTIONS:
        block = data.get(section)
        if isinstance(block, dict):
            block = [block]
        for entry in block or []:
            if isinstance(entry, dict):
                entries.append({**entry, "section": section})
    return entries


# kicad-cli names the net inside each item description, e.g.
# `Pad 3 [I2C_SDA] of U1 on F.Cu` or `Track [GND] on F.Cu, length 6.0000 mm`.
_ITEM_NET = re.compile(r"\[([^\]]+)\]")


def _nets_in(items: list[str]) -> list[str]:
    """Nets named by a violation's items. `<no net>` is an absence, not a net."""
    names = {
        match.group(1).strip()
        for item in items
        for match in _ITEM_NET.finditer(item)
        if match.group(1).strip() not in ("", "<no net>")
    }
    return sorted(names)


def parse_report(data: dict, raw_output: str = "") -> ErcReport:
    """Normalize a kicad-cli JSON ERC/DRC report."""
    violations: list[Violation] = []
    for entry in _entries(data):
        severity = SEVERITY_MAP.get(str(entry.get("severity", "warning")).lower(), "warning")
        items = [
            str(item.get("description", "")) for item in entry.get("items", []) if isinstance(item, dict)
        ]
        section = entry.get("section")
        kind = str(entry.get("type", "unknown"))
        violations.append(
            Violation(
                severity=severity,
                # Section-qualified so an unconnected item never shares a
                # signature with a same-named rule violation.
                type=kind if section in (None, "violations") else f"{section}.{kind}",
                description=str(entry.get("description", "")),
                items=sorted(items),
                nets=_nets_in(items),
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
