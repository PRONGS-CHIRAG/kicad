from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kicad import upload as upload_module
from app.kicad.upload import UploadError, extract_project

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "projects" / "esp32_i2c_demo"


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _real_project(prefix: str = "") -> dict[str, bytes]:
    return {f"{prefix}{path.name}": path.read_bytes() for path in sorted(FIXTURES.iterdir())}


def test_extracts_a_flat_project(tmp_path: Path) -> None:
    target = extract_project(_zip(_real_project()), "mine", tmp_path)
    assert target == tmp_path / "mine"
    assert (target / "esp32_i2c_demo.kicad_sch").exists()


def test_flattens_a_single_wrapping_folder(tmp_path: Path) -> None:
    target = extract_project(_zip(_real_project("board/")), "wrapped", tmp_path)
    assert list(target.glob("*.kicad_sch")), "the wrapping folder should be stripped"


def test_rejects_an_archive_without_a_schematic(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="no .kicad_sch"):
        extract_project(_zip({"notes.txt": b"hello"}), "empty", tmp_path)


def test_rejects_zip_slip(tmp_path: Path) -> None:
    payload = _real_project()
    payload["../escape.kicad_sch"] = b"(kicad_sch)"
    with pytest.raises(UploadError, match="unsafe path"):
        extract_project(_zip(payload), "evil", tmp_path)
    assert not (tmp_path.parent / "escape.kicad_sch").exists()


def test_rejects_absolute_paths(tmp_path: Path) -> None:
    payload = _real_project()
    payload["/etc/passwd.kicad_sch"] = b"(kicad_sch)"
    with pytest.raises(UploadError, match="unsafe path"):
        extract_project(_zip(payload), "evil", tmp_path)


@pytest.mark.parametrize("name", ["../etc", "a/b", "", ".hidden", "x" * 65])
def test_rejects_bad_project_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(UploadError):
        extract_project(_zip(_real_project()), name, tmp_path)


def test_rejects_an_archive_that_expands_past_the_size_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zip-bomb guard: a small archive declaring a huge expansion is refused.

    The cap is lowered rather than building a real 200 MB payload, so the guard
    itself is exercised without the test costing 200 MB of memory.
    """
    monkeypatch.setattr(upload_module, "MAX_UNCOMPRESSED_BYTES", 1024)
    payload = _real_project()
    # Highly compressible, so the archive stays tiny while file_size does not.
    payload["big.kicad_pcb"] = b"\0" * 65536
    with pytest.raises(UploadError, match="more than the allowed maximum"):
        extract_project(_zip(payload), "bomb", tmp_path)
    assert not (tmp_path / "bomb").exists()


def test_rejects_an_archive_with_too_many_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_module, "MAX_ENTRIES", 5)
    payload = _real_project()
    payload.update({f"pad{i}.txt": b"x" for i in range(10)})
    with pytest.raises(UploadError, match="more than the .* allowed"):
        extract_project(_zip(payload), "manyfiles", tmp_path)
    assert not (tmp_path / "manyfiles").exists()


def test_a_project_within_the_caps_is_still_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards must not be so tight that a legitimate project trips them."""
    monkeypatch.setattr(upload_module, "MAX_UNCOMPRESSED_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(upload_module, "MAX_ENTRIES", 50)
    target = extract_project(_zip(_real_project()), "ok", tmp_path)
    assert list(target.glob("*.kicad_sch"))


def test_drops_files_that_are_not_part_of_a_kicad_project(tmp_path: Path) -> None:
    payload = _real_project()
    payload["evil.sh"] = b"rm -rf /"
    target = extract_project(_zip(payload), "filtered", tmp_path)
    assert not (target / "evil.sh").exists()
    assert list(target.glob("*.kicad_sch"))


def test_replacing_a_project_does_not_leave_the_old_files(tmp_path: Path) -> None:
    extract_project(_zip(_real_project()), "again", tmp_path)
    stale = tmp_path / "again" / "stale.kicad_sym"
    stale.write_text("(stale)")
    extract_project(_zip(_real_project()), "again", tmp_path)
    assert not stale.exists()


def test_uploaded_project_becomes_usable_over_the_api(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects",
        data={"name": "uploaded_demo"},
        files={"file": ("project.zip", _zip(_real_project()), "application/zip")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["origin"] == "uploaded"

    listed = {p["name"]: p["origin"] for p in client.get("/api/projects").json()["projects"]}
    assert listed["uploaded_demo"] == "uploaded"
    assert listed["esp32_i2c_demo"] == "fixture"

    session = client.post("/api/sessions", json={"project": "uploaded_demo"})
    assert session.status_code == 200
    assert {c["reference"] for c in session.json()["components"]} >= {"U1", "U2"}


def test_upload_rejects_a_non_zip(client: TestClient) -> None:
    response = client.post(
        "/api/projects",
        data={"name": "junk"},
        files={"file": ("x.zip", b"not a zip", "application/zip")},
    )
    assert response.status_code == 400
    assert "not a valid zip" in response.json()["detail"]


def test_uploads_never_touch_the_fixture_directory(client: TestClient) -> None:
    before = sorted(p.name for p in FIXTURES.parent.iterdir())
    client.post(
        "/api/projects",
        data={"name": "somewhere_else"},
        files={"file": ("p.zip", _zip(_real_project()), "application/zip")},
    )
    assert sorted(p.name for p in FIXTURES.parent.iterdir()) == before
