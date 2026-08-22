"""Safe extraction of an uploaded KiCAD project archive."""

from __future__ import annotations

import re
import shutil
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 2000
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Everything a KiCAD project legitimately needs. Anything else in the archive is
# dropped rather than written to disk.
ALLOWED_SUFFIXES = {
    ".kicad_pro",
    ".kicad_sch",
    ".kicad_pcb",
    ".kicad_sym",
    ".kicad_prl",
    ".kicad_dru",
    ".kicad_mod",
    ".kicad_wks",
    ".net",
    ".csv",
    ".txt",
    ".json",
}


class UploadError(ValueError):
    """The archive is not a usable KiCAD project."""


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = [m for m in archive.infolist() if not m.is_dir()]
    if len(members) > MAX_ENTRIES:
        raise UploadError(f"archive has {len(members)} files, more than the {MAX_ENTRIES} allowed")
    total = sum(m.file_size for m in members)
    if total > MAX_UNCOMPRESSED_BYTES:
        raise UploadError(f"archive expands to {total} bytes, more than the allowed maximum")

    safe: list[zipfile.ZipInfo] = []
    for member in members:
        path = PurePosixPath(member.filename)
        # Zip-slip: reject absolute paths, parent traversal and drive letters.
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise UploadError(f"archive contains an unsafe path: {member.filename}")
        if any(part.startswith(".") and part not in {".pretty"} for part in path.parts[:-1]):
            continue
        if path.suffix.lower() in ALLOWED_SUFFIXES:
            safe.append(member)
    return safe


def _strip_common_root(members: list[zipfile.ZipInfo]) -> int:
    """Number of leading path segments to drop, so a zipped folder is flattened."""
    roots = {PurePosixPath(m.filename).parts[0] for m in members if len(PurePosixPath(m.filename).parts) > 1}
    nested = all(len(PurePosixPath(m.filename).parts) > 1 for m in members)
    return 1 if nested and len(roots) == 1 else 0


def extract_project(data: bytes, name: str, dest_root: Path) -> Path:
    """Extract a project archive into `dest_root/name` and return the directory."""
    if not NAME_PATTERN.match(name):
        raise UploadError(
            "project name must be 1-64 characters of letters, digits, dot, dash or underscore"
        )
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadError("the uploaded file is not a valid zip archive") from exc

    with archive:
        members = _safe_members(archive)
        if not any(PurePosixPath(m.filename).suffix.lower() == ".kicad_sch" for m in members):
            raise UploadError("the archive contains no .kicad_sch file, so it is not a KiCAD project")
        strip = _strip_common_root(members)

        target = dest_root / name
        staging = dest_root / f".{name}.incoming"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            for member in members:
                parts = PurePosixPath(member.filename).parts[strip:]
                if not parts:
                    continue
                out = staging.joinpath(*parts)
                # Belt and braces: confirm the resolved path stayed inside staging.
                if not out.resolve().is_relative_to(staging.resolve()):
                    raise UploadError(f"archive contains an unsafe path: {member.filename}")
                out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            if not list(staging.glob("*.kicad_sch")):
                raise UploadError(
                    "the .kicad_sch file is not at the project root; upload the project folder itself"
                )
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    return target
